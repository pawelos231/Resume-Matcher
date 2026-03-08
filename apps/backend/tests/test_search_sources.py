import unittest

from app.services.search.pipeline import (
    ALL_SOURCES,
    DEFAULT_SOURCE_TARGETS,
    parse_source_scrape_limit,
)


class TestSearchSources(unittest.TestCase):
    def test_pracujpl_is_registered(self) -> None:
        self.assertIn("pracujpl", ALL_SOURCES)
        self.assertEqual(DEFAULT_SOURCE_TARGETS["pracujpl"], 50)

    def test_pracujpl_limit_uses_named_query_key(self) -> None:
        result = parse_source_scrape_limit(
            {"scrapeLimitPracujPl": "25"},
            "pracujpl",
            DEFAULT_SOURCE_TARGETS["pracujpl"],
        )

        self.assertEqual(result, 25)

    def test_pracujpl_limit_supports_max_keyword(self) -> None:
        result = parse_source_scrape_limit(
            {"scrapeLimitPracuj": "max"},
            "pracujpl",
            DEFAULT_SOURCE_TARGETS["pracujpl"],
        )

        self.assertIsNone(result)
