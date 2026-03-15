"""Pracuj.pl scraper."""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.services.search.providers.searchable_text import extract_searchable_text
from app.services.search.types import ScrapedOffer

PRACUJ_LISTING_URL = "https://www.pracuj.pl/praca/it%3Bkw"
MAX_SCRAPE_PAGES = 200
PAGE_READY_TIMEOUT_MS = 45_000
PAGE_STABILIZATION_DELAY_MS = 2_000

REQUEST_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)

ProgressHandler = Callable[[dict[str, float | int]], None]
StopRequestedHandler = Callable[[], bool]

_EXTRACTION_SCRIPT = """
() => {
  const cleanText = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const containers = Array.from(
    document.querySelectorAll(
      '[data-test="positioned-offer"], [data-test="default-offer"], [data-test="promoted-offer"]'
    )
  );

  const offers = containers
    .map((container) => {
      const titleLink = container.querySelector('[data-test="link-offer-title"]');
      if (!titleLink) {
        return null;
      }

      const additionalInfo = Array.from(
        container.querySelectorAll('[data-test^="offer-additional-info-"]')
      )
        .map((node) => cleanText(node.textContent))
        .filter(Boolean);

      const tags = [
        cleanText(container.querySelector('[data-test="text-super-offer"]')?.textContent),
        cleanText(container.querySelector('[data-test="text-one-click-apply"]')?.textContent),
        cleanText(container.querySelector('[data-test="promoted-text"]')?.textContent),
      ].filter(Boolean);

      return {
        title: cleanText(titleLink.textContent),
        url: titleLink.href || '',
        company: cleanText(
          container.querySelector('[data-test="text-company-name"]')?.textContent
        ),
        location: cleanText(
          container.querySelector('[data-test="text-region"]')?.textContent
        ),
        salary: cleanText(
          container.querySelector('[data-test="offer-salary"]')?.textContent
        ) || null,
        additionalInfo,
        tags,
        rawText: cleanText(container.innerText),
      };
    })
    .filter(Boolean);

  const maxPageText =
    cleanText(document.querySelector('[data-test="top-pagination-max-page-number"]')?.textContent) ||
    cleanText(
      document.querySelector('[data-test="bottom-pagination-mobile-max-page-number"]')?.textContent
    ) ||
    '1';

  return { maxPageText, offers };
}
"""


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _build_page_url(page_number: int) -> str:
    if page_number <= 1:
        return PRACUJ_LISTING_URL
    return f"{PRACUJ_LISTING_URL}?pn={page_number}"


def _extract_offer_id(url: str, fallback: str) -> str:
    match = re.search(r",oferta,(\d+)", url)
    if match:
        return match.group(1)
    return fallback


def _normalize_offer_url(url: str) -> str:
    stripped = url.strip()
    if not stripped:
        return ""

    parsed = urlsplit(stripped)
    if not parsed.scheme or not parsed.netloc:
        return stripped

    normalized_path = re.sub(r"/+$", "", parsed.path) or parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _parse_max_page(raw_value: str | None) -> int:
    digits = re.sub(r"[^\d]", "", raw_value or "")
    if not digits:
        return 1
    try:
        return max(int(digits), 1)
    except ValueError:
        return 1


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


async def _launch_browser(playwright: Playwright) -> Browser:
    try:
        return await playwright.chromium.launch(headless=True)
    except NotImplementedError as exc:
        raise RuntimeError(
            "Pracuj.pl scraping could not start Chromium because the current asyncio "
            "event loop does not support subprocesses."
        ) from exc
    except PlaywrightError as exc:
        fallback_executable = _find_browser_executable()
        if fallback_executable:
            return await playwright.chromium.launch(
                executable_path=fallback_executable,
                headless=True,
            )
        raise RuntimeError(
            "Pracuj.pl scraping requires a Chromium browser. Install Playwright browsers "
            "or make Chrome/Edge available on this machine."
        ) from exc


async def _prepare_context(browser: Browser) -> BrowserContext:
    context = await browser.new_context(
        locale="pl-PL",
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


def _should_stop(stop_requested: StopRequestedHandler | None) -> bool:
    return stop_requested is not None and stop_requested()


async def _load_page(page: Page, page_number: int) -> dict[str, Any]:
    await page.goto(
        _build_page_url(page_number),
        wait_until="domcontentloaded",
        timeout=PAGE_READY_TIMEOUT_MS,
    )

    try:
        await page.wait_for_selector(
            '[data-test="link-offer-title"]',
            timeout=PAGE_READY_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError as exc:
        title = await page.title()
        raise RuntimeError(
            f"Pracuj.pl page {page_number} did not render offer listings (page title: {title})"
        ) from exc

    await page.wait_for_timeout(PAGE_STABILIZATION_DELAY_MS)
    payload = await page.evaluate(_EXTRACTION_SCRIPT)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Pracuj.pl page {page_number} returned an invalid payload")
    return payload


def _normalize_offer(raw_offer: dict[str, Any], index: int) -> ScrapedOffer | None:
    title = _clean_text(str(raw_offer.get("title") or ""))
    url = _normalize_offer_url(str(raw_offer.get("url") or ""))
    if not title or not url:
        return None

    company = _clean_text(str(raw_offer.get("company") or "Unknown company"))
    location = _clean_text(str(raw_offer.get("location") or ""))
    salary_value = _clean_text(str(raw_offer.get("salary") or ""))
    salary = salary_value or None

    additional_info: list[str] = []
    for item in raw_offer.get("additionalInfo") or []:
        normalized = _clean_text(str(item or ""))
        if normalized and normalized not in additional_info:
            additional_info.append(normalized)

    tags: list[str] = []
    for item in raw_offer.get("tags") or []:
        normalized = _clean_text(str(item or ""))
        if normalized and normalized not in tags:
            tags.append(normalized)

    searchable_text = extract_searchable_text(raw_offer, additional_info, tags)

    return ScrapedOffer(
        id=_extract_offer_id(url, f"pracujpl-{index}"),
        source="pracujpl",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=url,
        skills=[],
        searchable_text=searchable_text,
    )


async def _scrape_pracujpl_async(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
    stop_requested: StopRequestedHandler | None = None,
) -> list[ScrapedOffer]:
    """Scrape Pracuj.pl IT offers using the current event loop."""
    offers: list[ScrapedOffer] = []
    try:
        async with async_playwright() as playwright:
            browser = await _launch_browser(playwright)
            context = await _prepare_context(browser)

            try:
                seen_urls: set[str] = set()

                try:
                    if _should_stop(stop_requested):
                        return []

                    first_page = await context.new_page()
                    try:
                        first_payload = await _load_page(first_page, 1)
                    finally:
                        await first_page.close()

                    raw_first_page_offers = first_payload.get("offers")
                    if not isinstance(raw_first_page_offers, list):
                        raw_first_page_offers = []

                    extracted_first_page = [
                        offer
                        for index, raw_offer in enumerate(raw_first_page_offers)
                        if isinstance(raw_offer, dict)
                        for offer in [_normalize_offer(raw_offer, index)]
                        if offer is not None
                    ]

                    unique_first_page: list[ScrapedOffer] = []
                    for offer in extracted_first_page:
                        key = offer.url or offer.id
                        if key in seen_urls:
                            continue
                        seen_urls.add(key)
                        unique_first_page.append(offer)

                    offers.extend(unique_first_page)
                    if target_count is not None and len(offers) >= target_count:
                        offers = offers[:target_count]

                    total_pages = min(
                        _parse_max_page(str(first_payload.get("maxPageText") or "1")),
                        MAX_SCRAPE_PAGES,
                    )
                    offers_per_page = max(len(unique_first_page), 1)
                    page_limit = total_pages
                    if target_count is not None:
                        page_limit = min(
                            total_pages,
                            max(1, math.ceil(target_count / offers_per_page)),
                        )

                    if on_progress:
                        progress = (
                            min(1 / max(page_limit, 1), 1.0)
                            if target_count is None
                            else min(len(offers) / max(target_count, 1), 1.0)
                        )
                        on_progress({"collected": len(offers), "progress": progress})

                    if _should_stop(stop_requested):
                        return offers if target_count is None else offers[:target_count]

                    for page_number in range(2, page_limit + 1):
                        if target_count is not None and len(offers) >= target_count:
                            break
                        if _should_stop(stop_requested):
                            break

                        page = await context.new_page()
                        try:
                            payload = await _load_page(page, page_number)
                        finally:
                            await page.close()

                        raw_page_offers = payload.get("offers")
                        if not isinstance(raw_page_offers, list):
                            raw_page_offers = []

                        for raw_index, raw_offer in enumerate(raw_page_offers):
                            if target_count is not None and len(offers) >= target_count:
                                break
                            if _should_stop(stop_requested):
                                break
                            if not isinstance(raw_offer, dict):
                                continue
                            offer = _normalize_offer(
                                raw_offer,
                                (page_number - 1) * offers_per_page + raw_index,
                            )
                            if offer is None:
                                continue
                            key = offer.url or offer.id
                            if key in seen_urls:
                                continue
                            seen_urls.add(key)
                            offers.append(offer)

                        if on_progress:
                            progress = (
                                min(page_number / max(page_limit, 1), 1.0)
                                if target_count is None
                                else min(len(offers) / max(target_count, 1), 1.0)
                            )
                            on_progress({"collected": len(offers), "progress": progress})
                except asyncio.CancelledError:
                    return offers if target_count is None else offers[:target_count]

                result = offers if target_count is None else offers[:target_count]
                if on_progress:
                    on_progress({"collected": len(result), "progress": 1.0})
                return result
            finally:
                await context.close()
                await browser.close()
    except NotImplementedError as exc:
        raise RuntimeError(
            "Pracuj.pl scraping could not start Playwright because the current asyncio "
            "event loop does not support subprocesses."
        ) from exc


def _run_pracujpl_in_worker_thread(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
    stop_requested: StopRequestedHandler | None = None,
) -> list[ScrapedOffer]:
    if sys.platform == "win32":
        loop = asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
    else:
        loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            _scrape_pracujpl_async(
                target_count,
                on_progress=on_progress,
                stop_requested=stop_requested,
            )
        )
    finally:
        with contextlib.suppress(Exception):
            loop.run_until_complete(loop.shutdown_asyncgens())
        asyncio.set_event_loop(None)
        loop.close()


async def _scrape_pracujpl_via_worker_thread(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
) -> list[ScrapedOffer]:
    loop = asyncio.get_running_loop()
    completion_future: asyncio.Future[list[ScrapedOffer]] = loop.create_future()
    stop_requested = threading.Event()

    def _set_result(result: list[ScrapedOffer]) -> None:
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
            result = _run_pracujpl_in_worker_thread(
                target_count,
                on_progress=_thread_progress if on_progress is not None else None,
                stop_requested=stop_requested.is_set,
            )
        except BaseException as exc:
            loop.call_soon_threadsafe(_set_exception, exc)
        else:
            loop.call_soon_threadsafe(_set_result, result)

    worker_thread = threading.Thread(
        target=_worker,
        name="pracujpl-scraper",
        daemon=True,
    )
    worker_thread.start()

    try:
        return await asyncio.shield(completion_future)
    except asyncio.CancelledError:
        stop_requested.set()
        return await asyncio.shield(completion_future)


async def scrape_pracujpl(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
) -> list[ScrapedOffer]:
    """Scrape Pracuj.pl IT offers."""
    if sys.platform == "win32":
        return await _scrape_pracujpl_via_worker_thread(
            target_count,
            on_progress=on_progress,
        )

    return await _scrape_pracujpl_async(
        target_count,
        on_progress=on_progress,
    )
