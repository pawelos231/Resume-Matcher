"""Company website crawling and LLM-backed extraction for search offers."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urldefrag, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from app.llm import complete_json, embed_texts
from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.types import OfferSource

logger = logging.getLogger(__name__)

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}

SEARCH_ENGINE_URL = "https://html.duckduckgo.com/html/"
OFFER_PAGE_TIMEOUT_MS = 15_000
SEARCH_TIMEOUT_MS = 12_000
CRAWL_PAGE_TIMEOUT_MS = 15_000
MAX_CRAWL_PAGES = 6
MAX_CRAWL_DEPTH = 2
MAX_LINKS_PER_PAGE = 12
CHUNK_MAX_TOKENS = 750
CHUNK_OVERLAP_TOKENS = 100
MAX_EMBEDDING_CHUNKS = 64
TOP_K_CHUNKS = 6
MAX_FRAGMENT_CHARS = 1_600
MAX_SUMMARY_EVIDENCE = 4

DEFAULT_COMPANY_INFO_QUESTION = (
    "Summarize what this company does, what products or services it offers, "
    "what technology or business signals are visible, and what context is most "
    "useful for a candidate evaluating this employer."
)

COMMON_SECOND_LEVEL_SUFFIXES = {
    "co.uk",
    "org.uk",
    "gov.uk",
    "ac.uk",
    "com.au",
    "net.au",
    "org.au",
    "co.jp",
    "com.br",
    "com.pl",
}

NOISE_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
    "form",
    "button",
    "dialog",
    "aside",
    "footer",
    "nav",
}

NOISE_KEYWORDS = {
    "nav",
    "footer",
    "cookie",
    "consent",
    "tracking",
    "analytics",
    "advert",
    "promo",
    "banner",
    "popup",
    "newsletter",
    "subscribe",
    "breadcrumb",
    "share",
    "social",
    "related",
}

SKIP_CRAWL_PATH_KEYWORDS = {
    "privacy",
    "terms",
    "cookies",
    "legal",
    "login",
    "signup",
    "sign-in",
    "register",
    "cart",
}

PRIORITIZED_LINK_KEYWORDS = (
    "about",
    "company",
    "team",
    "mission",
    "values",
    "product",
    "products",
    "platform",
    "services",
    "solutions",
    "technology",
    "engineering",
    "careers",
    "jobs",
    "join",
    "who-we-are",
)

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "their",
    "about",
    "what",
    "when",
    "where",
    "which",
    "have",
    "will",
    "more",
    "most",
    "than",
    "also",
    "only",
    "using",
    "used",
    "offer",
    "offers",
    "page",
    "company",
}

EXCLUDED_OFFICIAL_SITE_HOSTS = {
    "schema.org",
    "duckduckgo.com",
    "google.com",
    "bing.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "glassdoor.com",
    "indeed.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "teamtailor.com",
    "smartrecruiters.com",
    "workable.com",
    "recruitee.com",
    "jobvite.com",
    "bamboohr.com",
    "applytojob.com",
    "nofluffjobs.com",
    "justjoin.it",
    "rocketjobs.pl",
    "bulldogjob.com",
    "theprotocol.it",
    "solid.jobs",
    "pracuj.pl",
    "olx.pl",
    "ziprecruiter.com",
    "ziprecruiter.ie",
    "careerbuilder.com",
}

JSON_LD_URL_KEYS = {"url", "sameAs", "sameas", "website", "homepage"}
JSON_LD_NESTED_KEYS = {
    "hiringOrganization",
    "organization",
    "employer",
    "worksFor",
    "provider",
    "company",
    "publisher",
    "brand",
}


@dataclass(slots=True)
class CompanyCrawlerInput:
    """Payload required to crawl and summarize company information."""

    source: OfferSource
    title: str
    company: str
    location: str
    salary: str | None
    url: str
    skills: list[str]
    question: str | None = None


@dataclass(slots=True)
class LinkCandidate:
    """Internal link candidate discovered during crawl."""

    url: str
    text: str = ""


@dataclass(slots=True)
class StructuredSection:
    """Semantic section extracted from cleaned HTML."""

    heading: str
    text: str


@dataclass(slots=True)
class StructuredDocument:
    """Cleaned and structured document used for chunking and retrieval."""

    url: str
    title: str
    sections: list[StructuredSection] = field(default_factory=list)
    links: list[LinkCandidate] = field(default_factory=list)


@dataclass(slots=True)
class TextChunk:
    """Chunked fragment that participates in retrieval."""

    url: str
    title: str
    heading: str
    text: str


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_multiline_text(value: str) -> str:
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = _clean_text(raw_line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _normalize_url(url: str, base_url: str | None = None) -> str | None:
    candidate = url.strip()
    if not candidate or candidate.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None

    if base_url:
        candidate = urljoin(base_url, candidate)

    normalized, _ = urldefrag(candidate)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            "",
            "",
        )
    )


def _validate_absolute_http_url(url: str) -> str:
    normalized = _normalize_url(url)
    if normalized is None:
        raise ValueError("Offer URL must be an absolute http/https URL.")
    return normalized


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _get_registrable_host(hostname: str) -> str:
    parts = hostname.lower().split(".")
    if len(parts) <= 2:
        return hostname.lower()

    suffix = ".".join(parts[-2:])
    if suffix in COMMON_SECOND_LEVEL_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _host_matches_allowed(url: str, allowed_host: str) -> bool:
    hostname = _hostname(url)
    if not hostname:
        return False
    registrable = _get_registrable_host(hostname)
    return registrable == allowed_host


def _is_excluded_official_host(url: str) -> bool:
    hostname = _hostname(url)
    if not hostname:
        return True

    for blocked in EXCLUDED_OFFICIAL_SITE_HOSTS:
        if hostname == blocked or hostname.endswith(f".{blocked}"):
            return True
    return False


def _company_domain_tokens(company_name: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", company_name.lower())
        if len(token) >= 3 and token not in STOPWORDS
    }


def _score_site_candidate(
    url: str,
    company_name: str,
    *,
    label: str = "",
    offer_host: str = "",
) -> float:
    if _is_excluded_official_host(url):
        return -100.0

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return -100.0

    score = 0.0
    if parsed.scheme == "https":
        score += 1.0
    if hostname == offer_host or hostname.endswith(f".{offer_host}"):
        score -= 4.0

    registrable = _get_registrable_host(hostname)
    host_tokens = set(re.findall(r"[a-z0-9]+", registrable.replace("-", " ")))
    company_tokens = _company_domain_tokens(company_name)
    score += float(len(company_tokens & host_tokens) * 5)

    combined_label = f"{label} {parsed.path}".lower()
    if any(token in combined_label for token in company_tokens):
        score += 2.0

    if parsed.path in {"", "/"}:
        score += 2.0
    if "about" in parsed.path.lower():
        score += 0.5
    if any(keyword in hostname for keyword in ("careers", "jobs", "apply")):
        score -= 1.0

    return score


async def _fetch_html(url: str, timeout_ms: int) -> str:
    response = await fetch_with_timeout(
        url,
        headers=REQUEST_HEADERS,
        timeout_ms=timeout_ms,
    )
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"Request failed with status {response.status}")

    content_type = response.headers.get("content-type", "").lower()
    body = response.text
    if "html" not in content_type and "<html" not in body.lower():
        raise RuntimeError("Response did not look like HTML")
    return body


def _extract_urls_from_json_ld(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        normalized = _normalize_url(value)
        if normalized is not None:
            urls.append(normalized)
        return urls

    if isinstance(value, list):
        for item in value:
            urls.extend(_extract_urls_from_json_ld(item))
        return urls

    if not isinstance(value, dict):
        return urls

    for key, nested in value.items():
        if key in JSON_LD_URL_KEYS:
            urls.extend(_extract_urls_from_json_ld(nested))
        elif key in JSON_LD_NESTED_KEYS or isinstance(nested, (dict, list)):
            urls.extend(_extract_urls_from_json_ld(nested))
    return urls


def _extract_offer_page_candidates(
    html: str,
    offer_url: str,
    company_name: str,
) -> list[tuple[str, float]]:
    soup = BeautifulSoup(html, "html.parser")
    offer_host = _hostname(offer_url)
    scores: dict[str, float] = {}

    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for candidate_url in _extract_urls_from_json_ld(payload):
            score = _score_site_candidate(
                candidate_url,
                company_name,
                label="json-ld",
                offer_host=offer_host,
            )
            if score > scores.get(candidate_url, -100.0):
                scores[candidate_url] = score

    for anchor in soup.find_all("a", href=True):
        candidate_url = _normalize_url(anchor.get("href", ""), offer_url)
        if candidate_url is None:
            continue

        score = _score_site_candidate(
            candidate_url,
            company_name,
            label=_clean_text(anchor.get_text(" ", strip=True)),
            offer_host=offer_host,
        )
        if score > scores.get(candidate_url, -100.0):
            scores[candidate_url] = score

    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _extract_search_target_url(href: str) -> str | None:
    normalized = _normalize_url(href, SEARCH_ENGINE_URL)
    if normalized is None:
        return None

    parsed = urlparse(normalized)
    hostname = (parsed.hostname or "").lower()
    if hostname.endswith("duckduckgo.com"):
        raw_target = parse_qs(parsed.query).get("uddg", [None])[0]
        if raw_target:
            return _normalize_url(unquote(raw_target))

    return normalized


async def _search_company_website(offer: CompanyCrawlerInput) -> list[tuple[str, float]]:
    query = _clean_text(f"{offer.company} official site {offer.title}")
    if not query:
        return []

    search_url = f"{SEARCH_ENGINE_URL}?q={quote_plus(query)}"
    try:
        html = await _fetch_html(search_url, SEARCH_TIMEOUT_MS)
    except Exception as exc:
        logger.warning("Company website search failed. company=%s error=%s", offer.company, exc)
        return []

    soup = BeautifulSoup(html, "html.parser")
    scores: dict[str, float] = {}
    offer_host = _hostname(offer.url)

    selectors = (
        "a.result__a",
        ".result a[href]",
        ".results_links a[href]",
        "a[href]",
    )
    anchors: list[Tag] = []
    for selector in selectors:
        anchors.extend(tag for tag in soup.select(selector) if isinstance(tag, Tag))

    for anchor in anchors:
        target_url = _extract_search_target_url(anchor.get("href", ""))
        if target_url is None:
            continue
        score = _score_site_candidate(
            target_url,
            offer.company,
            label=_clean_text(anchor.get_text(" ", strip=True)),
            offer_host=offer_host,
        )
        if score > scores.get(target_url, -100.0):
            scores[target_url] = score

    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


async def _resolve_company_website(
    offer: CompanyCrawlerInput,
) -> tuple[str | None, str, str | None]:
    offer_html: str | None = None
    try:
        offer_html = await _fetch_html(offer.url, OFFER_PAGE_TIMEOUT_MS)
    except Exception as exc:
        logger.warning(
            "Offer page fetch failed during company discovery. url=%s error=%s",
            offer.url,
            exc,
        )

    if offer_html:
        offer_candidates = _extract_offer_page_candidates(offer_html, offer.url, offer.company)
        if offer_candidates and offer_candidates[0][1] >= 5.0:
            return offer_candidates[0][0], "offer_page", offer_html

    search_candidates = await _search_company_website(offer)
    if search_candidates and search_candidates[0][1] >= 4.0:
        return search_candidates[0][0], "search_engine", offer_html

    return None, "unresolved", offer_html


def _remove_noise_nodes(root: Tag) -> None:
    for tag_name in NOISE_TAGS:
        for node in root.find_all(tag_name):
            node.decompose()

    for node in list(root.find_all(True)):
        attrs = getattr(node, "attrs", None)
        if not isinstance(attrs, dict):
            continue

        class_name = " ".join(str(value) for value in attrs.get("class", []))
        marker_text = " ".join(
            [
                str(attrs.get("id", "")),
                class_name,
                str(attrs.get("role", "")),
                str(attrs.get("aria-label", "")),
                str(attrs.get("data-testid", "")),
            ]
        ).lower()
        style = str(attrs.get("style", "")).lower()
        if any(keyword in marker_text for keyword in NOISE_KEYWORDS):
            node.decompose()
            continue
        if "display:none" in style or "visibility:hidden" in style:
            node.decompose()


def _pick_content_root(soup: BeautifulSoup) -> Tag:
    for selector in ("main", "article", "[role='main']", "#content", ".content", "body"):
        candidate = soup.select_one(selector)
        if isinstance(candidate, Tag):
            return candidate
    if soup.body is not None:
        return soup.body
    return soup


def _extract_table_text(table: Tag) -> str:
    lines: list[str] = []
    for row in table.find_all("tr"):
        cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
        cells = [cell for cell in cells if cell]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _extract_links(root: Tag, base_url: str) -> list[LinkCandidate]:
    links: list[LinkCandidate] = []
    seen: set[str] = set()
    for anchor in root.find_all("a", href=True):
        normalized = _normalize_url(anchor.get("href", ""), base_url)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        links.append(
            LinkCandidate(
                url=normalized,
                text=_clean_text(anchor.get_text(" ", strip=True)),
            )
        )
    return links


def _extract_sections(root: Tag, title: str) -> list[StructuredSection]:
    sections: list[StructuredSection] = []
    current_heading = title or "Overview"
    current_parts: list[str] = []
    seen_blocks: set[str] = set()

    def flush_section() -> None:
        nonlocal current_parts
        text = _normalize_multiline_text("\n".join(current_parts))
        if text:
            sections.append(StructuredSection(heading=current_heading, text=text))
        current_parts = []

    for node in root.find_all(["h1", "h2", "h3", "p", "li", "table"], recursive=True):
        if not isinstance(node, Tag):
            continue

        if node.name in {"h1", "h2", "h3"}:
            heading = _clean_text(node.get_text(" ", strip=True))
            if heading:
                flush_section()
                current_heading = heading
            continue

        if node.name == "table":
            block_text = _extract_table_text(node)
        else:
            block_text = _clean_text(node.get_text(" ", strip=True))

        if node.name != "table" and len(block_text) < 20:
            continue

        marker = block_text.casefold()
        if marker in seen_blocks:
            continue
        seen_blocks.add(marker)
        current_parts.append(block_text)

    flush_section()

    if sections:
        return sections

    fallback_text = _normalize_multiline_text(root.get_text("\n", strip=True))
    if not fallback_text:
        return []

    return [StructuredSection(heading=title or "Overview", text=fallback_text)]


def _build_structured_document(url: str, html: str) -> StructuredDocument:
    soup = BeautifulSoup(html, "html.parser")
    title = _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    root = _pick_content_root(soup)
    _remove_noise_nodes(root)
    sections = _extract_sections(root, title)
    links = _extract_links(root, url)
    return StructuredDocument(url=url, title=title or _hostname(url), sections=sections, links=links)


def _score_internal_link(link: LinkCandidate) -> float:
    path = urlparse(link.url).path.lower()
    if any(keyword in path for keyword in SKIP_CRAWL_PATH_KEYWORDS):
        return -10.0

    score = 0.0
    if path in {"", "/"}:
        score += 4.0

    if any(keyword in path for keyword in PRIORITIZED_LINK_KEYWORDS):
        score += 3.0

    if link.text:
        lowered = link.text.lower()
        if any(keyword in lowered for keyword in PRIORITIZED_LINK_KEYWORDS):
            score += 1.0

    depth = len([fragment for fragment in path.split("/") if fragment])
    score -= float(depth) * 0.1
    return score


def _build_seed_urls(website_url: str) -> list[str]:
    parsed = urlparse(website_url)
    root_url = urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))
    if root_url == website_url:
        return [website_url]
    return [website_url, root_url]


async def _crawl_company_site(seed_urls: list[str]) -> list[StructuredDocument]:
    if not seed_urls:
        return []

    allowed_host = _get_registrable_host(_hostname(seed_urls[0]))
    queue: list[tuple[str, int]] = [(seed_url, 0) for seed_url in seed_urls]
    queued = {seed_url for seed_url in seed_urls}
    visited: set[str] = set()
    documents: list[StructuredDocument] = []

    while queue and len(documents) < MAX_CRAWL_PAGES:
        current_url, depth = queue.pop(0)
        queued.discard(current_url)
        if current_url in visited:
            continue
        visited.add(current_url)

        if not _host_matches_allowed(current_url, allowed_host):
            continue

        try:
            html = await _fetch_html(current_url, CRAWL_PAGE_TIMEOUT_MS)
        except Exception as exc:
            logger.debug("Skipping crawled page after fetch failure. url=%s error=%s", current_url, exc)
            continue

        document = _build_structured_document(current_url, html)
        if document.sections:
            total_text = sum(len(section.text) for section in document.sections)
            if total_text >= 80:
                documents.append(document)

        if depth >= MAX_CRAWL_DEPTH:
            continue

        ranked_links = sorted(document.links, key=_score_internal_link, reverse=True)
        for link in ranked_links[:MAX_LINKS_PER_PAGE]:
            if link.url in visited or link.url in queued:
                continue
            if not _host_matches_allowed(link.url, allowed_host):
                continue
            if _score_internal_link(link) < -5:
                continue
            queue.append((link.url, depth + 1))
            queued.add(link.url)

    return documents


def _chunk_text(text: str) -> list[str]:
    tokens = re.findall(r"\S+", text)
    if not tokens:
        return []
    if len(tokens) <= CHUNK_MAX_TOKENS:
        return [" ".join(tokens)]

    chunks: list[str] = []
    step = max(1, CHUNK_MAX_TOKENS - CHUNK_OVERLAP_TOKENS)
    start = 0
    while start < len(tokens):
        end = min(len(tokens), start + CHUNK_MAX_TOKENS)
        chunk = " ".join(tokens[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(tokens):
            break
        start += step
    return chunks


def _chunk_documents(documents: list[StructuredDocument]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for document in documents:
        for section in document.sections:
            for chunk in _chunk_text(section.text):
                chunks.append(
                    TextChunk(
                        url=document.url,
                        title=document.title,
                        heading=section.heading,
                        text=chunk,
                    )
                )
    return chunks


def _normalize_terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in STOPWORDS
    }


def _lexical_score(
    chunk: TextChunk,
    query_terms: set[str],
    company_terms: set[str],
    skill_terms: set[str],
) -> float:
    haystack = f"{chunk.title} {chunk.heading} {chunk.text}".lower()
    haystack_terms = _normalize_terms(haystack)
    if not haystack_terms:
        return 0.0

    overlap = len(query_terms & haystack_terms)
    company_overlap = len(company_terms & haystack_terms)
    skill_overlap = len(skill_terms & haystack_terms)
    return (
        overlap / max(len(query_terms), 1)
        + company_overlap * 0.15
        + skill_overlap * 0.1
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0

    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


async def _retrieve_relevant_chunks(
    chunks: list[TextChunk],
    offer: CompanyCrawlerInput,
    question: str,
) -> tuple[list[TextChunk], str]:
    if not chunks:
        return [], "lexical"

    query_text = _clean_text(
        " ".join(
            [
                question,
                offer.company,
                offer.title,
                offer.location,
                " ".join(offer.skills[:12]),
            ]
        )
    )
    query_terms = _normalize_terms(query_text)
    company_terms = _company_domain_tokens(offer.company)
    skill_terms = _normalize_terms(" ".join(offer.skills))

    lexical_ranked = [
        (chunk, _lexical_score(chunk, query_terms, company_terms, skill_terms))
        for chunk in chunks
    ]
    lexical_ranked.sort(key=lambda item: item[1], reverse=True)

    embedding_candidates = lexical_ranked[:MAX_EMBEDDING_CHUNKS]
    candidate_chunks = [chunk for chunk, _ in embedding_candidates]
    candidate_scores = [score for _, score in embedding_candidates]

    embeddings = await embed_texts(
        [query_text, *[f"{chunk.title}\n{chunk.heading}\n{chunk.text}" for chunk in candidate_chunks]]
    )
    if embeddings is None or len(embeddings) != len(candidate_chunks) + 1:
        return [chunk for chunk, _ in lexical_ranked[:TOP_K_CHUNKS]], "lexical"

    query_vector = embeddings[0]
    max_lexical = max(candidate_scores) if candidate_scores else 0.0
    ranked_with_embeddings: list[tuple[TextChunk, float]] = []
    for index, chunk in enumerate(candidate_chunks):
        cosine = _cosine_similarity(query_vector, embeddings[index + 1])
        lexical_normalized = (
            candidate_scores[index] / max_lexical if max_lexical > 0 else 0.0
        )
        ranked_with_embeddings.append((chunk, cosine * 0.75 + lexical_normalized * 0.25))

    ranked_with_embeddings.sort(key=lambda item: item[1], reverse=True)
    return [chunk for chunk, _ in ranked_with_embeddings[:TOP_K_CHUNKS]], "embedding"


def _fallback_summary_from_chunks(chunks: list[TextChunk]) -> tuple[str, list[str]]:
    sentences: list[str] = []
    highlights: list[str] = []
    seen_sentences: set[str] = set()

    for chunk in chunks:
        for raw_sentence in re.split(r"(?<=[.!?])\s+", _clean_text(chunk.text)):
            sentence = _clean_text(raw_sentence)
            if len(sentence) < 35:
                continue
            marker = sentence.casefold()
            if marker in seen_sentences:
                continue
            seen_sentences.add(marker)
            sentences.append(sentence if sentence[-1] in ".!?" else f"{sentence}.")
            if len(sentences) >= 4:
                break
        highlight = _clean_text(chunk.heading)
        if highlight and highlight not in highlights:
            highlights.append(highlight)
        if len(sentences) >= 4 and len(highlights) >= 3:
            break

    summary = " ".join(sentences[:4]).strip()
    if not summary:
        summary = "Could not extract a concise company summary from the crawled pages."
    return summary, highlights[:5]


def _build_prompt_fragments(chunks: list[TextChunk]) -> str:
    parts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        parts.append(
            "\n".join(
                [
                    f"Fragment {index}",
                    f"URL: {chunk.url}",
                    f"Page title: {chunk.title}",
                    f"Section: {chunk.heading}",
                    f"Content: {chunk.text[:MAX_FRAGMENT_CHARS]}",
                ]
            )
        )
    return "\n\n".join(parts)


def _coerce_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = _clean_text(item)
        if not text:
            continue
        output.append(text)
        if len(output) >= limit:
            break
    return output


def _fallback_evidence(chunks: list[TextChunk]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for chunk in chunks[:MAX_SUMMARY_EVIDENCE]:
        evidence.append(
            {
                "url": chunk.url,
                "title": chunk.title,
                "snippet": _clean_text(chunk.text[:260]),
            }
        )
    return evidence


def _coerce_evidence(value: Any, fallback: list[dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return fallback

    evidence: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        url = _clean_text(str(item.get("url", "")))
        title = _clean_text(str(item.get("title", "")))
        snippet = _clean_text(str(item.get("snippet", "")))
        if not snippet:
            continue
        evidence.append(
            {
                "url": url,
                "title": title,
                "snippet": snippet,
            }
        )
        if len(evidence) >= MAX_SUMMARY_EVIDENCE:
            break

    return evidence or fallback


async def _extract_company_info_with_llm(
    offer: CompanyCrawlerInput,
    question: str,
    website_url: str | None,
    chunks: list[TextChunk],
) -> tuple[str, list[str], list[dict[str, str]], bool]:
    fallback_summary, fallback_highlights = _fallback_summary_from_chunks(chunks)
    fallback_evidence = _fallback_evidence(chunks)

    prompt = (
        "Use only the fragments below. If something is missing, say it is not found.\n"
        "Do not invent facts or company claims.\n"
        "Keep the answer useful for a candidate evaluating the company.\n"
        "Return JSON only with this shape:\n"
        "{\n"
        '  "summary": "3-5 sentence summary",\n'
        '  "highlights": ["short bullet", "short bullet"],\n'
        '  "evidence": [\n'
        '    {"url": "https://...", "title": "Page title", "snippet": "short supporting snippet"}\n'
        "  ]\n"
        "}\n\n"
        f"Company: {offer.company}\n"
        f"Offer role: {offer.title}\n"
        f"Resolved website: {website_url or 'Not found'}\n"
        f"Question: {question}\n\n"
        "Fragments:\n"
        f"{_build_prompt_fragments(chunks)}\n"
    )

    try:
        payload = await complete_json(
            prompt=prompt,
            system_prompt="You are a precise research analyst extracting company information from crawled web fragments.",
            max_tokens=2_500,
            retries=1,
            deterministic=True,
        )
        summary = _clean_text(str(payload.get("summary", ""))) or fallback_summary
        highlights = _coerce_string_list(payload.get("highlights"), limit=6) or fallback_highlights
        evidence = _coerce_evidence(payload.get("evidence"), fallback_evidence)
        return summary, highlights, evidence, True
    except Exception as exc:
        logger.warning("Company info extraction fell back to heuristic summary. error=%s", exc)
        return fallback_summary, fallback_highlights, fallback_evidence, False


async def generate_company_info_from_offer(
    offer: CompanyCrawlerInput,
) -> dict[str, Any]:
    """Resolve a company website, crawl it, and summarize relevant company info."""
    normalized_offer = CompanyCrawlerInput(
        source=offer.source,
        title=_clean_text(offer.title),
        company=_clean_text(offer.company),
        location=_clean_text(offer.location),
        salary=_clean_text(offer.salary or "") or None,
        url=_validate_absolute_http_url(offer.url),
        skills=[_clean_text(skill) for skill in offer.skills if _clean_text(skill)],
        question=_clean_text(offer.question or "") or None,
    )
    question = normalized_offer.question or DEFAULT_COMPANY_INFO_QUESTION

    website_url, website_found_via, offer_html = await _resolve_company_website(normalized_offer)

    documents: list[StructuredDocument] = []
    if website_url:
        documents = await _crawl_company_site(_build_seed_urls(website_url))

    if not documents and offer_html:
        fallback_document = _build_structured_document(normalized_offer.url, offer_html)
        if fallback_document.sections:
            documents = [fallback_document]

    chunks = _chunk_documents(documents)
    if not chunks:
        raise ValueError("Could not extract readable company content from the resolved pages.")

    relevant_chunks, retrieval_method = await _retrieve_relevant_chunks(
        chunks,
        normalized_offer,
        question,
    )
    if not relevant_chunks:
        relevant_chunks = chunks[:TOP_K_CHUNKS]

    summary, highlights, evidence, used_llm = await _extract_company_info_with_llm(
        normalized_offer,
        question,
        website_url,
        relevant_chunks,
    )

    source_pages: list[dict[str, str]] = []
    seen_pages: set[str] = set()
    for document in documents:
        if document.url in seen_pages:
            continue
        seen_pages.add(document.url)
        source_pages.append({"url": document.url, "title": document.title})

    return {
        "company": normalized_offer.company,
        "websiteUrl": website_url,
        "websiteFoundVia": website_found_via,
        "question": question,
        "summary": summary,
        "highlights": highlights,
        "sourcePages": source_pages[:MAX_CRAWL_PAGES],
        "evidence": evidence,
        "stats": {
            "pagesVisited": len(documents),
            "chunksIndexed": len(chunks),
            "relevantChunks": len(relevant_chunks),
            "retrievalMethod": retrieval_method,
            "usedLlm": used_llm,
        },
    }
