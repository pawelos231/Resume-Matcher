import asyncio
import unittest
from collections.abc import Awaitable, Callable
from unittest.mock import patch

from app.services.search import pipeline
from app.services.search.pipeline import (
    ALL_SOURCES,
    MAX_SOURCE_SCRAPE_TIMEOUT_S,
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


class TestSearchKeywordParsing(unittest.TestCase):
    def test_parse_keywords_splits_only_on_commas(self) -> None:
        result = parse_keywords({"keywords": "react, node , typescript"})

        self.assertEqual(result, ["react", "node", "typescript"])

    def test_parse_keywords_preserves_space_inside_single_keyword(self) -> None:
        result = parse_keywords({"keywords": "machine learning, data science"})

        self.assertEqual(result, ["machine learning", "data science"])


class TestSearchTimeoutParsing(unittest.TestCase):
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

        with (
            patch(
                "app.services.search.pipeline.scrape_nofluffjobs",
                new=_make_scraper("nofluffjobs", 0.05),
            ),
            patch(
                "app.services.search.pipeline.scrape_justjoinit",
                new=_make_scraper("justjoinit", 0.01),
            ),
            patch(
                "app.services.search.pipeline.scrape_bulldogjob",
                new=_make_scraper("bulldogjob", 0.03),
            ),
            patch(
                "app.services.search.pipeline.scrape_theprotocol",
                new=_make_scraper("theprotocol", 0.02),
            ),
            patch(
                "app.services.search.pipeline.scrape_solidjobs",
                new=_make_scraper("solidjobs", 0.04),
            ),
            patch(
                "app.services.search.pipeline.scrape_pracujpl",
                new=_make_scraper("pracujpl", 0.0),
            ),
        ):
            status, payload = await run_scrape({"limit": "1000"})

        self.assertEqual(status, 200)
        self.assertEqual(
            [offer["source"] for offer in payload["data"]],
            ALL_SOURCES,
        )

    async def test_run_scrape_reuses_cached_result_for_same_query(self) -> None:
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

        with (
            patch(
                "app.services.search.pipeline.scrape_nofluffjobs",
                new=_make_scraper("nofluffjobs"),
            ),
            patch(
                "app.services.search.pipeline.scrape_justjoinit",
                new=_make_scraper("justjoinit"),
            ),
            patch(
                "app.services.search.pipeline.scrape_bulldogjob",
                new=_make_scraper("bulldogjob"),
            ),
            patch(
                "app.services.search.pipeline.scrape_theprotocol",
                new=_make_scraper("theprotocol"),
            ),
            patch(
                "app.services.search.pipeline.scrape_solidjobs",
                new=_make_scraper("solidjobs"),
            ),
            patch(
                "app.services.search.pipeline.scrape_pracujpl",
                new=_make_scraper("pracujpl"),
            ),
        ):
            first_status, first_payload = await run_scrape({"limit": "1000", "keywords": "java"})
            second_status, second_payload = await run_scrape(
                {"limit": "1000", "keywords": "java"}
            )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(first_payload, second_payload)
        self.assertEqual(call_counts, {source: 1 for source in ALL_SOURCES})

    async def test_run_scrape_uses_timeout_override_for_all_sources(self) -> None:
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

        with (
            patch(
                "app.services.search.pipeline.scrape_nofluffjobs",
                new=_make_scraper("nofluffjobs"),
            ),
            patch(
                "app.services.search.pipeline.scrape_justjoinit",
                new=_make_scraper("justjoinit"),
            ),
            patch(
                "app.services.search.pipeline.scrape_bulldogjob",
                new=_make_scraper("bulldogjob"),
            ),
            patch(
                "app.services.search.pipeline.scrape_theprotocol",
                new=_make_scraper("theprotocol"),
            ),
            patch(
                "app.services.search.pipeline.scrape_solidjobs",
                new=_make_scraper("solidjobs"),
            ),
            patch(
                "app.services.search.pipeline.scrape_pracujpl",
                new=_make_scraper("pracujpl"),
            ),
            patch(
                "app.services.search.pipeline._run_with_timeout",
                new=_fake_run_with_timeout,
            ),
        ):
            status, _payload = await run_scrape(
                {"limit": "1000", "timeoutSeconds": "240"}
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

        with (
            patch(
                "app.services.search.pipeline.scrape_nofluffjobs",
                new=_make_stoppable_scraper("nofluffjobs"),
            ),
            patch(
                "app.services.search.pipeline.scrape_justjoinit",
                new=_make_stoppable_scraper("justjoinit"),
            ),
            patch(
                "app.services.search.pipeline.scrape_bulldogjob",
                new=_make_stoppable_scraper("bulldogjob"),
            ),
            patch(
                "app.services.search.pipeline.scrape_theprotocol",
                new=_make_stoppable_scraper("theprotocol"),
            ),
            patch(
                "app.services.search.pipeline.scrape_solidjobs",
                new=_make_stoppable_scraper("solidjobs"),
            ),
            patch(
                "app.services.search.pipeline.scrape_pracujpl",
                new=_make_stoppable_scraper("pracujpl"),
            ),
        ):
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
