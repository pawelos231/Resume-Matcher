import unittest

from app.services.search.pipeline import to_public_offers
from app.services.search.types import ScrapedOffer


def _offer(
    title: str,
    *,
    skills: list[str] | None = None,
    searchable_text: str | None = None,
) -> ScrapedOffer:
    return ScrapedOffer(
        id="offer-1",
        source="justjoinit",
        title=title,
        company="Acme",
        location="Warsaw",
        salary=None,
        url="https://example.com/offer-1",
        skills=skills or ["python"],
        searchable_text=searchable_text or title.lower(),
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

    def test_matches_keyword_as_token_substring(self) -> None:
        offers = [_offer("Senior JavaScript Developer")]

        result = to_public_offers(
            offers=offers,
            keywords=["java"],
            keyword_mode="and",
            salary_range_only=False,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["matchedKeywords"], ["java"])

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

    def test_matches_keywords_against_searchable_text_not_only_title(self) -> None:
        offers = [
            _offer(
                "Senior Engineer",
                skills=["react", "typescript"],
                searchable_text="Senior Engineer\nReact TypeScript remote",
            )
        ]

        result = to_public_offers(
            offers=offers,
            keywords=["react"],
            keyword_mode="and",
            salary_range_only=False,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["matchedKeywords"], ["react"])

    def test_matches_keyword_inside_punctuated_token(self) -> None:
        offers = [
            _offer(
                "Senior Engineer",
                searchable_text="senior engineer node.js typescript",
            )
        ]

        result = to_public_offers(
            offers=offers,
            keywords=["node"],
            keyword_mode="and",
            salary_range_only=False,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["matchedKeywords"], ["node"])

    def test_and_mode_requires_keyword_matches_in_any_tokens(self) -> None:
        offers = [
            _offer(
                "Backend Engineer",
                searchable_text="backend engineer\nnode.js typescript remote-first",
            )
        ]

        result = to_public_offers(
            offers=offers,
            keywords=["node", "type"],
            keyword_mode="and",
            salary_range_only=False,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["matchedKeywords"], ["node", "type"])
