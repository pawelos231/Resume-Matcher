"""Pydantic schemas for search scraping endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


OfferSource = Literal[
    "nofluffjobs",
    "justjoinit",
    "bulldogjob",
    "theprotocol",
    "solidjobs",
    "pracujpl",
]
KeywordMode = Literal["and", "or"]
OfferSortBy = Literal["relevance", "name", "salary"]
OfferSortDirection = Literal["asc", "desc"]
ScrapeTargetLabel = int | Literal["max"]


class SearchOffer(BaseModel):
    """Public offer returned by search scrape endpoint."""

    id: str
    source: OfferSource
    title: str
    company: str
    location: str
    salary: str | None = None
    url: str
    skills: list[str]
    matchedKeywords: list[str]


class SearchScraperError(BaseModel):
    """Single provider error entry."""

    source: OfferSource
    message: str


class SearchScrapeMeta(BaseModel):
    """Search scrape execution metadata."""

    generatedAt: str
    durationMs: int
    wasStopped: bool = False
    requestedScrapeBySource: dict[OfferSource, ScrapeTargetLabel]
    scrapedTotalCount: int
    scrapedBySource: dict[OfferSource, int]
    dedupedScrapedCount: int
    requestedLimit: int
    returnedCount: int
    keywords: list[str]
    keywordMode: KeywordMode
    salaryRangeOnly: bool
    sortBy: OfferSortBy
    sortDirection: OfferSortDirection


class SearchScrapeResponse(BaseModel):
    """Response payload for search scraping."""

    meta: SearchScrapeMeta
    data: list[SearchOffer]
    errors: list[SearchScraperError]


class SearchProgressEvent(BaseModel):
    """Progress event payload used in SSE mode."""

    stage: Literal["start", "scraping", "finalizing", "done"]
    progressPercent: int
    message: str
    requestedScrapeBySource: dict[OfferSource, ScrapeTargetLabel]
    scrapedTotalCount: int
    scrapedBySource: dict[OfferSource, int]


class SearchDoneEvent(BaseModel):
    """Done event payload used in SSE mode."""

    status: int
    payload: SearchScrapeResponse


class SearchStopRequest(BaseModel):
    """Request payload used to stop an active search scrape."""

    requestId: str = Field(..., min_length=1)


class SearchStopResponse(BaseModel):
    """Response payload returned after asking an active scrape to stop."""

    requestId: str
    stopRequested: bool


class SearchGenerateJobDescriptionRequest(BaseModel):
    """Input payload for generating a tailor-ready job description from an offer."""

    source: OfferSource
    title: str
    company: str
    location: str
    salary: str | None = None
    url: str
    skills: list[str] = Field(default_factory=list)


class SearchGenerateJobDescriptionResponse(BaseModel):
    """Generated job description payload returned to frontend."""

    jobDescription: str
    sourceTextLength: int
    usedLlm: bool
