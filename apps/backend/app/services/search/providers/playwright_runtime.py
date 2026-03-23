"""Shared Playwright runtime helpers for JS-heavy job-board providers."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Playwright,
    async_playwright,
)

T = TypeVar("T")
ProgressHandler = Callable[[dict[str, float | int]], None]
StopRequestedHandler = Callable[[], bool]
BrowserRunner = Callable[
    [BrowserContext, StopRequestedHandler | None, ProgressHandler | None],
    Awaitable[T],
]

REQUEST_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)


def _find_browser_executable() -> str | None:
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
            / "Microsoft/Edge/Application/msedge.exe",
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    else:
        candidates = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/microsoft-edge"),
            Path("/snap/bin/chromium"),
        ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


async def _launch_browser(playwright: Playwright, provider_label: str) -> Browser:
    try:
        return await playwright.chromium.launch(headless=True)
    except NotImplementedError as exc:
        raise RuntimeError(
            f"{provider_label} scraping could not start Chromium because the current "
            "asyncio event loop does not support subprocesses."
        ) from exc
    except PlaywrightError as exc:
        fallback_executable = _find_browser_executable()
        if fallback_executable:
            return await playwright.chromium.launch(
                executable_path=fallback_executable,
                headless=True,
            )
        raise RuntimeError(
            f"{provider_label} scraping requires a Chromium browser. Install "
            "Playwright browsers or make Chrome/Edge available on this machine."
        ) from exc


async def _prepare_context(browser: Browser, locale: str) -> BrowserContext:
    context = await browser.new_context(
        locale=locale,
        user_agent=REQUEST_USER_AGENT,
        viewport={"width": 1440, "height": 2200},
    )

    async def _route_assets(route: Any) -> None:
        if route.request.resource_type in {"image", "media", "font"}:
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", _route_assets)
    return context


async def _run_browser_session(
    provider_label: str,
    locale: str,
    runner: BrowserRunner[T],
    *,
    on_progress: ProgressHandler | None = None,
    stop_requested: StopRequestedHandler | None = None,
) -> T:
    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright, provider_label)
        context = await _prepare_context(browser, locale)
        try:
            return await runner(context, stop_requested, on_progress)
        finally:
            await context.close()
            await browser.close()


def _run_browser_session_in_worker_thread(
    provider_label: str,
    locale: str,
    runner: BrowserRunner[T],
    *,
    on_progress: ProgressHandler | None = None,
    stop_requested: StopRequestedHandler | None = None,
) -> T:
    if sys.platform == "win32":
        loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
    else:
        loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            _run_browser_session(
                provider_label,
                locale,
                runner,
                on_progress=on_progress,
                stop_requested=stop_requested,
            )
        )
    finally:
        with contextlib.suppress(Exception):
            loop.run_until_complete(loop.shutdown_asyncgens())
        asyncio.set_event_loop(None)
        loop.close()


async def run_playwright_scraper(
    provider_label: str,
    runner: BrowserRunner[T],
    *,
    locale: str = "en-US",
    on_progress: ProgressHandler | None = None,
) -> T:
    """Run a Playwright-backed scraper with a Windows-safe worker-thread fallback."""
    if sys.platform != "win32":
        return await _run_browser_session(
            provider_label,
            locale,
            runner,
            on_progress=on_progress,
        )

    loop = asyncio.get_running_loop()
    completion_future: asyncio.Future[T] = loop.create_future()
    stop_requested = threading.Event()

    def _set_result(result: T) -> None:
        if not completion_future.done():
            completion_future.set_result(result)

    def _set_exception(exc: BaseException) -> None:
        if not completion_future.done():
            completion_future.set_exception(exc)

    def _thread_progress(event: dict[str, float | int]) -> None:
        if on_progress is None:
            return
        loop.call_soon_threadsafe(on_progress, event)

    def _worker() -> None:
        try:
            result = _run_browser_session_in_worker_thread(
                provider_label,
                locale,
                runner,
                on_progress=_thread_progress if on_progress is not None else None,
                stop_requested=stop_requested.is_set,
            )
        except BaseException as exc:
            loop.call_soon_threadsafe(_set_exception, exc)
        else:
            loop.call_soon_threadsafe(_set_result, result)

    worker_thread = threading.Thread(
        target=_worker,
        name=f"{provider_label.lower()}-scraper",
        daemon=True,
    )
    worker_thread.start()

    try:
        return await asyncio.shield(completion_future)
    except asyncio.CancelledError:
        stop_requested.set()
        return await asyncio.shield(completion_future)
