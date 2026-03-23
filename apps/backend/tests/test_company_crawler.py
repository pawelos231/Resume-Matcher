import unittest
from unittest.mock import AsyncMock, patch

from app.services.search.company_crawler import (
    CompanyCrawlerInput,
    StructuredDocument,
    StructuredSection,
    TextChunk,
    _build_structured_document,
    _chunk_text,
    _extract_offer_page_candidates,
    _retrieve_relevant_chunks,
    generate_company_info_from_offer,
)


class TestCompanyCrawlerStructure(unittest.TestCase):
    def test_build_structured_document_removes_noise_and_extracts_sections(self) -> None:
        html = """
        <html>
          <head>
            <title>Acme Robotics</title>
            <script>window.track = true;</script>
          </head>
          <body>
            <nav><a href="/privacy">Privacy</a></nav>
            <main>
              <h1>Warehouse Automation</h1>
              <p>Acme builds robotics systems for warehouse fulfillment teams.</p>
              <h2>Products</h2>
              <p>The flagship platform combines conveyor control and analytics.</p>
              <table>
                <tr><th>Founded</th><td>2018</td></tr>
              </table>
              <a href="/about">About us</a>
            </main>
            <footer>Footer text</footer>
          </body>
        </html>
        """

        document = _build_structured_document("https://acme.example/", html)

        self.assertEqual(document.title, "Acme Robotics")
        self.assertGreaterEqual(len(document.sections), 2)
        self.assertEqual(document.sections[0].heading, "Warehouse Automation")
        self.assertIn("robotics systems", document.sections[0].text)
        self.assertTrue(
            any("Founded | 2018" in section.text for section in document.sections)
        )
        self.assertEqual([link.url for link in document.links], ["https://acme.example/about"])

    def test_chunk_text_uses_overlap(self) -> None:
        text = " ".join(f"token-{index}" for index in range(900))

        chunks = _chunk_text(text)

        self.assertGreater(len(chunks), 1)
        self.assertIn("token-650", chunks[1])

    def test_offer_page_candidates_ignore_schema_context_urls(self) -> None:
        html = """
        <html>
          <head>
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "hiringOrganization": {
                  "@type": "Organization",
                  "name": "Wakacje.pl",
                  "sameAs": "https://www.wakacje.pl/"
                }
              }
            </script>
          </head>
          <body></body>
        </html>
        """

        candidates = _extract_offer_page_candidates(
            html,
            "https://nofluffjobs.com/pl/job/test",
            "Wakacje.pl",
        )

        self.assertEqual(candidates[0][0], "https://www.wakacje.pl/")
        self.assertFalse(any("schema.org" in candidate_url for candidate_url, _ in candidates))


class TestCompanyCrawlerRetrieval(unittest.IsolatedAsyncioTestCase):
    async def test_retrieval_falls_back_to_lexical_when_embeddings_unavailable(self) -> None:
        offer = CompanyCrawlerInput(
            source="nofluffjobs",
            title="Backend Engineer",
            company="Acme Robotics",
            location="Warsaw",
            salary=None,
            url="https://jobs.example/acme",
            skills=["python", "robotics"],
        )
        chunks = [
            StructuredDocument(
                url="https://acme.example/",
                title="Acme",
                sections=[
                    StructuredSection(
                        heading="About",
                        text="Acme Robotics builds warehouse automation systems in Python.",
                    )
                ],
            ),
            StructuredDocument(
                url="https://acme.example/careers",
                title="Careers",
                sections=[
                    StructuredSection(
                        heading="Benefits",
                        text="The company offers pension benefits and a dog-friendly office.",
                    )
                ],
            ),
        ]

        flattened_chunks: list[TextChunk] = []
        for document in chunks:
            for section in document.sections:
                flattened_chunks.append(
                    TextChunk(
                        url=document.url,
                        title=document.title,
                        heading=section.heading,
                        text=section.text,
                    )
                )

        with patch("app.services.search.company_crawler.embed_texts", AsyncMock(return_value=None)):
            selected, method = await _retrieve_relevant_chunks(
                flattened_chunks,
                offer,
                "Summarize the company's products and technology.",
            )

        self.assertEqual(method, "lexical")
        self.assertEqual(selected[0].url, "https://acme.example/")


class TestCompanyCrawlerPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_generate_company_info_from_offer_returns_expected_shape(self) -> None:
        offer = CompanyCrawlerInput(
            source="nofluffjobs",
            title="Backend Engineer",
            company="Acme Robotics",
            location="Warsaw",
            salary=None,
            url="https://jobs.example/acme",
            skills=["python", "robotics"],
        )
        crawled_documents = [
            StructuredDocument(
                url="https://acme.example/",
                title="Acme Robotics",
                sections=[
                    StructuredSection(
                        heading="Overview",
                        text="Acme Robotics builds warehouse automation systems for fulfillment operators.",
                    )
                ],
            )
        ]

        with (
            patch(
                "app.services.search.company_crawler._resolve_company_website",
                AsyncMock(return_value=("https://acme.example/", "search_engine", None)),
            ),
            patch(
                "app.services.search.company_crawler._crawl_company_site",
                AsyncMock(return_value=crawled_documents),
            ),
            patch(
                "app.services.search.company_crawler._extract_company_info_with_llm",
                AsyncMock(
                    return_value=(
                        "Acme Robotics builds warehouse automation products.",
                        ["Warehouse automation platform"],
                        [
                            {
                                "url": "https://acme.example/",
                                "title": "Acme Robotics",
                                "snippet": "Builds warehouse automation products.",
                            }
                        ],
                        True,
                    )
                ),
            ),
            patch("app.services.search.company_crawler.embed_texts", AsyncMock(return_value=None)),
        ):
            result = await generate_company_info_from_offer(offer)

        self.assertEqual(result["company"], "Acme Robotics")
        self.assertEqual(result["websiteUrl"], "https://acme.example/")
        self.assertEqual(result["websiteFoundVia"], "search_engine")
        self.assertEqual(result["stats"]["pagesVisited"], 1)
        self.assertEqual(result["stats"]["relevantChunks"], 1)
        self.assertTrue(result["stats"]["usedLlm"])
