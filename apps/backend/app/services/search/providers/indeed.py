"""Indeed scraper."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote_plus

from app.services.search.providers.playwright_runtime import run_playwright_scraper
from app.services.search.providers.searchable_text import extract_searchable_text
from app.services.search.types import ScrapedOffer

INDEED_BASE_URL = "https://www.indeed.com"
DEFAULT_SEARCH_QUERY = "software engineer"
LISTING_READY_TIMEOUT_MS = 10_000
PAGE_STABILIZATION_DELAY_MS = 1_000

ProgressHandler = Callable[[dict[str, float | int]], None]

_EXTRACTION_SCRIPT = r"""
() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const cards = Array.from(document.querySelectorAll('[data-testid="slider_item"]'));
  const looksLikeSalary = (value) => {
    const text = clean(value);
    return (
      !!text &&
      text.length < 140 &&
      (text.includes('$') ||
        text.includes('£') ||
        text.includes('€') ||
        /\b(per hour|an hour|a year|a month)\b/i.test(text))
    );
  };

  return cards.map((card, index) => {
    const link =
      card.querySelector('h2 a[id^="job_"]') ||
      card.querySelector('h2 a[id^="sj_"]');
    const company = card.querySelector('[data-testid="company-name"]');
    const location = card.querySelector('[data-testid="text-location"]');
    const snippet = card.querySelector('[data-testid="belowJobSnippet"]');
    const cardText = clean(card.innerText);
    const salary =
      Array.from(card.querySelectorAll('*'))
        .map((element) => clean(element.textContent))
        .find(looksLikeSalary) || '';
    const jobKey =
      link?.getAttribute('data-jk') ||
      link?.dataset?.jk ||
      link?.id?.replace(/^(job_|sj_)/, '') ||
      '';

    return {
      index,
      id: clean(jobKey || `indeed-${index}`),
      title: clean(link?.textContent),
      company: clean(company?.textContent),
      location: clean(location?.textContent),
      salary: clean(salary),
      url: jobKey
        ? `https://www.indeed.com/viewjob?jk=${jobKey}`
        : clean(link?.href || ''),
      searchableText: clean(snippet?.textContent || cardText),
      cardText,
    };
  });
}
"""


def _build_query(keywords: list[str] | None) -> str:
    query = " ".join(keyword.strip() for keyword in (keywords or []) if keyword.strip())
    normalized = " ".join(query.split())
    return normalized or DEFAULT_SEARCH_QUERY


def _build_search_url(keywords: list[str] | None) -> str:
    return f"{INDEED_BASE_URL}/jobs?q={quote_plus(_build_query(keywords))}"


def _normalize_offer(payload: dict[str, Any]) -> ScrapedOffer | None:
    title = str(payload.get("title") or "").strip()
    url = str(payload.get("url") or "").strip()
    if not title or not url:
        return None

    company = str(payload.get("company") or "").strip() or "Unknown company"
    location = str(payload.get("location") or "").strip()
    salary = str(payload.get("salary") or "").strip() or None
    searchable_text = extract_searchable_text(
        title,
        company,
        location,
        salary or "",
        payload.get("searchableText"),
        payload.get("cardText"),
    )

    return ScrapedOffer(
        id=str(payload.get("id") or "").strip() or "indeed",
        source="indeed",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=url,
        skills=[],
        searchable_text=searchable_text,
    )


async def scrape_indeed(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
    *,
    keywords: list[str] | None = None,
) -> list[ScrapedOffer]:
    """Scrape Indeed offers for the current keyword query."""

    async def _runner(
        context: Any,
        stop_requested: Callable[[], bool] | None,
        emit_progress: ProgressHandler | None,
    ) -> list[ScrapedOffer]:
        page = await context.new_page()
        try:
            await page.goto(
                _build_search_url(keywords),
                wait_until="domcontentloaded",
                timeout=LISTING_READY_TIMEOUT_MS,
            )
            await page.wait_for_selector(
                '[data-testid="slider_item"]',
                timeout=LISTING_READY_TIMEOUT_MS,
            )
            await page.wait_for_timeout(PAGE_STABILIZATION_DELAY_MS)

            raw_items = await page.evaluate(_EXTRACTION_SCRIPT)
            if not isinstance(raw_items, list):
                raise RuntimeError("Indeed returned an invalid listing payload")

            offers: list[ScrapedOffer] = []
            target_total = len(raw_items) if target_count is None else min(len(raw_items), target_count)

            for index, raw_item in enumerate(raw_items):
                if stop_requested and stop_requested():
                    break
                if target_count is not None and len(offers) >= target_count:
                    break
                if not isinstance(raw_item, dict):
                    continue
                offer = _normalize_offer(raw_item)
                if offer is None:
                    continue
                offers.append(offer)
                if emit_progress:
                    progress = min((index + 1) / max(target_total, 1), 1.0)
                    emit_progress({"collected": len(offers), "progress": progress})

            return offers if target_count is None else offers[:target_count]
        finally:
            await page.close()

    return await run_playwright_scraper(
        "Indeed",
        _runner,
        locale="en-US",
        on_progress=on_progress,
    )
