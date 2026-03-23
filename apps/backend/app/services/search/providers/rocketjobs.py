"""RocketJobs scraper."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Callable
from urllib.parse import quote_plus, urlsplit, urlunsplit

from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.providers.schema_jobposting import (
    clean_text,
    extract_job_posting_from_html,
    format_job_posting_location,
    format_job_posting_salary,
    strip_html_fragment,
)
from app.services.search.providers.searchable_text import extract_searchable_text
from app.services.search.types import ScrapedOffer

ROCKETJOBS_SEARCH_URL = "https://rocketjobs.pl/oferty-pracy"
DEFAULT_SEARCH_QUERY = "it"
MAX_CONCURRENT_DETAIL_REQUESTS = 10
SEARCH_FETCH_TIMEOUT_MS = 8_000
DETAIL_FETCH_TIMEOUT_MS = 8_000

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}

ProgressHandler = Callable[[dict[str, float | int]], None]


def _build_query(keywords: list[str] | None) -> str:
    query = " ".join(keyword for keyword in (keywords or []) if clean_text(keyword))
    normalized = clean_text(query)
    return normalized or DEFAULT_SEARCH_QUERY


def _build_search_url(keywords: list[str] | None) -> str:
    return f"{ROCKETJOBS_SEARCH_URL}?keyword={quote_plus(_build_query(keywords))}"


def _extract_offer_urls(html_text: str) -> list[str]:
    pattern = r"<script[^>]*type=['\"]application/ld\+json['\"][^>]*>([\s\S]*?)</script>"
    urls: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(pattern, html_text, flags=re.IGNORECASE):
        raw_payload = match.group(1).strip()
        if not raw_payload:
            continue

        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue

        if not isinstance(parsed, dict) or parsed.get("@type") != "CollectionPage":
            continue

        has_part = parsed.get("hasPart")
        if not isinstance(has_part, list):
            continue

        for item in has_part:
            if not isinstance(item, dict):
                continue
            candidate = clean_text(str(item.get("url") or ""))
            if not candidate or "/oferta-pracy/" not in candidate or candidate in seen:
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
    path = urlsplit(url).path.rstrip("/")
    if not path:
        return fallback
    slug = path.split("/")[-1]
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
    description = strip_html_fragment(str(posting.get("description") or ""))
    searchable_text = extract_searchable_text(posting, description)

    return ScrapedOffer(
        id=_offer_id_from_url(normalized_url, f"rocketjobs-{index}"),
        source="rocketjobs",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=normalized_url,
        skills=[],
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


async def scrape_rocketjobs(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
    *,
    keywords: list[str] | None = None,
) -> list[ScrapedOffer]:
    """Scrape RocketJobs offers for the current keyword query."""
    response = await fetch_with_timeout(
        _build_search_url(keywords),
        headers=REQUEST_HEADERS,
        timeout_ms=SEARCH_FETCH_TIMEOUT_MS,
    )
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"RocketJobs request failed with status {response.status}")

    all_urls = _extract_offer_urls(response.text)
    urls_to_process = all_urls if target_count is None else all_urls[:target_count]
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
