"""JustJoinIt scraper."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.providers.searchable_text import extract_searchable_text
from app.services.search.types import ScrapedOffer

JUST_JOIN_IT_URL = "https://justjoin.it"
JUST_JOIN_IT_ACTIVE_JOBS_SITEMAP = "https://justjoin.it/sitemaps/active-jobs.xml"
MAX_CONCURRENT_OFFER_REQUESTS = 8
MAX_URLS_IN_MAX_MODE = 2_000

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}

ProgressHandler = Callable[[dict[str, float | int]], None]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_loc_entries_from_xml(xml: str) -> list[str]:
    return [
        _clean_text(match.group(1))
        for match in re.finditer(r"<loc>(.*?)</loc>", xml, flags=re.IGNORECASE)
        if _clean_text(match.group(1))
    ]


def _format_unit(unit_text: str | None) -> str:
    normalized = _clean_text(unit_text or "").upper()
    if normalized == "HOUR":
        return "h"
    if normalized == "MONTH":
        return "month"
    if normalized == "YEAR":
        return "year"
    return normalized.lower() or "month"


def _format_salary(posting: dict[str, Any]) -> str | None:
    salary = posting.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    value = salary.get("value")
    if not isinstance(value, dict):
        return None

    currency = _clean_text(str(salary.get("currency") or "PLN")).upper()
    unit = _format_unit(value.get("unitText"))
    min_value = value.get("minValue")
    max_value = value.get("maxValue")
    single_value = value.get("value")

    def _fmt(number: int | float) -> str:
        return f"{int(number):,}".replace(",", " ")

    if isinstance(min_value, (int, float)) and isinstance(max_value, (int, float)):
        return f"{_fmt(min_value)} - {_fmt(max_value)} {currency}/{unit}"
    if isinstance(single_value, (int, float)):
        return f"{_fmt(single_value)} {currency}/{unit}"
    if isinstance(min_value, (int, float)):
        return f"{_fmt(min_value)} {currency}/{unit}"
    return None


def _format_location(posting: dict[str, Any]) -> str:
    address = posting.get("jobLocation", {}).get("address", {})
    if not isinstance(address, dict):
        return ""
    chunks = [
        _clean_text(str(address.get("addressLocality") or "")),
        _clean_text(str(address.get("addressRegion") or "")),
        _clean_text(str(address.get("addressCountry") or "")),
    ]
    unique: list[str] = []
    for chunk in chunks:
        if chunk and chunk not in unique:
            unique.append(chunk)
    return ", ".join(unique)


def _extract_required_skills(html: str) -> list[str]:
    match = re.search(r'\\"requiredSkills\\":\[([\s\S]*?)\],\\"niceToHaveSkills\\"', html)
    if not match:
        return []

    raw = f"[{match.group(1)}]".replace('\\"', '"')
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list):
        return []

    skills: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = _clean_text(str(item.get("name") or ""))
        if not name or name in seen:
            continue
        seen.add(name)
        skills.append(name)
        if len(skills) >= 12:
            break
    return skills


def _extract_job_posting_from_html(html: str) -> dict[str, Any] | None:
    pattern = r'<script type="application/ld\+json">([\s\S]*?)</script>'
    for match in re.finditer(pattern, html, flags=re.IGNORECASE):
        payload = match.group(1)
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    return item
        elif isinstance(parsed, dict) and parsed.get("@type") == "JobPosting":
            return parsed
    return None


def _normalize_offer(
    url: str,
    posting: dict[str, Any],
    skills: list[str],
    index: int,
) -> ScrapedOffer | None:
    title = _clean_text(str(posting.get("title") or ""))
    if not title:
        return None

    company = _clean_text(
        str(posting.get("hiringOrganization", {}).get("name") or "Unknown company")
    )
    location = _format_location(posting)
    salary = _format_salary(posting)
    path_parts = [segment for segment in url.split("/") if segment]
    offer_id = path_parts[-1].strip() if path_parts else f"justjoinit-{index}"
    description = _clean_text(str(posting.get("description") or ""))

    searchable_text = extract_searchable_text(posting, skills, description)

    return ScrapedOffer(
        id=offer_id,
        source="justjoinit",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=url,
        skills=skills,
        searchable_text=searchable_text,
    )


async def _fetch_text(url: str) -> str:
    response = await fetch_with_timeout(url, headers=REQUEST_HEADERS)
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"JustJoinIT request failed with status {response.status} for {url}")
    return response.text


async def _parse_offer_page(url: str, index: int) -> ScrapedOffer | None:
    try:
        response = await fetch_with_timeout(url, headers=REQUEST_HEADERS)
    except Exception:
        return None

    if response.status < 200 or response.status >= 300:
        return None

    html = response.text
    posting = _extract_job_posting_from_html(html)
    if not posting:
        return None

    skills = _extract_required_skills(html)
    return _normalize_offer(url, posting, skills, index)


async def scrape_justjoinit(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
) -> list[ScrapedOffer]:
    """Scrape JustJoinIt offers."""
    sitemap_index_xml = await _fetch_text(JUST_JOIN_IT_ACTIVE_JOBS_SITEMAP)
    part_urls = _parse_loc_entries_from_xml(sitemap_index_xml)
    if not part_urls:
        raise RuntimeError("JustJoinIT sitemap has no parts")

    all_urls: list[str] = []
    for part_url in part_urls:
        part_xml = await _fetch_text(part_url)
        all_urls.extend(_parse_loc_entries_from_xml(part_xml))

    deduped_urls: list[str] = []
    seen_urls: set[str] = set()
    for url in all_urls:
        if not url.startswith(f"{JUST_JOIN_IT_URL}/job-offer/"):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        deduped_urls.append(url)

    urls_to_process = (
        deduped_urls[:MAX_URLS_IN_MAX_MODE] if target_count is None else deduped_urls
    )
    total_urls = len(urls_to_process)
    offers: list[ScrapedOffer] = []
    seen_offer_urls: set[str] = set()
    processed_urls = 0

    if total_urls == 0:
        if on_progress:
            on_progress({"collected": 0, "progress": 1.0})
        return offers

    for start in range(0, len(urls_to_process), MAX_CONCURRENT_OFFER_REQUESTS):
        if target_count is not None and len(offers) >= target_count:
            break

        batch = urls_to_process[start : start + MAX_CONCURRENT_OFFER_REQUESTS]
        settled = await asyncio.gather(
            *[_parse_offer_page(url, start + index) for index, url in enumerate(batch)],
            return_exceptions=True,
        )
        processed_urls += len(batch)

        for result in settled:
            if isinstance(result, Exception) or result is None:
                continue
            if result.url in seen_offer_urls:
                continue
            seen_offer_urls.add(result.url)
            offers.append(result)
            if target_count is not None and len(offers) >= target_count:
                break

        if on_progress:
            progress = (
                min(processed_urls / max(total_urls, 1), 1.0)
                if target_count is None
                else min(len(offers) / max(target_count, 1), 1.0)
            )
            on_progress({"collected": len(offers), "progress": progress})

    result = offers if target_count is None else offers[:target_count]
    if on_progress:
        on_progress({"collected": len(result), "progress": 1.0})
    return result
