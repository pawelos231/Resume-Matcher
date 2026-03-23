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
    "pracujpl",
    "rocketjobs",
    "olxpraca",
    "indeed",
    "glassdoor",
    "ziprecruiter",
    "careerbuilder",
]

KeywordMode = Literal["and", "or"]
OfferSortBy = Literal["relevance", "name", "salary"]
OfferSortDirection = Literal["asc", "desc"]
WorkMode = Literal["remote", "hybrid", "office", "unknown"]


@dataclass(slots=True)
class ScrapedOffer:
    """Raw offer collected from provider, including provider text used for matching."""

    id: str
    source: OfferSource
    title: str
    company: str
    location: str
    salary: str | None
    url: str
    skills: list[str]
    searchable_text: str
    work_mode: WorkMode | None = None


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
    workMode: WorkMode


class ScraperProgress(TypedDict):
    """Progress payload emitted by provider scrapers."""

    collected: int
    progress: float
