import unittest
from unittest.mock import AsyncMock, patch

from app.services.search.fetch_with_timeout import FetchResponse, fetch_with_timeout


def _response(status: int) -> FetchResponse:
    return FetchResponse(
        status=status,
        content=b"{}",
        headers={},
        set_cookie_headers=[],
    )


class TestFetchWithTimeout(unittest.IsolatedAsyncioTestCase):
    async def test_retries_transient_502_until_success(self) -> None:
        sleep_mock = AsyncMock()

        with (
            patch(
                "app.services.search.fetch_with_timeout._sync_fetch",
                side_effect=[_response(502), _response(200)],
            ) as sync_fetch_mock,
            patch("app.services.search.fetch_with_timeout.asyncio.sleep", new=sleep_mock),
        ):
            result = await fetch_with_timeout("https://example.com")

        self.assertEqual(result.status, 200)
        self.assertEqual(sync_fetch_mock.call_count, 2)
        sleep_mock.assert_awaited_once()

    async def test_returns_last_transient_response_after_retries(self) -> None:
        sleep_mock = AsyncMock()
        side_effect = [_response(503)] * 6

        with (
            patch(
                "app.services.search.fetch_with_timeout._sync_fetch",
                side_effect=side_effect,
            ) as sync_fetch_mock,
            patch("app.services.search.fetch_with_timeout.asyncio.sleep", new=sleep_mock),
        ):
            result = await fetch_with_timeout("https://example.com")

        self.assertEqual(result.status, 503)
        self.assertEqual(sync_fetch_mock.call_count, 6)
        self.assertEqual(sleep_mock.await_count, 5)
