import { API_BASE } from './client';

export type OfferSource =
  | 'nofluffjobs'
  | 'justjoinit'
  | 'bulldogjob'
  | 'theprotocol'
  | 'solidjobs'
  | 'pracujpl';

export type KeywordMode = 'and' | 'or';
export type OfferSortBy = 'relevance' | 'name' | 'salary';
export type OfferSortDirection = 'asc' | 'desc';

export type SearchOffer = {
  id: string;
  source: OfferSource;
  title: string;
  company: string;
  location: string;
  salary: string | null;
  url: string;
  skills: string[];
  matchedKeywords: string[];
};

export type SearchScraperError = {
  source: OfferSource;
  message: string;
};

export type SearchScrapeMeta = {
  generatedAt: string;
  durationMs: number;
  requestedScrapeBySource: Record<OfferSource, number | 'max'>;
  scrapedTotalCount: number;
  scrapedBySource: Record<OfferSource, number>;
  dedupedScrapedCount: number;
  requestedLimit: number;
  returnedCount: number;
  keywords: string[];
  keywordMode: KeywordMode;
  salaryRangeOnly: boolean;
  sortBy: OfferSortBy;
  sortDirection: OfferSortDirection;
};

export type SearchScrapeResponse = {
  meta: SearchScrapeMeta;
  data: SearchOffer[];
  errors: SearchScraperError[];
};

export type SearchProgressEvent = {
  stage: 'start' | 'scraping' | 'finalizing' | 'done';
  progressPercent: number;
  message: string;
  requestedScrapeBySource: Record<OfferSource, number | 'max'>;
  scrapedTotalCount: number;
  scrapedBySource: Record<OfferSource, number>;
};

export type SearchDoneEvent = {
  status: number;
  payload: SearchScrapeResponse;
};

export type SearchGenerateJobDescriptionRequest = {
  source: OfferSource;
  title: string;
  company: string;
  location: string;
  salary: string | null;
  url: string;
  skills: string[];
};

export type SearchGenerateJobDescriptionResponse = {
  jobDescription: string;
  sourceTextLength: number;
  usedLlm: boolean;
};

export type SearchScrapeParams = {
  limit: number;
  keywords: string;
  keywordMode: KeywordMode;
  salaryRangeOnly: boolean;
  sortBy: OfferSortBy;
  sortDirection: OfferSortDirection;
  sourceLimits: Record<OfferSource, string>;
  timeoutSeconds?: number;
};

const SOURCE_QUERY_KEYS: Record<OfferSource, string> = {
  nofluffjobs: 'scrapeLimitNoFluffJobs',
  justjoinit: 'scrapeLimitJustJoinIt',
  bulldogjob: 'scrapeLimitBulldogJob',
  theprotocol: 'scrapeLimitTheProtocol',
  solidjobs: 'scrapeLimitSolidJobs',
  pracujpl: 'scrapeLimitPracujPl',
};

export function buildSearchScrapeUrl(params: SearchScrapeParams, stream = false): string {
  const query = new URLSearchParams();
  query.set('limit', String(params.limit));
  query.set('keywords', params.keywords);
  query.set('keywordMode', params.keywordMode);
  query.set('salaryRangeOnly', String(params.salaryRangeOnly));
  query.set('sortBy', params.sortBy);
  query.set('sortDirection', params.sortDirection);
  if (typeof params.timeoutSeconds === 'number' && Number.isFinite(params.timeoutSeconds)) {
    query.set('timeoutSeconds', String(params.timeoutSeconds));
  }

  (Object.keys(SOURCE_QUERY_KEYS) as OfferSource[]).forEach((source) => {
    const value = params.sourceLimits[source]?.trim();
    if (!value) return;
    query.set(SOURCE_QUERY_KEYS[source], value);
  });

  if (stream) {
    query.set('stream', '1');
  }

  return `${API_BASE}/search/scrape?${query.toString()}`;
}

export async function fetchSearchScrape(
  params: SearchScrapeParams
): Promise<{ status: number; payload: SearchScrapeResponse }> {
  const response = await fetch(buildSearchScrapeUrl(params, false));
  const payload = (await response.json()) as SearchScrapeResponse;

  if (!response.ok && !payload?.meta) {
    const message = `Search scrape failed with status ${response.status}`;
    throw new Error(message);
  }

  return { status: response.status, payload };
}

export async function generateJobDescriptionFromSearchOffer(
  offer: SearchGenerateJobDescriptionRequest
): Promise<SearchGenerateJobDescriptionResponse> {
  const response = await fetch(`${API_BASE}/search/generate-job-description`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(offer),
  });

  const text = await response.text();
  if (!response.ok) {
    throw new Error(`Job description generation failed (${response.status}): ${text}`);
  }

  try {
    return JSON.parse(text) as SearchGenerateJobDescriptionResponse;
  } catch {
    throw new Error('Job description generation returned invalid JSON.');
  }
}
