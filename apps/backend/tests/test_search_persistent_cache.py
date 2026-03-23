import copy
import time
import unittest
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch

from app.routers import search as search_router
from app.schemas.search import (
    SearchCompanyInfoRequest,
    SearchGenerateJobDescriptionRequest,
)
from app.services.search import pipeline
from app.services.search.pipeline import ALL_SOURCES, run_scrape
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


class _FakePersistentSearchCacheDb:
    def __init__(self) -> None:
        self.entries: dict[str, dict[str, object]] = {}

    def get_cached_search_result(self, cache_key: str) -> dict[str, object] | None:
        entry = self.entries.get(cache_key)
        if entry is None:
            return None
        if float(entry["expires_at"]) <= time.time():
            self.entries.pop(cache_key, None)
            return None
        return copy.deepcopy(entry)

    def upsert_cached_search_result(
        self,
        cache_key: str,
        status: int,
        payload: dict[str, object],
        ttl_seconds: int,
    ) -> dict[str, object]:
        entry = {
            "cache_key": cache_key,
            "status": status,
            "payload": copy.deepcopy(payload),
            "expires_at": time.time() + ttl_seconds,
        }
        self.entries[cache_key] = entry
        return copy.deepcopy(entry)


class TestPersistentSearchCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with pipeline._SEARCH_RESULT_CACHE_LOCK:
            pipeline._SEARCH_RESULT_CACHE.clear()

    async def test_run_scrape_normalizes_missing_source_counts_from_persistent_cache(self) -> None:
        fake_db = _FakePersistentSearchCacheDb()
        source_targets = {
            source: pipeline.DEFAULT_SOURCE_TARGETS[source] for source in ALL_SOURCES
        }
        cache_key = pipeline._build_search_cache_key(
            1000,
            ["java"],
            "and",
            False,
            "relevance",
            "asc",
            source_targets,
            None,
        )
        fake_db.upsert_cached_search_result(
            cache_key=cache_key,
            status=200,
            payload={
                "meta": {
                    "generatedAt": "2026-03-15T00:00:00Z",
                    "durationMs": 123,
                    "wasStopped": False,
                    "requestedScrapeBySource": {
                        "nofluffjobs": "max",
                        "justjoinit": 10,
                    },
                    "scrapedTotalCount": 3,
                    "scrapedBySource": {
                        "nofluffjobs": 1,
                        "justjoinit": 2,
                    },
                    "dedupedScrapedCount": 3,
                    "requestedLimit": 1000,
                    "returnedCount": 0,
                    "keywords": ["java"],
                    "keywordMode": "and",
                    "salaryRangeOnly": False,
                    "sortBy": "relevance",
                    "sortDirection": "asc",
                },
                "data": [],
                "errors": [],
            },
            ttl_seconds=180,
        )

        with patch("app.services.search.pipeline.db", new=fake_db):
            status, payload = await run_scrape({"limit": "1000", "keywords": "java"})

        self.assertEqual(status, 200)
        self.assertEqual(payload["meta"]["requestedScrapeBySource"]["nofluffjobs"], "max")
        self.assertEqual(payload["meta"]["requestedScrapeBySource"]["rocketjobs"], 20)
        self.assertEqual(payload["meta"]["requestedScrapeBySource"]["careerbuilder"], 20)
        self.assertEqual(payload["meta"]["scrapedBySource"]["nofluffjobs"], 1)
        self.assertEqual(payload["meta"]["scrapedBySource"]["rocketjobs"], 0)
        self.assertEqual(payload["meta"]["scrapedBySource"]["careerbuilder"], 0)
        persisted_payload = fake_db.entries[cache_key]["payload"]
        self.assertEqual(
            persisted_payload["meta"]["requestedScrapeBySource"]["rocketjobs"],
            20,
        )
        self.assertEqual(
            persisted_payload["meta"]["scrapedBySource"]["careerbuilder"],
            0,
        )

    async def test_run_scrape_reuses_persistent_cache_after_memory_cache_is_cleared(self) -> None:
        call_counts: dict[str, int] = {source: 0 for source in ALL_SOURCES}
        fake_db = _FakePersistentSearchCacheDb()

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
            patch("app.services.search.pipeline.db", new=fake_db),
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
                "app.services.search.pipeline.scrape_rocketjobs",
                new=_make_scraper("rocketjobs"),
            ),
            patch(
                "app.services.search.pipeline.scrape_olxpraca",
                new=_make_scraper("olxpraca"),
            ),
            patch(
                "app.services.search.pipeline.scrape_indeed",
                new=_make_scraper("indeed"),
            ),
            patch(
                "app.services.search.pipeline.scrape_glassdoor",
                new=_make_scraper("glassdoor"),
            ),
            patch(
                "app.services.search.pipeline.scrape_ziprecruiter",
                new=_make_scraper("ziprecruiter"),
            ),
            patch(
                "app.services.search.pipeline.scrape_careerbuilder",
                new=_make_scraper("careerbuilder"),
            ),
        ):
            first_status, first_payload = await run_scrape(
                {"limit": "1000", "keywords": "java"}
            )

            async with pipeline._SEARCH_RESULT_CACHE_LOCK:
                pipeline._SEARCH_RESULT_CACHE.clear()

            second_status, second_payload = await run_scrape(
                {"limit": "1000", "keywords": "java"}
            )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(first_payload, second_payload)
        self.assertEqual(call_counts, {source: 1 for source in ALL_SOURCES})


class TestSearchRouterPersistentCompanyInfo(unittest.IsolatedAsyncioTestCase):
    async def test_generate_job_description_uses_cached_company_info_context(self) -> None:
        cached_company_info = {
            "response": {
                "summary": "Acme builds warehouse robots.",
                "highlights": ["Own robotics platform"],
                "evidence": [
                    {
                        "url": "https://acme.example/",
                        "title": "Acme",
                        "snippet": "Robotics platform for logistics teams.",
                    }
                ],
            }
        }
        request = SearchGenerateJobDescriptionRequest(
            id="offer-123",
            source="nofluffjobs",
            title="Backend Engineer",
            company="Acme",
            location="Warsaw",
            salary=None,
            url="https://jobs.example/acme",
            skills=["python"],
        )

        with (
            patch.object(
                search_router.db,
                "get_company_info_cache",
                return_value=copy.deepcopy(cached_company_info),
            ),
            patch(
                "app.routers.search.generate_job_description_from_offer",
                AsyncMock(
                    return_value={
                        "jobDescription": "Generated job description.",
                        "sourceTextLength": 123,
                        "usedLlm": True,
                    }
                ),
            ) as generate_mock,
        ):
            response = await search_router.generate_offer_job_description(request)

        called_offer = generate_mock.await_args.args[0]
        self.assertIn("Acme builds warehouse robots.", called_offer.company_context)
        self.assertEqual(response.companyContextSource, "cache")

    async def test_get_company_info_returns_persistent_cached_response(self) -> None:
        cached_response = {
            "company": "Acme",
            "websiteUrl": "https://acme.example/",
            "websiteFoundVia": "search_engine",
            "question": "What does this company do?",
            "summary": "Acme builds warehouse robots.",
            "highlights": ["Own robotics platform"],
            "sourcePages": [
                {
                    "url": "https://acme.example/",
                    "title": "Acme",
                }
            ],
            "evidence": [
                {
                    "url": "https://acme.example/",
                    "title": "Acme",
                    "snippet": "Robotics platform for logistics teams.",
                }
            ],
            "stats": {
                "pagesVisited": 1,
                "chunksIndexed": 3,
                "relevantChunks": 1,
                "retrievalMethod": "lexical",
                "usedLlm": True,
            },
        }
        request = SearchCompanyInfoRequest(
            id="offer-123",
            source="nofluffjobs",
            title="Backend Engineer",
            company="Acme",
            location="Warsaw",
            salary=None,
            url="https://jobs.example/acme",
            skills=["python"],
        )

        with (
            patch.object(
                search_router.db,
                "get_company_info_cache",
                return_value={"response": copy.deepcopy(cached_response)},
            ),
            patch(
                "app.routers.search.generate_company_info_from_offer",
                AsyncMock(),
            ) as generate_mock,
        ):
            response = await search_router.get_company_info(request)

        self.assertEqual(response.company, "Acme")
        self.assertEqual(response.summary, "Acme builds warehouse robots.")
        generate_mock.assert_not_awaited()


class TestSearchPayloadDecoration(unittest.TestCase):
    def test_decorate_search_payload_with_cache_status_adds_resume_and_company_flags(self) -> None:
        payload = {
            "meta": {},
            "data": [
                {
                    "id": "offer-123",
                    "source": "nofluffjobs",
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "location": "Warsaw",
                    "salary": None,
                    "url": "https://jobs.example/acme",
                    "skills": [],
                    "matchedKeywords": [],
                }
            ],
            "errors": [],
        }

        with patch.object(
            search_router.db,
            "get_offer_cache_statuses",
            return_value={
                "nofluffjobs:offer-123:https://jobs.example/acme": {
                    "has_company_info": True,
                    "resume_id": "resume-123",
                }
            },
        ):
            decorated = search_router._decorate_search_payload_with_cache_status(payload)

        offer = decorated["data"][0]
        self.assertEqual(offer["workMode"], "unknown")
        self.assertTrue(offer["alreadyGeneratedResume"])
        self.assertEqual(offer["generatedResumeId"], "resume-123")
        self.assertTrue(offer["alreadyGeneratedCompanyInfo"])
