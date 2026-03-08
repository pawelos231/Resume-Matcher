"""Generate a tailor-ready job description from a scraped offer."""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.llm import complete
from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.types import OfferSource

logger = logging.getLogger(__name__)

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}

PAGE_FETCH_TIMEOUT_MS = 20_000
MAX_SOURCE_TEXT_CHARS = 20_000
MAX_PROMPT_SOURCE_CHARS = 14_000
MIN_VALID_LLM_OUTPUT_CHARS = 120
MAX_FALLBACK_EXCERPT_CHARS = 2_500


SOURCE_LABELS: dict[OfferSource, str] = {
    "nofluffjobs": "NoFluffJobs",
    "justjoinit": "JustJoinIT",
    "bulldogjob": "Bulldogjob",
    "theprotocol": "theprotocol.it",
    "solidjobs": "Solid.jobs",
    "pracujpl": "Pracuj.pl",
}


@dataclass(slots=True)
class OfferJobDescriptionInput:
    """Input payload used to generate a tailor-ready job description."""

    source: OfferSource
    title: str
    company: str
    location: str
    salary: str | None
    url: str
    skills: list[str]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_multiline_text(value: str) -> str:
    lines: list[str] = []
    for raw_line in value.splitlines():
        cleaned = _clean_text(raw_line)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _validate_offer_url(url: str) -> str:
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Offer URL must be an absolute http/https URL.")
    return normalized


def _strip_html_to_text(raw_html: str) -> str:
    without_noise = re.sub(
        r"<(script|style|noscript|svg|iframe)[^>]*>[\s\S]*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE,
    )
    with_newlines = re.sub(
        r"</?(br|p|div|section|article|header|footer|li|ul|ol|tr|td|h1|h2|h3|h4|h5|h6)[^>]*>",
        "\n",
        without_noise,
        flags=re.IGNORECASE,
    )
    without_tags = re.sub(r"<[^>]+>", " ", with_newlines)
    decoded = html.unescape(without_tags)

    unique_lines: list[str] = []
    seen: set[str] = set()
    collected_chars = 0

    for raw_line in decoded.splitlines():
        line = _clean_text(raw_line)
        if len(line) < 2:
            continue
        marker = line.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        unique_lines.append(line)
        collected_chars += len(line) + 1
        if collected_chars >= MAX_SOURCE_TEXT_CHARS:
            break

    return "\n".join(unique_lines)[:MAX_SOURCE_TEXT_CHARS]


async def _fetch_offer_source_text(url: str) -> str:
    response = await fetch_with_timeout(
        url,
        headers=REQUEST_HEADERS,
        timeout_ms=PAGE_FETCH_TIMEOUT_MS,
    )
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"Offer page request failed with status {response.status}")

    content_type = response.headers.get("content-type", "").lower()
    body_text = response.text

    if "html" in content_type or "<html" in body_text.lower():
        return _strip_html_to_text(body_text)

    if "json" in content_type:
        try:
            parsed = response.json()
            return _normalize_multiline_text(str(parsed))[:MAX_SOURCE_TEXT_CHARS]
        except Exception:
            return _normalize_multiline_text(body_text)[:MAX_SOURCE_TEXT_CHARS]

    return _normalize_multiline_text(body_text)[:MAX_SOURCE_TEXT_CHARS]


def _to_offer_metadata_block(offer: OfferJobDescriptionInput) -> str:
    skill_text = ", ".join(skill for skill in offer.skills if _clean_text(skill))
    chunks = [
        f"Source: {SOURCE_LABELS[offer.source]}",
        f"Title: {_clean_text(offer.title) or 'Unknown'}",
        f"Company: {_clean_text(offer.company) or 'Unknown'}",
        f"Location: {_clean_text(offer.location) or 'Not specified'}",
        f"Salary: {_clean_text(offer.salary or '') or 'Not specified'}",
        f"Skills listed in scraped summary: {skill_text or 'Not specified'}",
        f"Offer URL: {offer.url}",
    ]
    return "\n".join(chunks)


def _build_generation_prompt(
    offer: OfferJobDescriptionInput,
    extracted_offer_text: str,
) -> str:
    source_excerpt = extracted_offer_text[:MAX_PROMPT_SOURCE_CHARS]
    return (
        "Create a concise, resume-tailoring-ready job description based only on provided data.\n"
        "Do not invent facts.\n"
        "Use the same language as the source content.\n"
        "Output plain text only (no markdown).\n"
        "Output exactly 6 or 7 complete sentences.\n"
        "Do not use bullet points, numbering, section headers, or list formatting.\n"
        "Write it as one short coherent description of the role, responsibilities, skills, stack, and work conditions.\n"
        "If data is missing, mention that it is not specified within a sentence.\n\n"
        "=== Scraped offer summary ===\n"
        f"{_to_offer_metadata_block(offer)}\n\n"
        "=== Extracted offer page text ===\n"
        f"{source_excerpt or 'No page text available.'}\n"
    )


def _build_fallback_job_description(
    offer: OfferJobDescriptionInput,
    extracted_offer_text: str,
) -> str:
    skill_text = ", ".join(skill for skill in offer.skills if _clean_text(skill))
    excerpt = _clean_text(extracted_offer_text[:MAX_FALLBACK_EXCERPT_CHARS])
    location = _clean_text(offer.location) or "not specified"
    salary = _clean_text(offer.salary or "") or "not specified"
    source_label = SOURCE_LABELS[offer.source]

    base_sentences = [
        f"This role is {offer.title} at {offer.company} from {source_label}.",
        f"The location in the posting is {location}.",
        f"The compensation details are {salary}.",
        f"Key skills highlighted in the offer include {skill_text or 'not specified skills'}.",
        "The detailed responsibilities and hiring process are not fully specified in the parsed data.",
    ]

    if excerpt:
        base_sentences.append(
            f"Additional context from the posting suggests: {excerpt[:280]}."
        )

    base_sentences.append(
        f"For complete requirements and context, verify the original listing at {offer.url}."
    )

    return " ".join(base_sentences[:7]).strip()


def _split_into_sentences(text: str) -> list[str]:
    compact = _clean_text(text)
    if not compact:
        return []

    compact = re.sub(
        r"^here is [^:]{0,120}:\s*",
        "",
        compact,
        flags=re.IGNORECASE,
    )
    compact = re.sub(r"\s*[-*]\s+", " ", compact)

    raw_sentences = re.split(r"(?<=[.!?])\s+", compact)
    sentences: list[str] = []
    for raw in raw_sentences:
        sentence = _clean_text(raw)
        if len(sentence) < 4:
            continue
        if sentence[-1] not in ".!?":
            sentence = f"{sentence}."
        sentences.append(sentence)
    return sentences


def _normalize_generated_job_description(
    generated_text: str,
    offer: OfferJobDescriptionInput,
    extracted_offer_text: str,
) -> str:
    generated_sentences = _split_into_sentences(generated_text)
    fallback_sentences = _split_into_sentences(
        _build_fallback_job_description(offer, extracted_offer_text)
    )

    if not generated_sentences:
        return " ".join(fallback_sentences[:6])

    if len(generated_sentences) > 7:
        generated_sentences = generated_sentences[:7]

    if len(generated_sentences) < 6:
        existing = {sentence.casefold() for sentence in generated_sentences}
        for sentence in fallback_sentences:
            if sentence.casefold() in existing:
                continue
            generated_sentences.append(sentence)
            existing.add(sentence.casefold())
            if len(generated_sentences) >= 6:
                break

    if len(generated_sentences) > 7:
        generated_sentences = generated_sentences[:7]

    return " ".join(generated_sentences).strip()


async def generate_job_description_from_offer(
    offer: OfferJobDescriptionInput,
) -> dict[str, str | int | bool]:
    """Extract offer page content and generate a final job description via LLM."""
    normalized_url = _validate_offer_url(offer.url)
    normalized_offer = OfferJobDescriptionInput(
        source=offer.source,
        title=offer.title,
        company=offer.company,
        location=offer.location,
        salary=offer.salary,
        url=normalized_url,
        skills=offer.skills,
    )

    extracted_offer_text = ""
    try:
        extracted_offer_text = await _fetch_offer_source_text(normalized_url)
    except Exception as exc:
        logger.warning(
            "Failed to fetch offer page text for generation. url=%s error=%s",
            normalized_url,
            exc,
        )

    prompt = _build_generation_prompt(normalized_offer, extracted_offer_text)

    try:
        generated = await complete(
            prompt=prompt,
            system_prompt=(
                "You are an expert recruitment analyst preparing job descriptions "
                "for CV tailoring."
            ),
            max_tokens=2048,
            temperature=0.2,
        )
        normalized_output = _normalize_generated_job_description(
            generated,
            normalized_offer,
            extracted_offer_text,
        )
        if len(normalized_output) < MIN_VALID_LLM_OUTPUT_CHARS:
            raise ValueError("LLM output too short")
        return {
            "jobDescription": normalized_output,
            "sourceTextLength": len(extracted_offer_text),
            "usedLlm": True,
        }
    except Exception as exc:
        logger.exception(
            "LLM job description generation failed; returning fallback. url=%s error=%s",
            normalized_url,
            exc,
        )
        return {
            "jobDescription": _build_fallback_job_description(
                normalized_offer,
                extracted_offer_text,
            ),
            "sourceTextLength": len(extracted_offer_text),
            "usedLlm": False,
        }
