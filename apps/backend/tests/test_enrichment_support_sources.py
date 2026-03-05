import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.routers import enrichment as enrichment_router
from app.schemas.enrichment import (
    AnswerInput,
    EnhanceRequest,
    EnhanceSupportContext,
    SupportSourceInput,
)


class TestSupportSourceHelpers(unittest.TestCase):
    def test_extract_github_username_from_url_or_username(self) -> None:
        self.assertEqual(
            enrichment_router._extract_github_username("https://github.com/octocat"),
            "octocat",
        )
        self.assertEqual(
            enrichment_router._extract_github_username("github.com/openai"),
            "openai",
        )
        self.assertEqual(
            enrichment_router._extract_github_username("@torvalds"),
            "torvalds",
        )

    def test_extract_github_username_rejects_invalid_input(self) -> None:
        self.assertIsNone(enrichment_router._extract_github_username("https://example.com/user"))
        self.assertIsNone(enrichment_router._extract_github_username("owner/repo"))
        self.assertIsNone(enrichment_router._extract_github_username("bad-"))


class TestSupportSourceContext(unittest.IsolatedAsyncioTestCase):
    async def test_build_supporting_context_combines_sources(self) -> None:
        support_context = EnhanceSupportContext(
            github=SupportSourceInput(
                enabled=True,
                profile="github.com/octocat",
                notes="Built CI/CD tooling for backend services.",
            ),
            linkedin=SupportSourceInput(
                enabled=True,
                profile="linkedin.com/in/octocat",
                notes="Promoted to lead engineer and mentored 4 developers.",
            ),
        )

        with patch.object(
            enrichment_router,
            "_fetch_github_profile_snapshot",
            AsyncMock(return_value="GitHub username: octocat"),
        ):
            context_text = await enrichment_router._build_supporting_context_text(
                support_context
            )

        self.assertIn("GitHub Support", context_text)
        self.assertIn("GitHub username: octocat", context_text)
        self.assertIn("LinkedIn Support", context_text)
        self.assertIn("Promoted to lead engineer", context_text)

    async def test_generate_enhancements_uses_support_context_in_prompt(self) -> None:
        request = EnhanceRequest(
            resume_id="resume_1",
            answers=[
                AnswerInput(
                    question_id="q_0",
                    answer="Reduced API latency by 40% through query optimization.",
                )
            ],
            support_context=EnhanceSupportContext(
                github=SupportSourceInput(enabled=True, profile="octocat"),
                linkedin=SupportSourceInput(
                    enabled=True,
                    notes="Led platform initiatives and improved developer onboarding.",
                ),
            ),
        )

        analysis_result = {
            "questions": [
                {
                    "question_id": "q_0",
                    "item_id": "exp_0",
                    "question": "What measurable impact did you deliver?",
                }
            ],
            "items_to_enrich": [
                {
                    "item_id": "exp_0",
                    "item_type": "experience",
                    "title": "Software Engineer",
                    "subtitle": "Acme Inc",
                    "current_description": ["Built backend APIs."],
                }
            ],
        }
        enhance_result = {
            "additional_bullets": [
                "Reduced API latency by 40% by optimizing query plans and caching."
            ]
        }

        mock_db = MagicMock()
        mock_db.get_resume.return_value = {
            "resume_id": "resume_1",
            "processed_data": {"workExperience": [], "personalProjects": []},
        }

        mock_complete_json = AsyncMock(side_effect=[analysis_result, enhance_result])
        mock_support_context = AsyncMock(
            return_value=(
                "GitHub Support:\nGitHub username: octocat\n\n"
                "LinkedIn Support:\nLed platform initiatives."
            )
        )

        with (
            patch.object(enrichment_router, "db", mock_db),
            patch.object(enrichment_router, "complete_json", mock_complete_json),
            patch.object(
                enrichment_router,
                "_build_supporting_context_text",
                mock_support_context,
            ),
        ):
            response = await enrichment_router.generate_enhancements(request)

        self.assertEqual(len(response.enhancements), 1)
        self.assertEqual(response.enhancements[0].item_id, "exp_0")

        enhance_prompt = mock_complete_json.await_args_list[1].args[0]
        self.assertIn("GitHub Support", enhance_prompt)
        self.assertIn("LinkedIn Support", enhance_prompt)
        mock_support_context.assert_awaited_once()
