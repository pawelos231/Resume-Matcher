import unittest

from app.services.search.pipeline import to_public_offers
from app.services.search.types import ScrapedOffer


def _offer(title: str) -> ScrapedOffer:
    return ScrapedOffer(
        id="offer-1",
        source="justjoinit",
        title=title,
        company="Acme",
        location="Warsaw",
        salary=None,
        url="https://example.com/offer-1",
        skills=["python"],
        searchable_text=title.lower(),
    )


class TestSearchKeywordMatching(unittest.TestCase):
    def test_matches_keywords_against_title_tokens(self) -> None:
        offers = [_offer("Senior React Developer")]

        result = to_public_offers(
            offers=offers,
            keywords=["react"],
            keyword_mode="and",
            salary_range_only=False,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["matchedKeywords"], ["react"])

    def test_does_not_match_keyword_as_substring(self) -> None:
        offers = [_offer("Senior JavaScript Developer")]

        result = to_public_offers(
            offers=offers,
            keywords=["java"],
            keyword_mode="and",
            salary_range_only=False,
        )

        self.assertEqual(result, [])

    def test_and_mode_requires_all_keywords_tokens(self) -> None:
        offers = [_offer("React Developer")]

        result = to_public_offers(
            offers=offers,
            keywords=["react", "node"],
            keyword_mode="and",
            salary_range_only=False,
        )

        self.assertEqual(result, [])

    def test_or_mode_accepts_any_matching_token(self) -> None:
        offers = [_offer("Senior Python Engineer")]

        result = to_public_offers(
            offers=offers,
            keywords=["python", "react"],
            keyword_mode="or",
            salary_range_only=False,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["matchedKeywords"], ["python"])
