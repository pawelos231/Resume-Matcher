"""Helpers for persistent search-offer cache keys and company-context reuse."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def build_offer_cache_key(source: str, offer_id: str | None, url: str) -> str | None:
    """Build the stable cache key used for offer-scoped persistent state."""
    normalized_source = _clean_text(source)
    normalized_url = _clean_text(url)
    normalized_offer_id = _clean_text(offer_id or "")
    if not normalized_source or not normalized_url or not normalized_offer_id:
        return None
    return f"{normalized_source}:{normalized_offer_id}:{normalized_url}"


def build_offer_cache_key_from_offer(offer: Mapping[str, Any]) -> str | None:
    """Build an offer cache key from a public offer payload."""
    return build_offer_cache_key(
        str(offer.get("source", "")),
        str(offer.get("id", "")),
        str(offer.get("url", "")),
    )


def build_company_context_text(company_info: Mapping[str, Any]) -> str:
    """Convert cached company info into the context block used for JD generation."""
    sections: list[str] = []

    summary = _clean_text(company_info.get("summary"))
    if summary:
        sections.append(f"Company summary:\n{summary}")

    raw_highlights = company_info.get("highlights")
    highlights: list[str] = []
    if isinstance(raw_highlights, list):
        highlights = [
            _clean_text(highlight)
            for highlight in raw_highlights
            if _clean_text(highlight)
        ][:5]
    if highlights:
        sections.append(
            "Company highlights:\n"
            + "\n".join(f"- {highlight}" for highlight in highlights)
        )

    raw_evidence = company_info.get("evidence")
    evidence_snippets: list[str] = []
    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if not isinstance(item, Mapping):
                continue
            snippet = _clean_text(item.get("snippet"))
            if not snippet:
                continue
            evidence_snippets.append(snippet)
            if len(evidence_snippets) >= 2:
                break
    if evidence_snippets:
        sections.append(
            "Supporting company context:\n"
            + "\n".join(f"- {snippet}" for snippet in evidence_snippets)
        )

    return "\n\n".join(sections).strip()
