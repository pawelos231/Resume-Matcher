"""HTTP helper with timeout and 429 retry behavior for scrapers."""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_FETCH_TIMEOUT_MS = 15_000
RETRY_ON_429_DELAYS_S = (0.25, 0.5, 1.0, 2.0, 5.0)


@dataclass(slots=True)
class FetchResponse:
    """Simplified HTTP response object for scraper calls."""

    status: int
    content: bytes
    headers: dict[str, str]
    set_cookie_headers: list[str]

    @property
    def text(self) -> str:
        """Decode response body as UTF-8 with replacement."""
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """Parse response body as JSON."""
        return json.loads(self.text)


def _to_url_label(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        return url
    return f"https://{url}"


def _headers_to_dict(headers: Message) -> dict[str, str]:
    output: dict[str, str] = {}
    for key, value in headers.items():
        output[key.lower()] = value
    return output


def _extract_set_cookie(headers: Message) -> list[str]:
    set_cookie = headers.get_all("Set-Cookie")
    if not set_cookie:
        return []
    return [value for value in set_cookie if value]


def _sync_fetch(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None,
    body: bytes | None,
    timeout_ms: int,
) -> FetchResponse:
    request = Request(
        url=url,
        data=body,
        headers=headers or {},
        method=method.upper(),
    )

    timeout_s = max(timeout_ms / 1000.0, 0.001)

    try:
        with urlopen(request, timeout=timeout_s) as response:
            raw_headers = response.headers
            return FetchResponse(
                status=int(response.status),
                content=response.read(),
                headers=_headers_to_dict(raw_headers),
                set_cookie_headers=_extract_set_cookie(raw_headers),
            )
    except HTTPError as exc:
        raw_headers = exc.headers or Message()
        return FetchResponse(
            status=int(exc.code),
            content=exc.read(),
            headers=_headers_to_dict(raw_headers),
            set_cookie_headers=_extract_set_cookie(raw_headers),
        )
    except (URLError, socket.timeout, TimeoutError) as exc:
        raise RuntimeError(
            f"Request failed for {_to_url_label(url)} after {timeout_ms} ms: {exc}"
        ) from exc


async def fetch_with_timeout(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout_ms: int = DEFAULT_FETCH_TIMEOUT_MS,
) -> FetchResponse:
    """Perform HTTP request with timeout and retry on 429."""
    for retry_index in range(len(RETRY_ON_429_DELAYS_S) + 1):
        response = await asyncio.to_thread(
            _sync_fetch,
            url,
            method=method,
            headers=headers,
            body=body,
            timeout_ms=timeout_ms,
        )

        if response.status != 429 or retry_index >= len(RETRY_ON_429_DELAYS_S):
            return response

        await asyncio.sleep(RETRY_ON_429_DELAYS_S[retry_index])

    raise RuntimeError(f"Retry loop exhausted unexpectedly for {_to_url_label(url)}")

