"""CareerBuilder scraper."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any, Callable
from urllib.parse import quote_plus, urlsplit, urlunsplit

from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.providers.playwright_runtime import run_playwright_scraper
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

CAREERBUILDER_SEARCH_URL = "https://www.careerbuilder.com/job-listings/search"
CAREERBUILDER_SEARCH_API_URL = (
    "https://appsapi.monster.io/jobs-svx-service/v2/monster/search-jobs/"
    "samsearch/en-US?apikey=hkp1igv13sjt7ltv5kfdhjpj"
)
DEFAULT_SEARCH_QUERY = "software engineer"
MAX_CONCURRENT_DETAIL_REQUESTS = 10
LISTING_READY_TIMEOUT_MS = 10_000
PAGE_STABILIZATION_DELAY_MS = 1_000
DETAIL_FETCH_TIMEOUT_MS = 8_000
SEARCH_API_TIMEOUT_MS = 8_000
MAX_API_RESULT_COUNT = 25

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}
SEARCH_API_HEADERS = {
    **REQUEST_HEADERS,
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json; charset=UTF-8",
}

ProgressHandler = Callable[[dict[str, float | int]], None]

_EXTRACTION_SCRIPT = r"""
() => {
  const urls = [];
  const seen = new Set();

  for (const link of document.querySelectorAll('a[href*="/job-details/"]')) {
    const href = (link.href || '').trim();
    if (!href || seen.has(href)) {
      continue;
    }
    seen.add(href);
    urls.push(href);
  }

  return urls;
}
"""


def _build_query(keywords: list[str] | None) -> str:
    query = " ".join(keyword for keyword in (keywords or []) if clean_text(keyword))
    normalized = clean_text(query)
    return normalized or DEFAULT_SEARCH_QUERY


def _build_search_url(keywords: list[str] | None) -> str:
    return f"{CAREERBUILDER_SEARCH_URL}?q={quote_plus(_build_query(keywords))}"


def _build_search_api_body(
    keywords: list[str] | None,
    page_size: int,
) -> dict[str, Any]:
    safe_page_size = max(1, min(page_size, MAX_API_RESULT_COUNT))
    return {
        "jobQuery": {
            "query": _build_query(keywords),
            # CareerBuilder's public JSON endpoint rejects an empty location payload.
            "locations": [
                {
                    "country": "us",
                    "address": "Remote",
                    "radius": {
                        "unit": "mi",
                        "value": 30,
                    },
                }
            ],
        },
        "jobAdsRequest": {
            "position": list(range(1, safe_page_size + 1)),
            "placement": {
                "channel": "WEB",
                "location": "JobSearchPage",
                "property": "careerbuilder.com",
                "type": "JOB_SEARCH",
                "view": "SPLIT",
            },
        },
        "fingerprintId": uuid.uuid4().hex,
        "offset": 0,
        "pageSize": safe_page_size,
        "includeJobs": [],
        "siteId": "careerbuilder.com",
    }


def _normalize_offer_url(url: str) -> str:
    parsed = urlsplit(clean_text(url))
    if not parsed.scheme or not parsed.netloc:
        return clean_text(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _offer_id_from_url(url: str, fallback: str) -> str:
    path = urlsplit(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    match = re.search(r"--([a-f0-9-]+)$", slug, flags=re.IGNORECASE)
    if match:
        return match.group(1)
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
        id=_offer_id_from_url(normalized_url, f"careerbuilder-{index}"),
        source="careerbuilder",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=normalized_url,
        skills=skills,
        searchable_text=searchable_text,
    )


def _normalize_offer_from_search_result(
    payload: dict[str, Any],
    index: int,
) -> ScrapedOffer | None:
    posting = payload.get("jobPosting")
    if not isinstance(posting, dict):
        return None

    title = clean_text(str(posting.get("title") or ""))
    url = clean_text(str(posting.get("url") or payload.get("canonicalUrl") or ""))
    if not title or not url:
        return None

    company = clean_text(
        str(posting.get("hiringOrganization", {}).get("name") or "Unknown company")
    )
    location = format_job_posting_location(posting.get("jobLocation"))
    salary = format_job_posting_salary(posting.get("baseSalary"))
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
    normalized_url = _normalize_offer_url(url)

    return ScrapedOffer(
        id=clean_text(str(payload.get("jobId") or "")) or _offer_id_from_url(
            normalized_url,
            f"careerbuilder-{index}",
        ),
        source="careerbuilder",
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


async def _fetch_search_api_offers(
    target_count: int,
    *,
    keywords: list[str] | None = None,
    on_progress: ProgressHandler | None = None,
) -> list[ScrapedOffer]:
    response = await fetch_with_timeout(
        CAREERBUILDER_SEARCH_API_URL,
        method="POST",
        headers={
            **SEARCH_API_HEADERS,
            "referer": _build_search_url(keywords),
        },
        body=json.dumps(_build_search_api_body(keywords, target_count)).encode("utf-8"),
        timeout_ms=SEARCH_API_TIMEOUT_MS,
    )
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"CareerBuilder request failed with status {response.status}")

    payload = response.json()
    raw_results = payload.get("jobResults")
    if not isinstance(raw_results, list):
        raise RuntimeError("CareerBuilder returned an invalid search payload")

    offers: list[ScrapedOffer] = []
    target_total = min(len(raw_results), target_count)
    for index, raw_result in enumerate(raw_results):
        if len(offers) >= target_count:
            break
        if not isinstance(raw_result, dict):
            continue
        offer = _normalize_offer_from_search_result(raw_result, index)
        if offer is None:
            continue
        offers.append(offer)
        if on_progress:
            progress = min((index + 1) / max(target_total, 1), 1.0)
            on_progress({"collected": len(offers), "progress": progress})

    if on_progress:
        on_progress({"collected": len(offers), "progress": 1.0})
    return offers[:target_count]


async def scrape_careerbuilder(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
    *,
    keywords: list[str] | None = None,
) -> list[ScrapedOffer]:
    """Scrape CareerBuilder offers for the current keyword query."""
    if target_count is not None and target_count > 0 and target_count <= MAX_API_RESULT_COUNT:
        try:
            return await _fetch_search_api_offers(
                target_count,
                keywords=keywords,
                on_progress=on_progress,
            )
        except Exception:
            # Fall back to the slower browser path if the JSON search API changes.
            pass

    async def _runner(
        context: Any,
        stop_requested: Callable[[], bool] | None,
        emit_progress: ProgressHandler | None,
    ) -> list[str]:
        _ = emit_progress
        page = await context.new_page()
        try:
            await page.goto(
                _build_search_url(keywords),
                wait_until="domcontentloaded",
                timeout=LISTING_READY_TIMEOUT_MS,
            )
            await page.wait_for_selector(
                'a[href*="/job-details/"]',
                timeout=LISTING_READY_TIMEOUT_MS,
            )
            await page.wait_for_timeout(PAGE_STABILIZATION_DELAY_MS)

            raw_urls = await page.evaluate(_EXTRACTION_SCRIPT)
            if not isinstance(raw_urls, list):
                raise RuntimeError("CareerBuilder returned an invalid listing payload")

            urls: list[str] = []
            seen: set[str] = set()
            for raw_url in raw_urls:
                if stop_requested and stop_requested():
                    break
                if not isinstance(raw_url, str):
                    continue
                normalized_url = _normalize_offer_url(raw_url)
                if not normalized_url or normalized_url in seen:
                    continue
                seen.add(normalized_url)
                urls.append(normalized_url)

            return urls
        finally:
            await page.close()

    all_urls = await run_playwright_scraper(
        "CareerBuilder",
        _runner,
        locale="en-US",
    )
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
