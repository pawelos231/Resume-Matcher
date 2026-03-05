"""Solid.jobs scraper."""

from __future__ import annotations

import re
from typing import Any, Callable

from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.types import ScrapedOffer

SOLIDJOBS_BASE_URL = "https://solid.jobs"
SOLIDJOBS_API_URL = f"{SOLIDJOBS_BASE_URL}/api/offers?sortOrder=default"
PROGRESS_BATCH_SIZE = 25

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "accept": "application/vnd.solidjobs.jobofferlist+json, application/json, text/plain, */*",
    "content-type": "application/vnd.solidjobs.jobofferlist+json; charset=UTF-8",
    "app-version": "1.1.0",
    "referer": f"{SOLIDJOBS_BASE_URL}/offers/it",
}

ProgressHandler = Callable[[dict[str, float | int]], None]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_salary_period(value: str | None) -> str:
    normalized = _clean_text(value or "").lower()
    if normalized in {"month", "miesiac", "miesiąc"}:
        return "month"
    if normalized in {"hour", "godzina"}:
        return "h"
    if normalized in {"year", "rok"}:
        return "year"
    return normalized or "month"


def _format_salary_range(range_data: dict[str, Any] | None) -> str | None:
    if not isinstance(range_data, dict):
        return None

    lower = range_data.get("lowerBound")
    upper = range_data.get("upperBound")
    has_lower = isinstance(lower, (int, float))
    has_upper = isinstance(upper, (int, float))
    if not has_lower and not has_upper:
        return None

    def _fmt(value: int | float) -> str:
        return f"{int(value):,}".replace(",", " ")

    if has_lower and has_upper:
        amount = f"{_fmt(lower)} - {_fmt(upper)}"
    elif has_lower:
        amount = _fmt(lower)
    else:
        amount = _fmt(upper)

    currency = _clean_text(str(range_data.get("currency") or "PLN"))
    period = _normalize_salary_period(str(range_data.get("salaryPeriod") or ""))
    employment_type = _clean_text(str(range_data.get("employmentType") or ""))

    base = f"{amount} {currency}/{period}"
    return f"{base} ({employment_type})" if employment_type else base


def _format_salary(offer: dict[str, Any]) -> str | None:
    parts: list[str] = []
    seen: set[str] = set()
    for key in ("salaryRange", "secondarySalaryRange"):
        formatted = _format_salary_range(offer.get(key))
        if formatted and formatted not in seen:
            seen.add(formatted)
            parts.append(formatted)
    return " | ".join(parts) if parts else None


def _format_location(offer: dict[str, Any]) -> str:
    chunks = [
        _clean_text(str(offer.get("companyCity") or "")),
        _clean_text(str(offer.get("companyAddress") or "")),
    ]
    unique: list[str] = []
    for chunk in chunks:
        if chunk and chunk not in unique:
            unique.append(chunk)
    return ", ".join(unique)


def _normalize_offer(offer: dict[str, Any], index: int) -> ScrapedOffer:
    fallback_id = f"solidjobs-{index}"
    numeric_id = str(offer.get("id") or "").strip()
    key_id = _clean_text(str(offer.get("jobOfferKey") or ""))
    offer_id = key_id or numeric_id or fallback_id

    title = _clean_text(str(offer.get("jobTitle") or "Untitled"))
    company = _clean_text(str(offer.get("companyName") or "Unknown company"))
    location = _format_location(offer)
    salary = _format_salary(offer)

    skills: list[str] = []
    seen_skills: set[str] = set()
    for group in (offer.get("requiredSkills") or [], offer.get("requiredLanguages") or []):
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            name = _clean_text(str(item.get("name") or ""))
            if name and name not in seen_skills:
                seen_skills.add(name)
                skills.append(name)

    slug = _clean_text(str(offer.get("jobOfferUrl") or ""))
    url = (
        f"{SOLIDJOBS_BASE_URL}/offer/{numeric_id}/{slug}"
        if slug and numeric_id
        else f"{SOLIDJOBS_BASE_URL}/offers/it"
    )

    searchable_text = _clean_text(
        " ".join(
            [
                title,
                company,
                location,
                salary or "",
                " ".join(skills),
                _clean_text(str(offer.get("remotePossible") or "")),
                _clean_text(str(offer.get("mainCategory") or "")),
                _clean_text(str(offer.get("subCategory") or "")),
                _clean_text(str(offer.get("experienceLevel") or "")),
                _clean_text(str(offer.get("workload") or "")),
                _clean_text(str(offer.get("division") or "")),
            ]
        )
    ).lower()

    return ScrapedOffer(
        id=offer_id,
        source="solidjobs",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=url,
        skills=skills,
        searchable_text=searchable_text,
    )


async def _fetch_offers() -> list[dict[str, Any]]:
    response = await fetch_with_timeout(SOLIDJOBS_API_URL, headers=REQUEST_HEADERS)
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"Solid.jobs request failed with status {response.status}")
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        raise RuntimeError("Solid.jobs response is not JSON (possible anti-bot response)")
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Solid.jobs response does not contain an offers array")
    return [item for item in payload if isinstance(item, dict)]


async def scrape_solidjobs(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
) -> list[ScrapedOffer]:
    """Scrape Solid.jobs offers."""
    raw_offers = await _fetch_offers()
    total_offers = len(raw_offers)
    result: list[ScrapedOffer] = []
    target = total_offers if target_count is None else min(target_count, total_offers)

    if total_offers == 0 or target == 0:
        if on_progress:
            on_progress({"collected": 0, "progress": 1.0})
        return []

    for index, offer in enumerate(raw_offers):
        if target_count is not None and len(result) >= target_count:
            break
        result.append(_normalize_offer(offer, index))

        processed_offers = index + 1
        if (
            processed_offers % PROGRESS_BATCH_SIZE == 0
            or processed_offers == total_offers
            or len(result) == target
        ):
            if on_progress:
                progress = (
                    min(processed_offers / max(total_offers, 1), 1.0)
                    if target_count is None
                    else min(len(result) / max(target_count, 1), 1.0)
                )
                on_progress({"collected": len(result), "progress": progress})

    if on_progress:
        on_progress({"collected": len(result), "progress": 1.0})
    return result

