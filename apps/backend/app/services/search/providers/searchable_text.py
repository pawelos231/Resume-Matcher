"""Helpers for building searchable offer text from provider payloads."""

from __future__ import annotations

import re
from typing import Any


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _collect_text_parts(value: Any, parts: list[str]) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        cleaned = _clean_text(value)
        if cleaned:
            parts.append(cleaned)
        return
    if isinstance(value, (int, float)):
        parts.append(str(value))
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_text_parts(item, parts)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_text_parts(item, parts)


def extract_searchable_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        _collect_text_parts(value, parts)
    return _clean_text(" ".join(parts))
