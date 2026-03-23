"""Glassdoor scraper."""

from __future__ import annotations

import re
from typing import Any, Callable

from app.services.search.providers.playwright_runtime import run_playwright_scraper
from app.services.search.providers.searchable_text import extract_searchable_text
from app.services.search.types import ScrapedOffer

GLASSDOOR_BASE_URL = "https://www.glassdoor.com"
DEFAULT_SEARCH_QUERY = "software engineer"
LISTING_READY_TIMEOUT_MS = 10_000
PAGE_STABILIZATION_DELAY_MS = 1_000

ProgressHandler = Callable[[dict[str, float | int]], None]

_EXTRACTION_SCRIPT = r"""
() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const parseSkills = (text) => {
    const match = clean(text).match(/Skills:\s*(.*)$/i);
    if (!match) {
      return [];
    }
    return match[1]
      .split(',')
      .map((item) => clean(item))
      .filter(Boolean)
      .slice(0, 12);
  };

  return Array.from(document.querySelectorAll('[data-test="jobListing"]')).map((card, index) => {
    const titleLink = card.querySelector('[data-test="job-title"]');
    const employer = card.querySelector('[id^="job-employer-"] span');
    const location = card.querySelector('[data-test="emp-location"]');
    const salary = card.querySelector('[data-test="detailSalary"]');
    const snippet = card.querySelector('[data-test="descSnippet"]');
    const text = clean(card.innerText);
    const cardJobId = clean(card.getAttribute('data-jobid') || `glassdoor-${index}`);
    const snippetText = clean(snippet?.textContent || '');

    return {
      index,
      id: cardJobId,
      title: clean(titleLink?.textContent),
      company: clean(employer?.textContent),
      location: clean(location?.textContent),
      salary: clean(salary?.textContent),
      url: clean(titleLink?.href || ''),
      searchableText: snippetText,
      skills: parseSkills(snippetText),
      cardText: text,
    };
  });
}
"""


def _build_query(keywords: list[str] | None) -> str:
    query = " ".join(keyword.strip() for keyword in (keywords or []) if keyword.strip())
    normalized = " ".join(query.split())
    return normalized or DEFAULT_SEARCH_QUERY


def _slugify_query(query: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")


def _build_search_url(keywords: list[str] | None) -> str:
    query = _build_query(keywords)
    slug = _slugify_query(query)
    return f"{GLASSDOOR_BASE_URL}/Job/{slug}-jobs-SRCH_KO0,{len(query)}.htm"


def _normalize_offer(payload: dict[str, Any]) -> ScrapedOffer | None:
    title = str(payload.get("title") or "").strip()
    url = str(payload.get("url") or "").strip()
    if not title or not url:
        return None

    company = str(payload.get("company") or "").strip() or "Unknown company"
    location = str(payload.get("location") or "").strip()
    salary = str(payload.get("salary") or "").strip() or None
    skills = [
        str(skill).strip()
        for skill in payload.get("skills") or []
        if str(skill).strip()
    ][:12]
    searchable_text = extract_searchable_text(
        title,
        company,
        location,
        salary or "",
        skills,
        payload.get("searchableText"),
        payload.get("cardText"),
    )

    return ScrapedOffer(
        id=str(payload.get("id") or "").strip() or "glassdoor",
        source="glassdoor",
        title=title,
        company=company,
        location=location,
        salary=salary,
        url=url,
        skills=skills,
        searchable_text=searchable_text,
    )


async def scrape_glassdoor(
    target_count: int | None,
    on_progress: ProgressHandler | None = None,
    *,
    keywords: list[str] | None = None,
) -> list[ScrapedOffer]:
    """Scrape Glassdoor offers for the current keyword query."""

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
                '[data-test="jobListing"]',
                timeout=LISTING_READY_TIMEOUT_MS,
            )
            await page.wait_for_timeout(PAGE_STABILIZATION_DELAY_MS)

            raw_items = await page.evaluate(_EXTRACTION_SCRIPT)
            if not isinstance(raw_items, list):
                raise RuntimeError("Glassdoor returned an invalid listing payload")

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
        "Glassdoor",
        _runner,
        locale="en-US",
        on_progress=on_progress,
    )
