import unittest
from unittest.mock import AsyncMock, patch

from app.services.search.offer_generation import (
    OfferJobDescriptionInput,
    _build_fallback_job_description,
    _build_generation_prompt,
    generate_job_description_from_offer,
)


class TestOfferGenerationPrompt(unittest.TestCase):
    def test_build_generation_prompt_includes_company_context_block(self) -> None:
        offer = OfferJobDescriptionInput(
            source="nofluffjobs",
            title="Fullstack Engineer",
            company="Acme",
            location="Warsaw",
            salary="20 000 PLN",
            url="https://jobs.example/acme",
            skills=["React", "Node.js"],
            company_context=(
                "Company summary:\nAcme builds B2B warehouse software.\n\n"
                "Company highlights:\n- Operates across Europe."
            ),
        )

        prompt = _build_generation_prompt(
            offer,
            "Node.js backend, React frontend, hybrid work model.",
        )

        self.assertIn("=== Additional company context ===", prompt)
        self.assertIn("Acme builds B2B warehouse software.", prompt)

    def test_fallback_description_includes_company_context_excerpt(self) -> None:
        offer = OfferJobDescriptionInput(
            source="nofluffjobs",
            title="Fullstack Engineer",
            company="Acme",
            location="Warsaw",
            salary=None,
            url="https://jobs.example/acme",
            skills=["React", "Node.js"],
            company_context="Acme sells workflow software for logistics teams.",
        )

        fallback = _build_fallback_job_description(
            offer,
            "The role covers frontend and backend delivery.",
        )

        self.assertIn("Relevant employer context indicates", fallback)
        self.assertIn("workflow software for logistics teams", fallback)


class TestOfferGenerationPipeline(unittest.IsolatedAsyncioTestCase):
    async def test_generate_job_description_passes_company_context_to_llm_prompt(self) -> None:
        offer = OfferJobDescriptionInput(
            source="nofluffjobs",
            title="Fullstack Engineer",
            company="Acme",
            location="Warsaw",
            salary="20 000 PLN",
            url="https://jobs.example/acme",
            skills=["React", "Node.js"],
            company_context="Acme builds workflow software for logistics teams.",
        )
        captured_prompt: dict[str, str] = {}

        async def _fake_complete(**kwargs: object) -> str:
            captured_prompt["prompt"] = str(kwargs["prompt"])
            return (
                "Acme is hiring a Fullstack Engineer to build React and Node.js features. "
                "The role covers backend APIs, frontend interfaces, and cross-functional delivery. "
                "The posting highlights hybrid work in Warsaw and a salary of 20 000 PLN. "
                "The company builds workflow software for logistics teams and operates in B2B. "
                "Candidates should be comfortable with product-minded engineering and modern web stacks. "
                "The resulting resume should be tailored to both the role requirements and employer context."
            )

        with (
            patch(
                "app.services.search.offer_generation._fetch_offer_source_text",
                AsyncMock(
                    return_value="React frontend, Node.js backend, hybrid work, product delivery."
                ),
            ),
            patch("app.services.search.offer_generation.complete", new=_fake_complete),
        ):
            result = await generate_job_description_from_offer(offer)

        self.assertTrue(result["usedLlm"])
        self.assertIn("=== Additional company context ===", captured_prompt["prompt"])
        self.assertIn("workflow software for logistics teams", captured_prompt["prompt"])
        self.assertIn("Acme is hiring a Fullstack Engineer", result["jobDescription"])
