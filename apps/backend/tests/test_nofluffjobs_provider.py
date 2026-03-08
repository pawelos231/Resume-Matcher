import json
import unittest
from unittest.mock import patch

from app.services.search.fetch_with_timeout import FetchResponse
from app.services.search.providers import nofluffjobs
from app.services.search.types import ScrapedOffer


def _posting(posting_id: str, title: str, slug: str) -> dict[str, object]:
    return {
        "id": posting_id,
        "title": title,
        "name": "Acme",
        "url": slug,
        "location": {"places": [{"city": "Warsaw"}]},
        "tiles": {"values": []},
    }


def _response_with_postings(postings: list[dict[str, object]]) -> FetchResponse:
    payload = f'{{"postings":{json.dumps(postings)}}}'.encode("utf-8")
    return FetchResponse(
        status=200,
        content=payload,
        headers={},
        set_cookie_headers=[],
    )


def _offer(source_id: str) -> ScrapedOffer:
    return ScrapedOffer(
        id=source_id,
        source="nofluffjobs",
        title=source_id,
        company="Acme",
        location="Warsaw",
        salary=None,
        url=f"https://nofluffjobs.com/pl/job/{source_id}",
        skills=[],
        searchable_text=source_id,
    )


class TestNoFluffJobsProvider(unittest.TestCase):
    def test_build_category_page_url_uses_category_slug(self) -> None:
        self.assertEqual(
            nofluffjobs._build_category_page_url("backend", 1),
            "https://nofluffjobs.com/pl/backend",
        )
        self.assertEqual(
            nofluffjobs._build_category_page_url("frontend", 3),
            "https://nofluffjobs.com/pl/frontend?page=3",
        )

    def test_interleave_category_offers_round_robins_categories(self) -> None:
        offers_by_category = {
            slug: []
            for slug in nofluffjobs.NO_FLUFF_CATEGORY_SLUGS
        }
        offers_by_category["backend"] = [_offer("backend-1"), _offer("backend-2")]
        offers_by_category["frontend"] = [_offer("frontend-1")]
        offers_by_category["data"] = [_offer("data-1"), _offer("data-2")]

        result = nofluffjobs._interleave_category_offers(offers_by_category)

        self.assertEqual(
            [offer.id for offer in result],
            ["backend-1", "frontend-1", "data-1", "backend-2", "data-2"],
        )


class TestNoFluffJobsScrapeDeterminism(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_nofluffjobs_uses_fixed_category_order(self) -> None:
        requested_urls: list[str] = []

        async def _fake_fetch(
            url: str,
            *,
            method: str = "GET",
            headers: dict[str, str] | None = None,
            body: bytes | None = None,
            timeout_ms: int = 15_000,
        ) -> FetchResponse:
            _ = method
            _ = headers
            _ = body
            _ = timeout_ms
            requested_urls.append(url)
            if url.endswith("/backend"):
                return _response_with_postings(
                    [
                        _posting("backend-1", "Backend One", "backend-one"),
                        _posting("backend-2", "Backend Two", "backend-two"),
                    ]
                )
            if url.endswith("/frontend"):
                return _response_with_postings(
                    [
                        _posting("frontend-1", "Frontend One", "frontend-one"),
                        _posting("frontend-2", "Frontend Two", "frontend-two"),
                    ]
                )
            return _response_with_postings([])

        with (
            patch.object(
                nofluffjobs,
                "NO_FLUFF_CATEGORY_SLUGS",
                ("backend", "frontend"),
            ),
            patch.object(nofluffjobs, "fetch_with_timeout", new=_fake_fetch),
        ):
            result = await nofluffjobs.scrape_nofluffjobs(3)

        self.assertEqual(
            requested_urls,
            [
                "https://nofluffjobs.com/pl/backend",
                "https://nofluffjobs.com/pl/frontend",
            ],
        )
        self.assertEqual(
            [offer.id for offer in result],
            ["backend-1", "frontend-1", "backend-2"],
        )
