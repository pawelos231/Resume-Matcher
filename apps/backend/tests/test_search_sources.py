import unittest

from app.services.search.pipeline import (
    ALL_SOURCES,
    DEFAULT_SOURCE_TARGETS,
    parse_source_scrape_limit,
)


class TestSearchSources(unittest.TestCase):
    def test_new_sources_are_registered(self) -> None:
        expected_defaults = {
            "pracujpl": 50,
            "rocketjobs": 20,
            "olxpraca": 20,
            "indeed": 20,
            "glassdoor": 20,
            "ziprecruiter": 20,
            "careerbuilder": 20,
        }

        for source, default_target in expected_defaults.items():
            with self.subTest(source=source):
                self.assertIn(source, ALL_SOURCES)
                self.assertEqual(DEFAULT_SOURCE_TARGETS[source], default_target)

    def test_source_limit_named_query_keys_are_supported(self) -> None:
        cases = [
            ("pracujpl", {"scrapeLimitPracujPl": "25"}, 25),
            ("rocketjobs", {"scrapeLimitRocketJobs": "12"}, 12),
            ("olxpraca", {"scrapeLimitOlxPraca": "14"}, 14),
            ("indeed", {"scrapeLimitIndeed": "16"}, 16),
            ("glassdoor", {"scrapeLimitGlassdoor": "18"}, 18),
            ("ziprecruiter", {"scrapeLimitZipRecruiter": "22"}, 22),
            ("careerbuilder", {"scrapeLimitCareerBuilder": "24"}, 24),
        ]

        for source, params, expected in cases:
            with self.subTest(source=source):
                result = parse_source_scrape_limit(
                    params,
                    source,  # type: ignore[arg-type]
                    DEFAULT_SOURCE_TARGETS[source],  # type: ignore[arg-type]
                )
                self.assertEqual(result, expected)

    def test_source_limit_supports_max_keyword(self) -> None:
        cases = [
            ("pracujpl", {"scrapeLimitPracuj": "max"}),
            ("rocketjobs", {"scrapeLimitRocket": "max"}),
            ("olxpraca", {"scrapeLimitOlx": "max"}),
            ("indeed", {"indeedLimit": "max"}),
            ("glassdoor", {"glassdoorLimit": "max"}),
            ("ziprecruiter", {"scrapeLimitZip": "max"}),
            ("careerbuilder", {"scrapeLimitCareer": "max"}),
        ]

        for source, params in cases:
            with self.subTest(source=source):
                result = parse_source_scrape_limit(
                    params,
                    source,  # type: ignore[arg-type]
                    DEFAULT_SOURCE_TARGETS[source],  # type: ignore[arg-type]
                )
                self.assertIsNone(result)
