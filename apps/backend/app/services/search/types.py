"""Shared types for search scraping services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

OfferSource = Literal[
    "nofluffjobs",
    "justjoinit",
    "bulldogjob",
    "theprotocol",
    "solidjobs",
]

KeywordMode = Literal["and", "or"]
OfferSortBy = Literal["relevance", "name", "salary"]
OfferSortDirection = Literal["asc", "desc"]


@dataclass(slots=True)
class ScrapedOffer:
    """Raw offer collected from provider, including internal searchable text."""

    id: str
    source: OfferSource
    title: str
    company: str
    location: str
    salary: str | None
    url: str
    skills: list[str]
    searchable_text: str


class PublicOffer(TypedDict):
    """Offer returned to clients."""

    id: str
    source: OfferSource
    title: str
    company: str
    location: str
    salary: str | None
    url: str
    skills: list[str]
    matchedKeywords: list[str]


class ScraperProgress(TypedDict):
    """Progress payload emitted by provider scrapers."""

    collected: int
    progress: float

