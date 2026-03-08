"""NoFluffJobs scraper."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.providers.searchable_text import extract_searchable_text
from app.services.search.types import ScrapedOffer

NO_FLUFF_URL = "https://nofluffjobs.com/pl"
OFFERS_PER_PAGE = 20
MAX_SCRAPE_PAGES = 50
MAX_SCRAPE_PAGES_IN_MAX_MODE = 500
NO_FLUFF_CATEGORY_SLUGS: tuple[str, ...] = (
    "backend",
    "fullstack",
    "frontend",
    "mobile",
    "testing",
    "devops",
    "architecture",
    "security",
    "embedded",
    "artificial-intelligence",
    "data",
    "sys-administrator",
    "game-dev",
    "business-analyst",
    "ux",
    "other",
)

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}

ProgressHandler = Callable[[dict[str, float | int]], None]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _build_category_page_url(category_slug: str, page: int) -> str:
    base_url = f"{NO_FLUFF_URL}/{category_slug}"
    if page <= 1:
        return base_url
    return f"{base_url}?page={page}"


def _parse_json_array_at(html: str, array_start: int) -> list[Any]:
    if array_start < 0 or array_start >= len(html) or html[array_start] != "[":
        return []

    depth = 0
    in_string = False
    escaped = False

    for index in range(array_start, len(html)):
        char = html[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == "[":
            depth += 1
            continue

        if char == "]":
            depth -= 1
            if depth == 0:
                raw_array = html[array_start : index + 1]
                try:
                    parsed = json.loads(raw_array)
                except json.JSONDecodeError:
                    return []
                return parsed if isinstance(parsed, list) else []

    return []


def _extract_postings_from_html(html: str) -> list[dict[str, Any]]:
    needle = '"postings":['
    cursor = html.find(needle)
    largest_match: list[dict[str, Any]] = []

    while cursor != -1:
        array_start = html.find("[", cursor + len(needle) - 1)
        parsed = _parse_json_array_at(html, array_start)
        parsed_list = [item for item in parsed if isinstance(item, dict)]

        if len(parsed_list) > len(largest_match):
            largest_match = parsed_list

        if parsed_list and any(item.get("title") and item.get("name") for item in parsed_list):
            return parsed_list

        cursor = html.find(needle, cursor + len(needle))

    return largest_match


def _format_salary(salary: dict[str, Any] | None) -> str | None:
    if not salary:
        return None

    disclosed_at = salary.get("disclosedAt")
    if disclosed_at and disclosed_at != "VISIBLE":
        return "Undisclosed"

    currency = _clean_text(str(salary.get("currency") or "PLN"))
    contract_type = _clean_text(str(salary.get("type") or "contract"))
    salary_from = salary.get("from")
    salary_to = salary.get("to")

    def _fmt(value: int | float) -> str:
        return f"{int(value):,}".replace(",", " ")

    if isinstance(salary_from, (int, float)) and isinstance(salary_to, (int, float)):
        return f"{_fmt(salary_from)} - {_fmt(salary_to)} {currency}/{contract_type}"
    if isinstance(salary_from, (int, float)):
        return f"{_fmt(salary_from)} {currency}/{contract_type}"

    return None


def _format_location(posting: dict[str, Any]) -> str:
    places = posting.get("location", {}).get("places", [])
    labels: set[str] = set()

    for place in places:
        if not isinstance(place, dict):
            continue
        candidate = (
            place.get("city")
            or str(place.get("province") or "").replace("-", " ")
            or place.get("country", {}).get("name")
            or ""
        )
        normalized = _clean_text(str(candidate))
        if normalized:
            labels.add(normalized)

    unique = list(labels)
    if len(unique) <= 3:
        return ", ".join(unique)
    return f"{', '.join(unique[:3])} +{len(unique) - 3} more"


def _extract_skills(posting: dict[str, Any]) -> list[str]:
    values = posting.get("tiles", {}).get("values", [])
    skills: list[str] = []
    seen: set[str] = set()

    for item in values:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "requirement":
            continue
        value = _clean_text(str(item.get("value") or ""))
        if value and value not in seen:
            seen.add(value)
            skills.append(value)

    return skills


def _normalize_posting(
    posting: dict[str, Any],
    index: int,
    page: int,
    category_slug: str,
) -> ScrapedOffer:
    title = _clean_text(str(posting.get("title") or "Untitled"))
    company = _clean_text(str(posting.get("name") or "Unknown company"))
    location = _format_location(posting)
    skills = _extract_skills(posting)
    salary = _format_salary(posting.get("salary"))
    slug = _clean_text(str(posting.get("url") or ""))
    url = f"https://nofluffjobs.com/pl/job/{slug}" if slug else "https://nofluffjobs.com/pl"
    offer_id = _clean_text(
        str(posting.get("id") or f"nofluff-{category_slug}-{page}-{index}")
    )

    searchable_text = extract_searchable_text(posting)

    return ScrapedOffer(
        id=offer_id,
        source="nofluffjobs",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=url,
        skills=skills,
        searchable_text=searchable_text,
    )


def _interleave_category_offers(
    offers_by_category: dict[str, list[ScrapedOffer]],
) -> list[ScrapedOffer]:
    interleaved: list[ScrapedOffer] = []
    longest_bucket = max((len(bucket) for bucket in offers_by_category.values()), default=0)

    for item_index in range(longest_bucket):
        for category_slug in NO_FLUFF_CATEGORY_SLUGS:
            bucket = offers_by_category[category_slug]
            if item_index < len(bucket):
                interleaved.append(bucket[item_index])

    return interleaved


async def scrape_nofluffjobs(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
) -> list[ScrapedOffer]:
    """Scrape NoFluffJobs offers."""
    offers_by_category: dict[str, list[ScrapedOffer]] = {
        category_slug: [] for category_slug in NO_FLUFF_CATEGORY_SLUGS
    }
    seen_offer_keys: set[str] = set()
    desired_offer_budget = (
        OFFERS_PER_PAGE * MAX_SCRAPE_PAGES_IN_MAX_MODE
        if target_count is None
        else max(target_count, 1)
    )
    round_limit = min(
        max(
            math.ceil(
                desired_offer_budget / (OFFERS_PER_PAGE * len(NO_FLUFF_CATEGORY_SLUGS))
            ),
            1,
        ),
        MAX_SCRAPE_PAGES_IN_MAX_MODE if target_count is None else MAX_SCRAPE_PAGES,
    )
    total_requests = round_limit * len(NO_FLUFF_CATEGORY_SLUGS)
    processed_requests = 0

    for page in range(1, round_limit + 1):
        for category_slug in NO_FLUFF_CATEGORY_SLUGS:
            processed_requests += 1
            response = await fetch_with_timeout(
                _build_category_page_url(category_slug, page),
                headers=REQUEST_HEADERS,
            )
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(
                    f"NoFluffJobs request failed with status {response.status}"
                )

            postings = _extract_postings_from_html(response.text)
            for index, posting in enumerate(postings):
                normalized = _normalize_posting(posting, index, page, category_slug)
                key = normalized.url or normalized.id
                if key in seen_offer_keys:
                    continue
                seen_offer_keys.add(key)
                offers_by_category[category_slug].append(normalized)

            total_offers = sum(len(bucket) for bucket in offers_by_category.values())

            if on_progress:
                progress = (
                    min(processed_requests / max(total_requests, 1), 1.0)
                    if target_count is None
                    else min(total_offers / max(target_count, 1), 1.0)
                )
                on_progress({"collected": total_offers, "progress": progress})

        total_offers = sum(len(bucket) for bucket in offers_by_category.values())
        if target_count is not None and total_offers >= target_count:
            break

    interleaved_offers = _interleave_category_offers(offers_by_category)
    result = interleaved_offers if target_count is None else interleaved_offers[:target_count]

    if on_progress:
        on_progress({"collected": len(result), "progress": 1.0})

    return result
