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
ScrapeTargetLabel = int | Literal["max"]
WorkMode = Literal["remote", "hybrid", "office", "unknown"]


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
    workMode: WorkMode = "unknown"
    alreadyGeneratedResume: bool = False
    generatedResumeId: str | None = None
    alreadyGeneratedCompanyInfo: bool = False


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

    id: str | None = None
    source: OfferSource
    title: str
    company: str
    location: str
    salary: str | None = None
    url: str
    skills: list[str] = Field(default_factory=list)
    companyContext: str | None = None


class SearchGenerateJobDescriptionResponse(BaseModel):
    """Generated job description payload returned to frontend."""

    jobDescription: str
    sourceTextLength: int
    usedLlm: bool
    companyContextSource: Literal["request", "cache", "none"] = "none"


class SearchCompanyInfoRequest(BaseModel):
    """Input payload for crawling and summarizing company information."""

    id: str | None = None
    source: OfferSource
    title: str
    company: str = Field(..., min_length=1)
    location: str
    salary: str | None = None
    url: str
    skills: list[str] = Field(default_factory=list)
    question: str | None = None


class SearchCompanyInfoSourcePage(BaseModel):
    """Crawled page that contributed to the company summary."""

    url: str
    title: str


class SearchCompanyInfoEvidence(BaseModel):
    """Relevant fragment used as supporting evidence in the response."""

    url: str
    title: str
    snippet: str


class SearchCompanyInfoStats(BaseModel):
    """Execution metadata for company crawling and extraction."""

    pagesVisited: int
    chunksIndexed: int
    relevantChunks: int
    retrievalMethod: Literal["embedding", "lexical"]
    usedLlm: bool


class SearchCompanyInfoResponse(BaseModel):
    """Summarized company information returned to the frontend."""

    company: str
    websiteUrl: str | None = None
    websiteFoundVia: Literal["offer_page", "search_engine", "unresolved"]
    question: str
    summary: str
    highlights: list[str] = Field(default_factory=list)
    sourcePages: list[SearchCompanyInfoSourcePage] = Field(default_factory=list)
    evidence: list[SearchCompanyInfoEvidence] = Field(default_factory=list)
    stats: SearchCompanyInfoStats
