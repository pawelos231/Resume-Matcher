"""ZipRecruiter scraper."""

from __future__ import annotations

import asyncio
import re
from typing import Callable
from urllib.parse import quote_plus, urlsplit, urlunsplit

from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.providers.schema_jobposting import (
    clean_text,
    extract_job_posting_from_html,
    extract_job_posting_skills,
    format_job_posting_location,
    format_job_posting_salary,
    strip_html_fragment,
)
from app.services.search.providers.searchable_text import extract_searchable_text
from app.services.search.types import ScrapedOffer

ZIPRECRUITER_BASE_URL = "https://www.ziprecruiter.ie"
ZIPRECRUITER_SEARCH_URL = f"{ZIPRECRUITER_BASE_URL}/jobs/search"
DEFAULT_SEARCH_QUERY = "software engineer"
MAX_CONCURRENT_DETAIL_REQUESTS = 10
MAX_SEARCH_PAGES = 10
SEARCH_FETCH_TIMEOUT_MS = 8_000
DETAIL_FETCH_TIMEOUT_MS = 8_000

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "accept-language": "en-US,en;q=0.9",
}

ProgressHandler = Callable[[dict[str, float | int]], None]


def _build_query(keywords: list[str] | None) -> str:
    query = " ".join(keyword.strip() for keyword in (keywords or []) if keyword.strip())
    normalized = " ".join(query.split())
    return normalized or DEFAULT_SEARCH_QUERY


def _build_search_url(keywords: list[str] | None, page: int = 1) -> str:
    query = quote_plus(_build_query(keywords))
    page_suffix = f"&page={page}" if page > 1 else ""
    return f"{ZIPRECRUITER_SEARCH_URL}?q={query}{page_suffix}"


def _extract_offer_urls(html_text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(r"""href=['"]([^'"]+)['"]""", html_text, flags=re.IGNORECASE):
        candidate = clean_text(match.group(1))
        if "/jobs/" not in candidate or "/jobs/search" in candidate:
            continue
        if candidate.startswith("/"):
            candidate = f"{ZIPRECRUITER_BASE_URL}{candidate}"
        candidate = candidate.split("?", 1)[0]
        if not re.search(r"/jobs/\d+-", candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)

    return urls


def _normalize_offer_url(url: str) -> str:
    parsed = urlsplit(clean_text(url))
    if not parsed.scheme or not parsed.netloc:
        return clean_text(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _offer_id_from_url(url: str, fallback: str) -> str:
    match = re.search(r"/jobs/(\d+)-", url)
    if match:
        return match.group(1)
    path = urlsplit(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    return clean_text(slug or fallback)


def _normalize_offer(url: str, html_text: str, index: int) -> ScrapedOffer | None:
    posting = extract_job_posting_from_html(html_text)
    if posting is None:
        return None

    title = clean_text(str(posting.get("title") or ""))
    if not title:
        return None

    company = clean_text(
        str(posting.get("hiringOrganization", {}).get("name") or "Unknown company")
    )
    location = format_job_posting_location(posting.get("jobLocation"))
    salary = format_job_posting_salary(posting.get("baseSalary"))
    normalized_url = _normalize_offer_url(url)
    skills = extract_job_posting_skills(posting.get("skills"))
    description = strip_html_fragment(str(posting.get("description") or ""))
    searchable_text = extract_searchable_text(
        title,
        company,
        location,
        salary or "",
        skills,
        description,
        posting.get("qualifications"),
        posting.get("responsibilities"),
        posting.get("jobBenefits"),
    )

    return ScrapedOffer(
        id=_offer_id_from_url(normalized_url, f"ziprecruiter-{index}"),
        source="ziprecruiter",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=normalized_url,
        skills=skills,
        searchable_text=searchable_text,
    )


async def _fetch_offer(url: str, index: int) -> ScrapedOffer | None:
    response = await fetch_with_timeout(
        url,
        headers=REQUEST_HEADERS,
        timeout_ms=DETAIL_FETCH_TIMEOUT_MS,
    )
    if response.status < 200 or response.status >= 300:
        return None
    return _normalize_offer(url, response.text, index)


def _response_is_bot_challenge(html_text: str) -> bool:
    lowered = html_text.lower()
    return (
        "just a moment" in lowered
        or "performing security verification" in lowered
        or (
            "security verification" in lowered
            and "ray id:" in lowered
            and "privacy" in lowered
        )
    )


async def scrape_ziprecruiter(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
    *,
    keywords: list[str] | None = None,
) -> list[ScrapedOffer]:
    """Scrape ZipRecruiter offers for the current keyword query."""
    listing_urls: list[str] = []
    seen_urls: set[str] = set()

    for page_number in range(1, MAX_SEARCH_PAGES + 1):
        response = await fetch_with_timeout(
            _build_search_url(keywords, page_number),
            headers=REQUEST_HEADERS,
            timeout_ms=SEARCH_FETCH_TIMEOUT_MS,
        )
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"ZipRecruiter request failed with status {response.status}")
        if _response_is_bot_challenge(response.text):
            raise RuntimeError(
                "ZipRecruiter blocked automated access with Cloudflare verification"
            )

        page_urls = _extract_offer_urls(response.text)
        fresh_urls = [url for url in page_urls if url not in seen_urls]
        if not fresh_urls:
            break

        for url in fresh_urls:
            seen_urls.add(url)
            listing_urls.append(url)

        if target_count is not None and len(listing_urls) >= target_count:
            break

    urls_to_process = listing_urls if target_count is None else listing_urls[:target_count]
    total_urls = len(urls_to_process)
    offers: list[ScrapedOffer] = []

    if total_urls == 0:
        if on_progress:
            on_progress({"collected": 0, "progress": 1.0})
        return offers

    try:
        for start in range(0, total_urls, MAX_CONCURRENT_DETAIL_REQUESTS):
            batch = urls_to_process[start : start + MAX_CONCURRENT_DETAIL_REQUESTS]
            settled = await asyncio.gather(
                *[_fetch_offer(url, start + index) for index, url in enumerate(batch)],
                return_exceptions=True,
            )

            for result in settled:
                if isinstance(result, Exception) or result is None:
                    continue
                offers.append(result)

            if on_progress:
                progress = min((start + len(batch)) / max(total_urls, 1), 1.0)
                on_progress({"collected": len(offers), "progress": progress})
    except asyncio.CancelledError:
        return offers if target_count is None else offers[:target_count]

    result = offers if target_count is None else offers[:target_count]
    if on_progress:
        on_progress({"collected": len(result), "progress": 1.0})
    return result
