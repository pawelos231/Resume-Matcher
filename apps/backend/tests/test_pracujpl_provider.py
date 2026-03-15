import unittest
from unittest.mock import patch

from app.services.search.providers import pracujpl
from app.services.search.types import ScrapedOffer


def _offer() -> ScrapedOffer:
    return ScrapedOffer(
        id="pracuj-1",
        source="pracujpl",
        title="Python Developer",
        company="Acme",
        location="Warsaw",
        salary=None,
        url="https://www.pracuj.pl/praca/python-developer,oferta,123",
        skills=[],
        searchable_text="Python Developer",
    )


class _BrokenPlaywrightContext:
    async def __aenter__(self) -> None:
        raise NotImplementedError()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> bool:
        _ = exc_type
        _ = exc
        _ = tb
        return False


class TestPracujPlProvider(unittest.IsolatedAsyncioTestCase):
    async def test_scrape_pracujpl_translates_missing_subprocess_support_into_clear_error(
        self,
    ) -> None:
        with (
            patch.object(pracujpl.sys, "platform", "linux"),
            patch(
                "app.services.search.providers.pracujpl.async_playwright",
                return_value=_BrokenPlaywrightContext(),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "event loop does not support subprocesses",
            ):
                await pracujpl.scrape_pracujpl(1)

    async def test_scrape_pracujpl_uses_worker_thread_path_on_windows(self) -> None:
        progress_events: list[dict[str, float | int]] = []
        expected_offer = _offer()

        def _fake_worker(
            target_count: int | None,
            on_progress: pracujpl.ProgressHandler | None = None,
            stop_requested: pracujpl.StopRequestedHandler | None = None,
        ) -> list[ScrapedOffer]:
            self.assertEqual(target_count, 1)
            self.assertIsNotNone(stop_requested)
            self.assertFalse(stop_requested())
            if on_progress is not None:
                on_progress({"collected": 1, "progress": 0.5})
            return [expected_offer]

        with (
            patch.object(pracujpl.sys, "platform", "win32"),
            patch(
                "app.services.search.providers.pracujpl._run_pracujpl_in_worker_thread",
                new=_fake_worker,
            ),
        ):
            result = await pracujpl.scrape_pracujpl(1, progress_events.append)

        self.assertEqual(result, [expected_offer])
        self.assertEqual(progress_events, [{"collected": 1, "progress": 0.5}])
