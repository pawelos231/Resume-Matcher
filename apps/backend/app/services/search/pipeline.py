"""Search scraping pipeline orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import datetime as dt
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Literal

from app.services.search.providers import (
    scrape_bulldogjob,
    scrape_justjoinit,
    scrape_nofluffjobs,
    scrape_pracujpl,
    scrape_solidjobs,
    scrape_theprotocol,
)
from app.services.search.types import (
    KeywordMode,
    OfferSortBy,
    OfferSortDirection,
    OfferSource,
    PublicOffer,
    ScrapedOffer,
)

DEFAULT_LIMIT = 1000
MAX_LIMIT = 10_000
MAX_SCRAPE_LIMIT = 10_000
MIN_SOURCE_SCRAPE_TIMEOUT_S = 15
MAX_SOURCE_SCRAPE_TIMEOUT_S = 600
# Keep scrape runs responsive in UI. "max" mode can scan many pages, but should still
# fail fast enough to avoid appearing hung from the frontend perspective.
SOURCE_SCRAPE_TIMEOUT_S = 60
SOURCE_SCRAPE_TIMEOUT_IN_MAX_MODE_S = 180
SEARCH_RESULT_CACHE_TTL_S = 180
DEFAULT_KEYWORD_MODE: KeywordMode = "and"
DEFAULT_SORT_BY: OfferSortBy = "relevance"
DEFAULT_SORT_DIRECTION: OfferSortDirection = "asc"
ALL_SOURCES: list[OfferSource] = [
    "nofluffjobs",
    "justjoinit",
    "bulldogjob",
    "theprotocol",
    "solidjobs",
    "pracujpl",
]

SOURCE_LABELS: dict[OfferSource, str] = {
    "nofluffjobs": "NoFluffJobs",
    "justjoinit": "JustJoinIT",
    "bulldogjob": "Bulldogjob",
    "theprotocol": "theprotocol.it",
    "solidjobs": "Solid.jobs",
    "pracujpl": "Pracuj.pl",
}

SOURCE_LIMIT_QUERY_KEYS: dict[OfferSource, list[str]] = {
    "nofluffjobs": [
        "scrapeLimitNoFluffJobs",
        "scrapeLimitNoFluff",
        "nofluffjobsLimit",
        "nofluffLimit",
    ],
    "justjoinit": [
        "scrapeLimitJustJoinIt",
        "scrapeLimitJustJoin",
        "justjoinitLimit",
        "jjiLimit",
    ],
    "bulldogjob": [
        "scrapeLimitBulldogJob",
        "scrapeLimitBulldogjob",
        "bulldogjobLimit",
        "bulldogLimit",
    ],
    "theprotocol": [
        "scrapeLimitTheProtocol",
        "scrapeLimitTheProtocolIt",
        "theprotocolLimit",
        "protocolLimit",
    ],
    "solidjobs": [
        "scrapeLimitSolidJobs",
        "scrapeLimitSolid",
        "solidjobsLimit",
        "solidLimit",
    ],
    "pracujpl": [
        "scrapeLimitPracujPl",
        "scrapeLimitPracuj",
        "pracujplLimit",
        "pracujLimit",
    ],
}

DEFAULT_SOURCE_TARGETS: dict[OfferSource, int | None] = {
    "nofluffjobs": None,
    "justjoinit": 10,
    "bulldogjob": None,
    "theprotocol": None,
    "solidjobs": None,
    "pracujpl": 50,
}

ScrapeTargetLabel = int | Literal["max"]
ProgressHandler = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class CachedSearchResult:
    expires_at: float
    status: int
    payload: dict[str, Any]


_SEARCH_RESULT_CACHE: dict[str, CachedSearchResult] = {}
_SEARCH_RESULT_CACHE_LOCK = asyncio.Lock()


def _get_param(params: Mapping[str, str], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def parse_limit(params: Mapping[str, str]) -> int:
    raw = _get_param(params, "limit")
    try:
        parsed = int(raw) if raw is not None else DEFAULT_LIMIT
    except ValueError:
        parsed = DEFAULT_LIMIT
    if parsed <= 0:
        return DEFAULT_LIMIT
    return min(parsed, MAX_LIMIT)


def parse_source_scrape_limit(
    params: Mapping[str, str],
    source: OfferSource,
    fallback: int | None,
) -> int | None:
    raw_value: str | None = None
    for key in SOURCE_LIMIT_QUERY_KEYS[source]:
        candidate = _get_param(params, key)
        if candidate is not None:
            raw_value = candidate
            break

    if raw_value is None:
        return fallback

    if raw_value.lower() == "max":
        return None

    try:
        parsed = int(raw_value)
    except ValueError:
        return fallback

    if parsed < 0:
        return fallback
    return min(parsed, MAX_SCRAPE_LIMIT)


def parse_keywords(params: Mapping[str, str]) -> list[str]:
    raw = _get_param(params, "keywords") or _get_param(params, "q") or ""
    keywords: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        normalized = " ".join(token.strip().lower().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(normalized)
    return keywords


def parse_keyword_mode(params: Mapping[str, str]) -> KeywordMode:
    raw = (
        _get_param(params, "keywordMode")
        or _get_param(params, "keywordsMode")
        or _get_param(params, "matchMode")
        or ""
    ).lower()
    return "or" if raw == "or" else DEFAULT_KEYWORD_MODE


def parse_sort_by(params: Mapping[str, str]) -> OfferSortBy:
    raw = (
        _get_param(params, "sortBy")
        or _get_param(params, "sort")
        or _get_param(params, "orderBy")
        or ""
    ).lower()
    if raw in {"name", "title", "nazwa"}:
        return "name"
    if raw in {"salary", "wynagrodzenie", "pay"}:
        return "salary"
    return DEFAULT_SORT_BY


def parse_sort_direction(params: Mapping[str, str]) -> OfferSortDirection:
    raw = (
        _get_param(params, "sortDirection")
        or _get_param(params, "sortOrder")
        or _get_param(params, "order")
        or ""
    ).lower()
    if raw in {"desc", "descending", "down", "-1"}:
        return "desc"
    if raw in {"asc", "ascending", "up", "1"}:
        return "asc"
    return DEFAULT_SORT_DIRECTION


def parse_salary_range_only(params: Mapping[str, str]) -> bool:
    raw = (
        _get_param(params, "salaryRangeOnly")
        or _get_param(params, "withSalaryRange")
        or _get_param(params, "salaryOnly")
        or ""
    ).lower()
    return raw in {"1", "true", "yes", "on"}


def parse_stream_mode(params: Mapping[str, str]) -> bool:
    raw = (_get_param(params, "stream") or _get_param(params, "progress") or "").lower()
    return raw in {"1", "true", "yes", "on"}


def parse_scrape_timeout_seconds(params: Mapping[str, str]) -> int | None:
    raw = (
        _get_param(params, "timeoutSeconds")
        or _get_param(params, "scrapeTimeoutSeconds")
        or _get_param(params, "timeout")
    )
    if raw is None:
        return None

    try:
        parsed = int(raw)
    except ValueError:
        return None

    if parsed <= 0:
        return None

    return max(
        MIN_SOURCE_SCRAPE_TIMEOUT_S,
        min(parsed, MAX_SOURCE_SCRAPE_TIMEOUT_S),
    )


def _has_salary_range(salary: str | None) -> bool:
    if not salary:
        return False
    import re

    return bool(re.search(r"\d[\d\s,.]*\s*-\s*\d[\d\s,.]*", salary))


def _has_keyword_match(
    matched_count: int,
    requested_count: int,
    keyword_mode: KeywordMode,
) -> bool:
    if requested_count == 0:
        return True
    if keyword_mode == "and":
        return matched_count == requested_count
    return matched_count > 0


def _normalize_search_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _tokenize_search_text(searchable_text: str) -> list[str]:
    return [token for token in _normalize_search_text(searchable_text).split() if token]


def _keyword_matches_tokens(keyword: str, tokens: list[str]) -> bool:
    normalized_keyword = _normalize_search_text(keyword)
    if not normalized_keyword:
        return False
    return any(normalized_keyword in token for token in tokens)


def _get_offer_searchable_text(offer: ScrapedOffer) -> str:
    if offer.searchable_text.strip():
        return offer.searchable_text
    return " ".join(
        [
            offer.title,
            offer.company,
            offer.location,
            offer.salary or "",
            " ".join(offer.skills),
        ]
    )


def dedupe_offers(offers: list[ScrapedOffer]) -> list[ScrapedOffer]:
    deduped: dict[str, ScrapedOffer] = {}
    for offer in offers:
        key = offer.url or f"{offer.source}:{offer.id}"
        deduped.setdefault(key, offer)
    return list(deduped.values())


def to_public_offers(
    offers: list[ScrapedOffer],
    keywords: list[str],
    keyword_mode: KeywordMode,
    salary_range_only: bool,
) -> list[PublicOffer]:
    with_matches: list[PublicOffer] = []
    for offer in offers:
        searchable_text = _get_offer_searchable_text(offer)
        tokens = _tokenize_search_text(searchable_text)
        matched_keywords = [
            keyword for keyword in keywords if _keyword_matches_tokens(keyword, tokens)
        ]
        if not _has_keyword_match(len(matched_keywords), len(keywords), keyword_mode):
            continue
        if salary_range_only and not _has_salary_range(offer.salary):
            continue
        with_matches.append(
            {
                "id": offer.id,
                "source": offer.source,
                "title": offer.title,
                "company": offer.company,
                "location": offer.location,
                "salary": offer.salary,
                "url": offer.url,
                "skills": offer.skills,
                "matchedKeywords": matched_keywords,
            }
        )
    return with_matches


def _to_numeric_salary_value(salary: str | None) -> int | None:
    if not salary:
        return None
    import re

    fragments = re.findall(r"\d[\d\s,.]*", salary)
    numeric_values: list[int] = []
    for fragment in fragments:
        digits = re.sub(r"[^\d]", "", fragment)
        if not digits:
            continue
        try:
            numeric_values.append(int(digits))
        except ValueError:
            continue
    return max(numeric_values) if numeric_values else None


def sort_offers(
    offers: list[PublicOffer],
    sort_by: OfferSortBy,
    sort_direction: OfferSortDirection,
) -> list[PublicOffer]:
    if sort_by == "relevance":
        return offers

    direction = 1 if sort_direction == "asc" else -1

    def _name_key(offer: PublicOffer) -> tuple[str, str, str]:
        return (offer["title"].casefold(), offer["company"].casefold(), offer["url"])

    def _salary_key(offer: PublicOffer) -> tuple[int, int, str]:
        value = _to_numeric_salary_value(offer["salary"])
        if value is None:
            # Always keep offers without salary at the end.
            return (1, 0, offer["title"].casefold())
        return (0, value * direction, offer["title"].casefold())

    if sort_by == "name":
        return sorted(
            offers,
            key=_name_key,
            reverse=(sort_direction == "desc"),
        )

    return sorted(offers, key=_salary_key)


def _to_scrape_target_label(target: int | None) -> ScrapeTargetLabel:
    return "max" if target is None else target


def _build_search_cache_key(
    limit: int,
    keywords: list[str],
    keyword_mode: KeywordMode,
    salary_range_only: bool,
    sort_by: OfferSortBy,
    sort_direction: OfferSortDirection,
    source_targets: dict[OfferSource, int | None],
    timeout_override_s: int | None,
) -> str:
    return json.dumps(
        {
            "limit": limit,
            "keywords": keywords,
            "keywordMode": keyword_mode,
            "salaryRangeOnly": salary_range_only,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
            "sourceTargets": {source: source_targets[source] for source in ALL_SOURCES},
            "timeoutSeconds": timeout_override_s,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


async def _get_cached_search_result(
    cache_key: str,
) -> tuple[int, dict[str, Any]] | None:
    async with _SEARCH_RESULT_CACHE_LOCK:
        cached = _SEARCH_RESULT_CACHE.get(cache_key)
        if cached is None:
            return None
        if cached.expires_at <= time.time():
            _SEARCH_RESULT_CACHE.pop(cache_key, None)
            return None
        return cached.status, copy.deepcopy(cached.payload)


async def _set_cached_search_result(
    cache_key: str,
    status: int,
    payload: dict[str, Any],
) -> None:
    async with _SEARCH_RESULT_CACHE_LOCK:
        _SEARCH_RESULT_CACHE[cache_key] = CachedSearchResult(
            expires_at=time.time() + SEARCH_RESULT_CACHE_TTL_S,
            status=status,
            payload=copy.deepcopy(payload),
        )


def _get_scraped_total(scraped_by_source: dict[OfferSource, int]) -> int:
    return sum(scraped_by_source[source] for source in ALL_SOURCES)


def _clamp_progress(value: float) -> float:
    if not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(float(value), 1.0))


def _compute_progress_percent(
    source_progress: dict[OfferSource, float],
    active_sources: list[OfferSource],
) -> int:
    if not active_sources:
        return 95
    total = sum(_clamp_progress(source_progress[source]) for source in active_sources)
    return min(95, round((total / len(active_sources)) * 95))


async def _run_with_timeout(
    source_label: str,
    timeout_s: int,
    runner: Callable[[], asyncio.Future | Any],
) -> Any:
    try:
        return await asyncio.wait_for(runner(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"{source_label} scraping timed out after {timeout_s * 1000} ms"
        ) from exc


async def run_scrape(
    params: Mapping[str, str],
    on_progress: ProgressHandler | None = None,
    stop_event: asyncio.Event | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run the full multi-source scraping pipeline."""
    started_at = time.time()
    limit = parse_limit(params)
    keywords = parse_keywords(params)
    keyword_mode = parse_keyword_mode(params)
    salary_range_only = parse_salary_range_only(params)
    sort_by = parse_sort_by(params)
    sort_direction = parse_sort_direction(params)
    timeout_override_s = parse_scrape_timeout_seconds(params)

    source_targets: dict[OfferSource, int | None] = {
        source: parse_source_scrape_limit(params, source, DEFAULT_SOURCE_TARGETS[source])
        for source in ALL_SOURCES
    }
    cache_key = _build_search_cache_key(
        limit,
        keywords,
        keyword_mode,
        salary_range_only,
        sort_by,
        sort_direction,
        source_targets,
        timeout_override_s,
    )
    cached_result = await _get_cached_search_result(cache_key)
    if cached_result is not None:
        return cached_result

    requested_scrape_by_source: dict[OfferSource, ScrapeTargetLabel] = {
        source: _to_scrape_target_label(source_targets[source]) for source in ALL_SOURCES
    }

    active_sources = [
        source
        for source in ALL_SOURCES
        if source_targets[source] is None or source_targets[source] > 0
    ]

    scraped_by_source: dict[OfferSource, int] = {source: 0 for source in ALL_SOURCES}
    source_progress: dict[OfferSource, float] = {
        source: (0.0 if source in active_sources else 1.0) for source in ALL_SOURCES
    }
    progress_state_lock = Lock()

    def build_progress_event(
        stage: str,
        progress_percent: int,
        message: str,
    ) -> dict[str, Any]:
        return {
            "stage": stage,
            "progressPercent": progress_percent,
            "message": message,
            "requestedScrapeBySource": requested_scrape_by_source,
            "scrapedTotalCount": _get_scraped_total(scraped_by_source),
            "scrapedBySource": dict(scraped_by_source),
        }

    def send_progress_event(stage: str, progress_percent: int, message: str) -> None:
        if not on_progress:
            return
        with progress_state_lock:
            event = build_progress_event(stage, progress_percent, message)
        on_progress(event)

    def update_source_progress(source: OfferSource, collected: int, progress: float) -> None:
        with progress_state_lock:
            scraped_by_source[source] = collected
            source_progress[source] = _clamp_progress(progress)
            event = build_progress_event(
                "scraping",
                _compute_progress_percent(source_progress, active_sources),
                f"Scraping {SOURCE_LABELS[source]} ({collected} offers)",
            )
        if on_progress:
            on_progress(event)

    async def run_nofluffjobs() -> list[ScrapedOffer]:
        return await scrape_nofluffjobs(
            source_targets["nofluffjobs"],
            lambda event: update_source_progress(
                "nofluffjobs", int(event["collected"]), float(event["progress"])
            ),
        )

    async def run_justjoinit() -> list[ScrapedOffer]:
        return await scrape_justjoinit(
            source_targets["justjoinit"],
            lambda event: update_source_progress(
                "justjoinit", int(event["collected"]), float(event["progress"])
            ),
        )

    async def run_bulldogjob() -> list[ScrapedOffer]:
        return await scrape_bulldogjob(
            source_targets["bulldogjob"],
            lambda event: update_source_progress(
                "bulldogjob", int(event["collected"]), float(event["progress"])
            ),
        )

    async def run_theprotocol() -> list[ScrapedOffer]:
        return await scrape_theprotocol(
            source_targets["theprotocol"],
            lambda event: update_source_progress(
                "theprotocol", int(event["collected"]), float(event["progress"])
            ),
        )

    async def run_solidjobs() -> list[ScrapedOffer]:
        return await scrape_solidjobs(
            source_targets["solidjobs"],
            lambda event: update_source_progress(
                "solidjobs", int(event["collected"]), float(event["progress"])
            ),
        )

    async def run_pracujpl() -> list[ScrapedOffer]:
        return await scrape_pracujpl(
            source_targets["pracujpl"],
            lambda event: update_source_progress(
                "pracujpl", int(event["collected"]), float(event["progress"])
            ),
        )

    scrape_tasks: list[tuple[OfferSource, int | None, Callable[[], Any]]] = [
        ("nofluffjobs", source_targets["nofluffjobs"], run_nofluffjobs),
        ("justjoinit", source_targets["justjoinit"], run_justjoinit),
        ("bulldogjob", source_targets["bulldogjob"], run_bulldogjob),
        ("theprotocol", source_targets["theprotocol"], run_theprotocol),
        ("solidjobs", source_targets["solidjobs"], run_solidjobs),
        ("pracujpl", source_targets["pracujpl"], run_pracujpl),
    ]
    task_entries: list[tuple[OfferSource, int | None, asyncio.Task[list[ScrapedOffer]]]] = []
    stop_requested = False

    send_progress_event("start", 0, "Starting scrape...")

    async def _execute_task(
        source: OfferSource,
        target: int | None,
        runner: Callable[[], Any],
    ) -> list[ScrapedOffer]:
        if target is not None and target <= 0:
            return []
        timeout_s = timeout_override_s or (
            SOURCE_SCRAPE_TIMEOUT_IN_MAX_MODE_S
            if target is None
            else SOURCE_SCRAPE_TIMEOUT_S
        )
        return await _run_with_timeout(SOURCE_LABELS[source], timeout_s, runner)

    for source, target, runner in scrape_tasks:
        task_entries.append(
            (
                source,
                target,
                asyncio.create_task(_execute_task(source, target, runner)),
            )
        )

    pending_tasks: set[asyncio.Task[list[ScrapedOffer]]] = {
        task for _, _, task in task_entries
    }
    stop_waiter = (
        asyncio.create_task(stop_event.wait())
        if stop_event is not None
        else None
    )

    try:
        while pending_tasks:
            wait_set: set[asyncio.Task[Any]] = set(pending_tasks)
            if stop_waiter is not None:
                wait_set.add(stop_waiter)

            done, _ = await asyncio.wait(
                wait_set,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if stop_waiter is not None and stop_waiter in done:
                stop_requested = True
                send_progress_event(
                    "finalizing",
                    _compute_progress_percent(source_progress, active_sources),
                    "Stopping scrape and returning partial results...",
                )
                for task in pending_tasks:
                    task.cancel()
                break

            pending_tasks -= {
                task for task in done if task is not stop_waiter
            }
    finally:
        if stop_waiter is not None and not stop_waiter.done():
            stop_waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stop_waiter

    errors: list[dict[str, str]] = []
    collected: list[ScrapedOffer] = []

    for source, target, task in task_entries:
        try:
            result = await task
        except asyncio.CancelledError:
            if stop_requested:
                continue
            if target is None or target > 0:
                errors.append({"source": source, "message": "Scraping was cancelled"})
            continue
        except Exception as exc:
            if target is None or target > 0:
                errors.append({"source": source, "message": str(exc)})
            continue
        with progress_state_lock:
            scraped_by_source[source] = len(result)
            source_progress[source] = 1.0
        collected.extend(result)

    send_progress_event("finalizing", 97, "Finalizing and filtering results...")

    deduped_scraped = dedupe_offers(collected)
    filtered_offers = to_public_offers(
        deduped_scraped,
        keywords,
        keyword_mode,
        salary_range_only,
    )
    offers = sort_offers(filtered_offers, sort_by, sort_direction)[:limit]
    scraped_total_count = _get_scraped_total(scraped_by_source)

    attempted_source_count = sum(
        1 for _, target, _ in scrape_tasks if target is None or target > 0
    )
    status = (
        502
        if not stop_requested
        and attempted_source_count > 0
        and len(errors) == attempted_source_count
        else 200
    )

    payload = {
        "meta": {
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "durationMs": round((time.time() - started_at) * 1000),
            "wasStopped": stop_requested,
            "requestedScrapeBySource": requested_scrape_by_source,
            "scrapedTotalCount": scraped_total_count,
            "scrapedBySource": scraped_by_source,
            "dedupedScrapedCount": len(deduped_scraped),
            "requestedLimit": limit,
            "returnedCount": len(offers),
            "keywords": keywords,
            "keywordMode": keyword_mode,
            "salaryRangeOnly": salary_range_only,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        },
        "data": offers,
        "errors": errors,
    }

    send_progress_event(
        "done",
        100,
        "Scraping stopped. Returning partial results."
        if stop_requested
        else "Scraping completed",
    )
    if not errors and not stop_requested:
        await _set_cached_search_result(cache_key, status, payload)
    return status, payload
