import unittest
from pathlib import Path

from app.prompts.templates import get_language_name

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _read_backend_file(relative_path: str) -> str:
    return (BACKEND_ROOT / relative_path).read_text(encoding="utf-8")


class TestLanguageSupport(unittest.TestCase):
    def test_supported_languages_include_polish(self) -> None:
        config_source = _read_backend_file("app/routers/config.py")

        self.assertIn('SUPPORTED_LANGUAGES = ["en", "es", "zh", "ja", "pt", "pl"]', config_source)

    def test_polish_language_name_is_exposed_for_prompts(self) -> None:
        self.assertEqual(get_language_name("pl"), "Polish")

    def test_language_config_response_defaults_include_polish(self) -> None:
        models_source = _read_backend_file("app/schemas/models.py")

        self.assertIn('"pl"', models_source)
