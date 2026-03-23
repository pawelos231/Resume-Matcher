import unittest

from app.services.resume_categorization import categorize_resume_record, categorize_resume_text


class TestResumeCategorization(unittest.TestCase):
    def test_detects_frontend_resume_from_processed_data(self) -> None:
        categories, primary_category = categorize_resume_record(
            {
                "processed_data": {
                    "summary": "Frontend engineer building design systems and accessible apps.",
                    "additional": {
                        "technicalSkills": ["React", "Next.js", "Tailwind CSS"],
                    },
                },
                "content": "",
            }
        )

        self.assertEqual(primary_category, "frontend")
        self.assertIn("frontend", categories)

    def test_detects_backend_resume_from_raw_markdown(self) -> None:
        categories, primary_category = categorize_resume_record(
            {
                "processed_data": None,
                "content": "Backend Engineer\nBuilt REST API services with FastAPI and PostgreSQL.",
            }
        )

        self.assertEqual(primary_category, "backend")
        self.assertIn("backend", categories)

    def test_marks_resume_as_fullstack_when_frontend_and_backend_overlap(self) -> None:
        categories, primary_category = categorize_resume_text(
            "Full stack engineer working with React, Next.js, FastAPI, PostgreSQL and Redis."
        )

        self.assertEqual(primary_category, "fullstack")
        self.assertEqual(categories[0], "fullstack")
        self.assertIn("frontend", categories)
        self.assertIn("backend", categories)

    def test_falls_back_to_uncategorized_when_no_known_keywords_exist(self) -> None:
        categories, primary_category = categorize_resume_text(
            "Operations specialist focused on stakeholder communication and reporting."
        )

        self.assertEqual(categories, ["uncategorized"])
        self.assertEqual(primary_category, "uncategorized")
