"""Helpers for extracting schema.org JobPosting data from offer pages."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from typing import Any


def clean_text(value: str) -> str:
    """Normalize whitespace and decode HTML entities."""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def strip_html_fragment(value: str) -> str:
    """Remove HTML tags from a small fragment."""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return clean_text(without_tags)


def _iter_job_postings(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        if payload.get("@type") == "JobPosting":
            yield payload

        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _iter_job_postings(item)

        for value in payload.values():
            if isinstance(value, (dict, list)):
                yield from _iter_job_postings(value)
        return

    if isinstance(payload, list):
        for item in payload:
            yield from _iter_job_postings(item)


def extract_job_posting_from_html(html_text: str) -> dict[str, Any] | None:
    """Return the first JobPosting payload from application/ld+json blocks."""
    pattern = r"<script[^>]*type=['\"]application/ld\+json['\"][^>]*>([\s\S]*?)</script>"
    for match in re.finditer(pattern, html_text, flags=re.IGNORECASE):
        raw_payload = match.group(1).strip()
        if not raw_payload:
            continue

        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue

        for job_posting in _iter_job_postings(parsed):
            return job_posting

    return None


def _coerce_number(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)

    if not isinstance(value, str):
        return None

    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None

    try:
        return int(digits)
    except ValueError:
        return None


def _format_amount(value: int | None) -> str | None:
    if value is None:
        return None
    return f"{value:,}".replace(",", " ")


def _format_unit(unit_text: str | None) -> str:
    normalized = clean_text(unit_text or "").upper()
    if normalized == "HOUR":
        return "h"
    if normalized == "DAY":
        return "day"
    if normalized == "MONTH":
        return "month"
    if normalized == "YEAR":
        return "year"
    return normalized.lower() or "period"


def format_job_posting_salary(base_salary: Any) -> str | None:
    """Format schema.org baseSalary into a compact text form."""
    if not isinstance(base_salary, dict):
        return None

    currency = clean_text(str(base_salary.get("currency") or "")).upper() or "USD"
    value = base_salary.get("value")
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, dict)), None)
    if not isinstance(value, dict):
        return None

    min_value = _coerce_number(value.get("minValue"))
    max_value = _coerce_number(value.get("maxValue"))
    single_value = _coerce_number(value.get("value"))
    unit = _format_unit(value.get("unitText"))

    formatted_min = _format_amount(min_value)
    formatted_max = _format_amount(max_value)
    formatted_single = _format_amount(single_value)

    if formatted_min and formatted_max:
        return f"{formatted_min} - {formatted_max} {currency}/{unit}"
    if formatted_single:
        return f"{formatted_single} {currency}/{unit}"
    if formatted_min:
        return f"{formatted_min} {currency}/{unit}"
    return None


def format_job_posting_location(job_location: Any) -> str:
    """Format schema.org jobLocation into a compact location label."""
    labels: list[str] = []

    def _append_location(candidate: Any) -> None:
        if not isinstance(candidate, dict):
            return
        address = candidate.get("address")
        if isinstance(address, dict):
            source = address
        else:
            source = candidate

        chunks = [
            clean_text(str(source.get("addressLocality") or "")),
            clean_text(str(source.get("addressRegion") or "")),
            clean_text(str(source.get("addressCountry") or "")),
        ]
        label_parts: list[str] = []
        for chunk in chunks:
            if chunk and chunk not in label_parts:
                label_parts.append(chunk)
        label = ", ".join(label_parts)
        if label and label not in labels:
            labels.append(label)

    if isinstance(job_location, list):
        for item in job_location:
            _append_location(item)
    else:
        _append_location(job_location)

    return ", ".join(labels)


def extract_job_posting_skills(raw_skills: Any) -> list[str]:
    """Extract up to 12 skill labels from schema.org skills fields."""
    candidates: list[str] = []

    def _push(value: str) -> None:
        normalized = strip_html_fragment(value)
        if not normalized:
            return
        for chunk in re.split(r"[,\n;|]+", normalized):
            item = clean_text(chunk)
            if not item or item in candidates:
                continue
            candidates.append(item)
            if len(candidates) >= 12:
                return

    if isinstance(raw_skills, str):
        _push(raw_skills)
    elif isinstance(raw_skills, list):
        for item in raw_skills:
            if isinstance(item, dict):
                for key in ("name", "description", "value"):
                    value = item.get(key)
                    if isinstance(value, str):
                        _push(value)
            elif isinstance(item, str):
                _push(item)
            if len(candidates) >= 12:
                break
    elif isinstance(raw_skills, dict):
        for key in ("name", "description", "value"):
            value = raw_skills.get(key)
            if isinstance(value, str):
                _push(value)

    return candidates[:12]
