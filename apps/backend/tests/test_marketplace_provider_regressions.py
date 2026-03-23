import json
import unittest
from unittest.mock import AsyncMock, patch

from app.services.search.fetch_with_timeout import FetchResponse
from app.services.search.providers import careerbuilder, ziprecruiter


def _response_with_html(html: str, status: int = 200) -> FetchResponse:
    return FetchResponse(
        status=status,
        content=html.encode("utf-8"),
        headers={},
        set_cookie_headers=[],
    )


def _response_with_json(payload: dict[str, object], status: int = 200) -> FetchResponse:
    return FetchResponse(
        status=status,
        content=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        set_cookie_headers=[],
    )


def _job_posting_html(
    *,
    title: str,
    company: str,
    url: str,
    city: str,
    country: str,
    currency: str = "EUR",
) -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": title,
        "description": f"<p>{title} using React, Node.js, and TypeScript.</p>",
        "url": url,
        "hiringOrganization": {
            "@type": "Organization",
            "name": company,
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": city,
                "addressCountry": country,
            },
        },
        "baseSalary": {
            "@type": "MonetaryAmount",
            "currency": currency,
            "value": {
                "@type": "QuantitativeValue",
                "minValue": 80_000,
                "maxValue": 120_000,
                "unitText": "YEAR",
            },
        },
        "skills": ["React", "Node.js", "TypeScript"],
    }
    return (
        "<html><body>"
        f'<script type="application/ld+json">{json.dumps(posting)}</script>'
        "</body></html>"
    )


class TestCareerBuilderProvider(unittest.IsolatedAsyncioTestCase):
    def test_build_search_url_uses_query_parameters_for_multiword_search(self) -> None:
        self.assertEqual(
            careerbuilder._build_search_url(["react", "node", "typescript"]),
            "https://www.careerbuilder.com/job-listings/search?q=react+node+typescript",
        )

    async def test_scrape_careerbuilder_uses_search_api_results_before_playwright_fallback(
        self,
    ) -> None:
        requested_urls: list[str] = []
        api_payload = {
            "jobResults": [
                {
                    "jobId": "e5e3f67e-02c4-4c72-9f3f-3dc07c674d10",
                    "canonicalUrl": "https://www.careerbuilder.com/job-details/senior-full-stack-react-node-engineer-ut--e5e3f67e-02c4-4c72-9f3f-3dc07c674d10",
                    "jobPosting": {
                        "@type": "JobPosting",
                        "title": "Senior Full-Stack React/Node Engineer",
                        "description": "<p>React, Node.js, TypeScript</p>",
                        "url": "https://www.careerbuilder.com/job-details/senior-full-stack-react-node-engineer-ut--e5e3f67e-02c4-4c72-9f3f-3dc07c674d10?mstr_dist=true",
                        "hiringOrganization": {"@type": "Organization", "name": "ConsultNet"},
                        "jobLocation": {
                            "@type": "Place",
                            "address": {
                                "@type": "PostalAddress",
                                "addressLocality": "Saint George",
                                "addressCountry": "US",
                            },
                        },
                        "baseSalary": {
                            "@type": "MonetaryAmount",
                            "currency": "USD",
                            "value": {
                                "@type": "QuantitativeValue",
                                "minValue": 80_000,
                                "maxValue": 120_000,
                                "unitText": "YEAR",
                            },
                        },
                    },
                },
                {
                    "jobId": "326991a7-12ec-4b5f-b1f8-48ad995f9d25",
                    "canonicalUrl": "https://www.careerbuilder.com/job-details/lead-full-stack-engineer-typescript-javascript-react-node-js-tn--326991a7-12ec-4b5f-b1f8-48ad995f9d25",
                    "jobPosting": {
                        "@type": "JobPosting",
                        "title": "Lead Full Stack Engineer - Typescript, JavaScript, React, Node.js",
                        "description": "<p>React, Node.js, TypeScript</p>",
                        "url": "https://www.careerbuilder.com/job-details/lead-full-stack-engineer-typescript-javascript-react-node-js-tn--326991a7-12ec-4b5f-b1f8-48ad995f9d25?mstr_dist=true",
                        "hiringOrganization": {"@type": "Organization", "name": "Addison Group"},
                        "jobLocation": {
                            "@type": "Place",
                            "address": {
                                "@type": "PostalAddress",
                                "addressLocality": "Nashville",
                                "addressCountry": "US",
                            },
                        },
                        "baseSalary": {
                            "@type": "MonetaryAmount",
                            "currency": "USD",
                            "value": {
                                "@type": "QuantitativeValue",
                                "minValue": 80_000,
                                "maxValue": 120_000,
                                "unitText": "YEAR",
                            },
                        },
                    },
                },
            ]
        }

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
            return _response_with_json(api_payload)

        with (
            patch.object(
                careerbuilder,
                "run_playwright_scraper",
                new=AsyncMock(),
            ),
            patch.object(careerbuilder, "fetch_with_timeout", new=_fake_fetch),
        ):
            result = await careerbuilder.scrape_careerbuilder(
                2,
                keywords=["react", "node", "typescript"],
            )

        self.assertEqual(
            requested_urls,
            [careerbuilder.CAREERBUILDER_SEARCH_API_URL],
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(
            [offer.title for offer in result],
            [
                "Senior Full-Stack React/Node Engineer",
                "Lead Full Stack Engineer - Typescript, JavaScript, React, Node.js",
            ],
        )
        self.assertTrue(all(offer.source == "careerbuilder" for offer in result))


class TestZipRecruiterProvider(unittest.IsolatedAsyncioTestCase):
    def test_build_search_url_uses_ie_listing_pages(self) -> None:
        self.assertEqual(
            ziprecruiter._build_search_url(["react", "node", "typescript"]),
            "https://www.ziprecruiter.ie/jobs/search?q=react+node+typescript",
        )
        self.assertEqual(
            ziprecruiter._build_search_url(["react", "node", "typescript"], page=2),
            "https://www.ziprecruiter.ie/jobs/search?q=react+node+typescript&page=2",
        )

    async def test_scrape_ziprecruiter_paginates_ie_search_results_and_fetches_details(
        self,
    ) -> None:
        search_page_1 = """
        <html><body>
          <a href="/jobs/503728850-senior-fullstack-engineer-typescript-react-node-at-sosafe">First</a>
        </body></html>
        """
        search_page_2 = """
        <html><body>
          <a href="https://www.ziprecruiter.ie/jobs/504127711-senior-software-engineering-react-typescript-at-liberty-information-technology?utm_source=test">Second</a>
        </body></html>
        """
        detail_url_1 = (
            "https://www.ziprecruiter.ie/jobs/503728850-senior-fullstack-engineer-"
            "typescript-react-node-at-sosafe"
        )
        detail_url_2 = (
            "https://www.ziprecruiter.ie/jobs/504127711-senior-software-engineering-"
            "react-typescript-at-liberty-information-technology"
        )
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
            if url == "https://www.ziprecruiter.ie/jobs/search?q=react+node+typescript":
                return _response_with_html(search_page_1)
            if url == "https://www.ziprecruiter.ie/jobs/search?q=react+node+typescript&page=2":
                return _response_with_html(search_page_2)
            if url == detail_url_1:
                return _response_with_html(
                    _job_posting_html(
                        title="Senior Fullstack Engineer - Typescript/React/Node",
                        company="SoSafe",
                        url=detail_url_1,
                        city="Dublin",
                        country="IE",
                    )
                )
            if url == detail_url_2:
                return _response_with_html(
                    _job_posting_html(
                        title="Senior Software Engineering - React | Typescript",
                        company="Liberty Information Technology",
                        url=detail_url_2,
                        city="Galway",
                        country="IE",
                    )
                )
            return _response_with_html("", status=404)

        with patch.object(ziprecruiter, "fetch_with_timeout", new=_fake_fetch):
            result = await ziprecruiter.scrape_ziprecruiter(
                2,
                keywords=["react", "node", "typescript"],
            )

        self.assertEqual(
            requested_urls,
            [
                "https://www.ziprecruiter.ie/jobs/search?q=react+node+typescript",
                "https://www.ziprecruiter.ie/jobs/search?q=react+node+typescript&page=2",
                detail_url_1,
                detail_url_2,
            ],
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(
            [offer.id for offer in result],
            ["503728850", "504127711"],
        )
        self.assertEqual(
            [offer.company for offer in result],
            ["SoSafe", "Liberty Information Technology"],
        )
        self.assertTrue(all(offer.source == "ziprecruiter" for offer in result))
