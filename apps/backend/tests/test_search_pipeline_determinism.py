import asyncio
import contextlib
import unittest
from collections.abc import Awaitable, Callable
from unittest.mock import patch

from app.services.search import pipeline
from app.services.search.pipeline import (
    ALL_SOURCES,
    MAX_SOURCE_SCRAPE_TIMEOUT_S,
    MIN_SOURCE_SCRAPE_TIMEOUT_S,
    parse_keywords,
    parse_scrape_timeout_seconds,
    run_scrape,
)
from app.services.search.types import ScrapedOffer


def _offer(source: str) -> ScrapedOffer:
    return ScrapedOffer(
        id=f"{source}-1",
        source=source,  # type: ignore[arg-type]
        title=f"{source} title",
        company="Acme",
        location="Warsaw",
        salary=None,
        url=f"https://example.com/{source}",
        skills=[],
        searchable_text=f"{source} title",
    )


SCRAPER_PATCH_TARGETS = {
    "nofluffjobs": "app.services.search.pipeline.scrape_nofluffjobs",
    "justjoinit": "app.services.search.pipeline.scrape_justjoinit",
    "bulldogjob": "app.services.search.pipeline.scrape_bulldogjob",
    "theprotocol": "app.services.search.pipeline.scrape_theprotocol",
    "solidjobs": "app.services.search.pipeline.scrape_solidjobs",
    "pracujpl": "app.services.search.pipeline.scrape_pracujpl",
    "rocketjobs": "app.services.search.pipeline.scrape_rocketjobs",
    "olxpraca": "app.services.search.pipeline.scrape_olxpraca",
    "indeed": "app.services.search.pipeline.scrape_indeed",
    "glassdoor": "app.services.search.pipeline.scrape_glassdoor",
    "ziprecruiter": "app.services.search.pipeline.scrape_ziprecruiter",
    "careerbuilder": "app.services.search.pipeline.scrape_careerbuilder",
}


def _patch_pipeline_scrapers(
    replacements: dict[str, Callable[..., Awaitable[list[ScrapedOffer]]]],
) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    for source, replacement in replacements.items():
        stack.enter_context(patch(SCRAPER_PATCH_TARGETS[source], new=replacement))
    return stack


class TestSearchKeywordParsing(unittest.TestCase):
    def test_parse_keywords_splits_only_on_commas(self) -> None:
        result = parse_keywords({"keywords": "react, node , typescript"})

        self.assertEqual(result, ["react", "node", "typescript"])

    def test_parse_keywords_preserves_space_inside_single_keyword(self) -> None:
        result = parse_keywords({"keywords": "machine learning, data science"})

        self.assertEqual(result, ["machine learning", "data science"])


class TestSearchTimeoutParsing(unittest.TestCase):
    def test_parse_scrape_timeout_seconds_allows_ten_second_override(self) -> None:
        result = parse_scrape_timeout_seconds({"timeoutSeconds": "10"})

        self.assertEqual(result, MIN_SOURCE_SCRAPE_TIMEOUT_S)

    def test_parse_scrape_timeout_seconds_clamps_high_values(self) -> None:
        result = parse_scrape_timeout_seconds({"timeoutSeconds": "9999"})

        self.assertEqual(result, MAX_SOURCE_SCRAPE_TIMEOUT_S)

    def test_parse_scrape_timeout_seconds_returns_none_for_invalid_values(self) -> None:
        result = parse_scrape_timeout_seconds({"timeoutSeconds": "abc"})

        self.assertIsNone(result)


class TestSearchPipelineDeterminism(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with pipeline._SEARCH_RESULT_CACHE_LOCK:
            pipeline._SEARCH_RESULT_CACHE.clear()

    async def test_run_scrape_keeps_provider_order_with_parallel_scrapers(self) -> None:
        delays = {
            "nofluffjobs": 0.05,
            "justjoinit": 0.01,
            "bulldogjob": 0.03,
            "theprotocol": 0.02,
            "solidjobs": 0.04,
            "pracujpl": 0.0,
            "rocketjobs": 0.025,
            "olxpraca": 0.015,
            "indeed": 0.035,
            "glassdoor": 0.045,
            "ziprecruiter": 0.005,
            "careerbuilder": 0.055,
        }

        def _make_scraper(
            source: str,
            delay_s: float,
        ) -> Callable[[int | None, object | None], Awaitable[list[ScrapedOffer]]]:
            async def _scraper(
                target_count: int | None,
                on_progress: object | None = None,
            ) -> list[ScrapedOffer]:
                _ = target_count
                _ = on_progress
                await asyncio.sleep(delay_s)
                return [_offer(source)]

            return _scraper

        replacements = {
            source: _make_scraper(source, delays[source]) for source in ALL_SOURCES
        }
        with _patch_pipeline_scrapers(replacements):
            status, payload = await run_scrape({"limit": "1000"})

        self.assertEqual(status, 200)
        self.assertEqual(
            [offer["source"] for offer in payload["data"]],
            ALL_SOURCES,
        )

    async def test_run_scrape_reuses_cached_result_for_same_query(self) -> None:
        async with pipeline._SEARCH_RESULT_CACHE_LOCK:
            pipeline._SEARCH_RESULT_CACHE.clear()

        call_counts: dict[str, int] = {source: 0 for source in ALL_SOURCES}

        def _make_scraper(
            source: str,
        ) -> Callable[[int | None, object | None], Awaitable[list[ScrapedOffer]]]:
            async def _scraper(
                target_count: int | None,
                on_progress: object | None = None,
            ) -> list[ScrapedOffer]:
                _ = target_count
                _ = on_progress
                call_counts[source] += 1
                return [_offer(source)]

            return _scraper

        replacements = {source: _make_scraper(source) for source in ALL_SOURCES}
        with _patch_pipeline_scrapers(replacements):
            first_status, first_payload = await run_scrape(
                {"limit": "1000", "keywords": "java-cache-test"}
            )
            second_status, second_payload = await run_scrape(
                {"limit": "1000", "keywords": "java-cache-test"}
            )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(first_payload, second_payload)
        self.assertEqual(call_counts, {source: 1 for source in ALL_SOURCES})

    async def test_run_scrape_uses_timeout_override_for_all_sources(self) -> None:
        async with pipeline._SEARCH_RESULT_CACHE_LOCK:
            pipeline._SEARCH_RESULT_CACHE.clear()

        captured_timeouts: list[int] = []

        def _make_scraper(
            source: str,
        ) -> Callable[[int | None, object | None], Awaitable[list[ScrapedOffer]]]:
            async def _scraper(
                target_count: int | None,
                on_progress: object | None = None,
            ) -> list[ScrapedOffer]:
                _ = target_count
                _ = on_progress
                return [_offer(source)]

            return _scraper

        async def _fake_run_with_timeout(
            source_label: str,
            timeout_s: int,
            runner: Callable[[], Awaitable[list[ScrapedOffer]]],
        ) -> list[ScrapedOffer]:
            _ = source_label
            captured_timeouts.append(timeout_s)
            return await runner()

        replacements = {source: _make_scraper(source) for source in ALL_SOURCES}
        with _patch_pipeline_scrapers(replacements), patch(
            "app.services.search.pipeline._run_with_timeout",
            new=_fake_run_with_timeout,
        ):
            status, _payload = await run_scrape(
                {"limit": "1000", "keywords": "timeout-override-test", "timeoutSeconds": "240"}
            )

        self.assertEqual(status, 200)
        self.assertEqual(len(captured_timeouts), len(ALL_SOURCES))
        self.assertTrue(all(timeout == 240 for timeout in captured_timeouts))

    async def test_run_scrape_returns_partial_results_when_stopped(self) -> None:
        stop_event = asyncio.Event()

        def _make_stoppable_scraper(
            source: str,
        ) -> Callable[[int | None, object | None], Awaitable[list[ScrapedOffer]]]:
            async def _scraper(
                target_count: int | None,
                on_progress: object | None = None,
            ) -> list[ScrapedOffer]:
                _ = target_count
                if callable(on_progress):
                    on_progress({"collected": 1, "progress": 0.5})

                partial = [_offer(source)]
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    return partial

                return partial

            return _scraper

        async def _trigger_stop() -> None:
            await asyncio.sleep(0.05)
            stop_event.set()

        replacements = {
            source: _make_stoppable_scraper(source) for source in ALL_SOURCES
        }
        with _patch_pipeline_scrapers(replacements):
            stop_task = asyncio.create_task(_trigger_stop())
            try:
                status, payload = await run_scrape(
                    {"limit": "1000", "keywords": "title"},
                    stop_event=stop_event,
                )
            finally:
                await stop_task

        self.assertEqual(status, 200)
        self.assertTrue(payload["meta"]["wasStopped"])
        self.assertEqual(payload["meta"]["scrapedTotalCount"], len(ALL_SOURCES))
        self.assertEqual(
            [offer["source"] for offer in payload["data"]],
            ALL_SOURCES,
        )

    async def test_run_scrape_passes_keywords_to_keyword_aware_scrapers(self) -> None:
        async with pipeline._SEARCH_RESULT_CACHE_LOCK:
            pipeline._SEARCH_RESULT_CACHE.clear()

        captured_keywords: dict[str, list[str] | None] = {}

        def _make_basic_scraper(
            source: str,
        ) -> Callable[[int | None, object | None], Awaitable[list[ScrapedOffer]]]:
            async def _scraper(
                target_count: int | None,
                on_progress: object | None = None,
            ) -> list[ScrapedOffer]:
                _ = target_count
                _ = on_progress
                return [_offer(source)]

            return _scraper

        async def _keyword_aware_scraper(
            target_count: int | None,
            on_progress: object | None = None,
            *,
            keywords: list[str] | None = None,
        ) -> list[ScrapedOffer]:
            _ = target_count
            _ = on_progress
            captured_keywords["rocketjobs"] = keywords
            return [_offer("rocketjobs")]

        replacements = {source: _make_basic_scraper(source) for source in ALL_SOURCES}
        replacements["rocketjobs"] = _keyword_aware_scraper

        with _patch_pipeline_scrapers(replacements):
            status, _payload = await run_scrape(
                {"limit": "1000", "keywords": "react,node,typescript,keyword-aware"}
            )

        self.assertEqual(status, 200)
        self.assertEqual(
            captured_keywords["rocketjobs"],
            ["react", "node", "typescript", "keyword-aware"],
        )
