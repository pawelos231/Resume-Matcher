"""Bulldogjob scraper."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable
from urllib.parse import quote

from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.types import ScrapedOffer

BULLDOGJOB_BASE_URL = "https://bulldogjob.pl"
OFFERS_PER_PAGE = 50
MAX_SCRAPE_PAGES = 500

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "accept-language": "pl-PL,pl;q=0.9,en;q=0.8",
}

ProgressHandler = Callable[[dict[str, float | int]], None]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _extract_next_data(html: str) -> dict[str, Any] | None:
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">([\s\S]*?)</script>',
        html,
    )
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    props = parsed.get("props", {})
    if not isinstance(props, dict):
        return None
    page_props = props.get("pageProps")
    return page_props if isinstance(page_props, dict) else None


def _format_salary(raw_salary: dict[str, Any] | None) -> str | None:
    if not raw_salary:
        return None
    if raw_salary.get("hidden") and not raw_salary.get("money"):
        return "Undisclosed"
    money = _clean_text(str(raw_salary.get("money") or ""))
    if not money:
        return None
    currency = _clean_text(str(raw_salary.get("currency") or ""))
    return f"{money} {currency}".strip()


def _normalize_offer(listing: dict[str, Any], index: int, page: int) -> ScrapedOffer:
    offer_id = _clean_text(str(listing.get("id") or f"bulldogjob-{page}-{index}"))
    title = _clean_text(str(listing.get("position") or "Untitled"))
    company = _clean_text(str(listing.get("company", {}).get("name") or "Unknown company"))
    location = _clean_text(str(listing.get("city") or ""))
    salary = _format_salary(listing.get("denominatedSalaryLong"))

    skills: list[str] = []
    seen_skills: set[str] = set()
    for skill in listing.get("technologyTags") or []:
        normalized = _clean_text(str(skill or ""))
        if normalized and normalized not in seen_skills:
            seen_skills.add(normalized)
            skills.append(normalized)

    external_url = _clean_text(str(listing.get("redirectTo") or ""))
    url = (
        external_url
        if external_url
        else f"{BULLDOGJOB_BASE_URL}/companies/jobs/{quote(offer_id)}"
    )

    searchable_text = _clean_text(
        " ".join([title, company, location, salary or "", " ".join(skills)])
    ).lower()

    return ScrapedOffer(
        id=offer_id,
        source="bulldogjob",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=url,
        skills=skills,
        searchable_text=searchable_text,
    )


def _get_listing_url(page: int) -> str:
    return f"{BULLDOGJOB_BASE_URL}/companies/jobs/s/page,{page}"


async def _fetch_listing_page(page: int) -> str:
    response = await fetch_with_timeout(_get_listing_url(page), headers=REQUEST_HEADERS)
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(
            f"Bulldogjob request failed with status {response.status} for page {page}"
        )
    return response.text


async def scrape_bulldogjob(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
) -> list[ScrapedOffer]:
    """Scrape Bulldogjob offers."""
    offers: list[ScrapedOffer] = []
    pages_processed = 0
    page_limit = 1
    per_page = OFFERS_PER_PAGE

    page = 1
    while page <= page_limit:
        if target_count is not None and len(offers) >= target_count:
            break

        html = await _fetch_listing_page(page)
        page_props = _extract_next_data(html)
        if not page_props:
            raise RuntimeError(f"Bulldogjob page {page} does not contain __NEXT_DATA__")

        jobs = page_props.get("jobs") or []
        if not isinstance(jobs, list):
            jobs = []

        total_count = max(int(page_props.get("totalCount") or len(jobs)), len(jobs))
        slug_state = page_props.get("slugState") or {}
        if isinstance(slug_state, dict):
            per_page = max(int(slug_state.get("perPage") or per_page), 1)

        total_pages = max(1, math.ceil(total_count / max(per_page, 1)))
        if page == 1:
            desired_pages = (
                total_pages
                if target_count is None
                else max(1, math.ceil(target_count / max(per_page, 1)))
            )
            page_limit = min(desired_pages, total_pages, MAX_SCRAPE_PAGES)

        pages_processed = page
        if not jobs:
            break

        offers.extend(_normalize_offer(item, index, page) for index, item in enumerate(jobs))

        if on_progress:
            progress = (
                min(page / max(page_limit, 1), 1.0)
                if target_count is None
                else min(len(offers) / max(target_count, 1), 1.0)
            )
            on_progress({"collected": len(offers), "progress": progress})

        if len(jobs) < per_page:
            break
        page += 1

    result = offers if target_count is None else offers[:target_count]
    if on_progress:
        on_progress({"collected": len(result), "progress": 1.0})
    return result

