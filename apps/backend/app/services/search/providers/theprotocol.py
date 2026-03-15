"""theprotocol.it scraper."""

from __future__ import annotations

import asyncio
import json
import math
import re
from typing import Any, Callable
from urllib.parse import unquote

from app.services.search.fetch_with_timeout import fetch_with_timeout
from app.services.search.providers.searchable_text import extract_searchable_text
from app.services.search.types import ScrapedOffer

THE_PROTOCOL_BASE_URL = "https://theprotocol.it"
THE_PROTOCOL_API_BASE_URL = "https://apus-api.theprotocol.it"
PAGE_SIZE = 50

REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "accept": "application/json, text/plain, */*",
    "x-application-name": "theprotocol-offers",
    "x-application-version": "4.4.1280",
    "referer": f"{THE_PROTOCOL_BASE_URL}/praca",
    "origin": THE_PROTOCOL_BASE_URL,
}

SEARCH_BODY = {
    "typesOfContractIds": [],
    "positionLevelIds": [],
    "cities": [],
    "workModeCodes": [],
    "onlyWithProjectDescription": False,
    "expectedTechnologies": [],
    "niceToHaveTechnologies": [],
    "excludedTechnologies": [],
    "regionsOfWorld": [],
    "keywords": [],
    "specializationsCodes": [],
    "isSupportingUkraine": False,
    "fromExternalLocations": True,
}

ProgressHandler = Callable[[dict[str, float | int]], None]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _format_salary_piece(salary: dict[str, Any] | None) -> str | None:
    if not salary:
        return None

    currency = _clean_text(str(salary.get("currencySymbol") or salary.get("currency") or "PLN"))
    time_unit = salary.get("timeUnit") or {}
    if not isinstance(time_unit, dict):
        time_unit = {}
    unit = _clean_text(str(time_unit.get("shortForm") or time_unit.get("longForm") or "month"))
    kind = _clean_text(str(salary.get("kindName") or ""))
    salary_from = salary.get("from")
    salary_to = salary.get("to")

    def _fmt(value: int | float) -> str:
        return f"{int(value):,}".replace(",", " ")

    amount = ""
    if isinstance(salary_from, (int, float)) and isinstance(salary_to, (int, float)):
        amount = f"{_fmt(salary_from)} - {_fmt(salary_to)}"
    elif isinstance(salary_from, (int, float)):
        amount = _fmt(salary_from)
    elif isinstance(salary_to, (int, float)):
        amount = _fmt(salary_to)

    if not amount:
        return None

    base = f"{amount} {currency}/{unit}"
    return f"{base} {kind}".strip()


def _format_salary(offer: dict[str, Any]) -> str | None:
    contract_salaries: list[str] = []
    seen: set[str] = set()
    for contract in offer.get("typesOfContracts") or []:
        if not isinstance(contract, dict):
            continue
        piece = _format_salary_piece(contract.get("salary"))
        if piece and piece not in seen:
            seen.add(piece)
            contract_salaries.append(piece)

    if contract_salaries:
        return " | ".join(contract_salaries)

    salary = offer.get("salary")
    if not isinstance(salary, dict):
        return None
    return _format_salary_piece(
        {
            "from": salary.get("from"),
            "to": salary.get("to"),
            "currency": salary.get("currency"),
            "timeUnit": salary.get("timeUnit"),
        }
    )


def _format_location(offer: dict[str, Any]) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for place in offer.get("workplace") or []:
        if not isinstance(place, dict):
            continue
        label = _clean_text(
            str(place.get("location") or place.get("city") or place.get("region") or "")
        )
        if label and label not in seen:
            seen.add(label)
            values.append(label)
    return ", ".join(values)


def _normalize_offer(offer: dict[str, Any], index: int) -> ScrapedOffer:
    offer_id = _clean_text(str(offer.get("id") or f"theprotocol-{index}"))
    title = _clean_text(str(offer.get("title") or "Untitled"))
    company = _clean_text(str(offer.get("employer") or "Unknown company"))
    location = _format_location(offer)
    salary = _format_salary(offer)

    skills: list[str] = []
    seen_skills: set[str] = set()
    for skill in offer.get("technologies") or []:
        normalized = _clean_text(str(skill or ""))
        if normalized and normalized not in seen_skills:
            seen_skills.add(normalized)
            skills.append(normalized)

    url_name = _clean_text(str(offer.get("offerUrlName") or ""))
    url = f"{THE_PROTOCOL_BASE_URL}/praca/{url_name}" if url_name else f"{THE_PROTOCOL_BASE_URL}/praca"

    about_project = offer.get("aboutProject") or []
    project_chunks = []
    for chunk in about_project:
        normalized = _clean_text(str(chunk or ""))
        if normalized:
            project_chunks.append(normalized)

    searchable_text = extract_searchable_text(offer, project_chunks)

    return ScrapedOffer(
        id=offer_id,
        source="theprotocol",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=url,
        skills=skills,
        searchable_text=searchable_text,
    )


def _read_cookie(set_cookie_header: str, cookie_name: str) -> str | None:
    escaped = re.escape(cookie_name)
    match = re.search(rf"{escaped}=([^;]+)", set_cookie_header)
    return match.group(1) if match else None


async def _create_session() -> tuple[str, str]:
    response = await fetch_with_timeout(
        f"{THE_PROTOCOL_API_BASE_URL}/csrf-token",
        headers=REQUEST_HEADERS,
    )
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(f"theprotocol csrf request failed with status {response.status}")

    set_cookie_header = "; ".join(response.set_cookie_headers) or response.headers.get("set-cookie", "")
    xsrf_cookie = _read_cookie(set_cookie_header, "XSRF-TOKEN")
    anti_forgery_cookie = _read_cookie(set_cookie_header, "_stemantiforgery")
    if not xsrf_cookie:
        raise RuntimeError("theprotocol csrf token cookie missing")

    cookie_parts = [f"XSRF-TOKEN={xsrf_cookie}"]
    if anti_forgery_cookie:
        cookie_parts.append(f"_stemantiforgery={anti_forgery_cookie}")

    return unquote(xsrf_cookie), "; ".join(cookie_parts)


async def _fetch_search_page(page_number: int, xsrf_token: str, cookie: str) -> dict[str, Any]:
    url = (
        f"{THE_PROTOCOL_API_BASE_URL}/offers/_search"
        f"?pageNumber={page_number}&orderby.field=Relevance&pageSize={PAGE_SIZE}"
    )
    response = await fetch_with_timeout(
        url,
        method="POST",
        headers={
            **REQUEST_HEADERS,
            "content-type": "application/json",
            "x-xsrf-token": xsrf_token,
            "cookie": cookie,
        },
        body=json.dumps(SEARCH_BODY).encode("utf-8"),
    )
    if response.status < 200 or response.status >= 300:
        raise RuntimeError(
            f"theprotocol search request failed with status {response.status} for page {page_number}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("theprotocol search payload is not an object")
    return payload


async def scrape_theprotocol(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
) -> list[ScrapedOffer]:
    """Scrape theprotocol.it offers."""
    offers: list[ScrapedOffer] = []
    try:
        xsrf_token, cookie = await _create_session()
        pages_processed = 0

        first_page = await _fetch_search_page(1, xsrf_token, cookie)
        page_meta = first_page.get("page") or {}
        total_pages = max(int(page_meta.get("count") or 1), 1)
        page_limit = (
            total_pages
            if target_count is None
            else min(max(math.ceil(target_count / PAGE_SIZE), 1), total_pages)
        )

        def _process_items(items: Any, page_number: int) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if target_count is not None and len(offers) >= target_count:
                    break
                if not isinstance(item, dict):
                    continue
                offers.append(_normalize_offer(item, len(offers) + page_number * PAGE_SIZE))

        _process_items(first_page.get("offers"), 1)
        pages_processed = 1
        if on_progress:
            progress = (
                min(pages_processed / max(page_limit, 1), 1.0)
                if target_count is None
                else min(len(offers) / max(target_count, 1), 1.0)
            )
            on_progress({"collected": len(offers), "progress": progress})

        for page_number in range(2, page_limit + 1):
            if target_count is not None and len(offers) >= target_count:
                break
            payload = await _fetch_search_page(page_number, xsrf_token, cookie)
            _process_items(payload.get("offers"), page_number)
            pages_processed = page_number

            if on_progress:
                progress = (
                    min(pages_processed / max(page_limit, 1), 1.0)
                    if target_count is None
                    else min(len(offers) / max(target_count, 1), 1.0)
                )
                on_progress({"collected": len(offers), "progress": progress})
    except asyncio.CancelledError:
        return offers if target_count is None else offers[:target_count]

    result = offers if target_count is None else offers[:target_count]
    if on_progress:
        on_progress({"collected": len(result), "progress": 1.0})
    return result
