import { API_BASE, apiPost } from './client';

export type OfferSource =
  | 'nofluffjobs'
  | 'justjoinit'
  | 'bulldogjob'
  | 'theprotocol'
  | 'solidjobs'
  | 'pracujpl'
  | 'rocketjobs'
  | 'olxpraca'
  | 'indeed'
  | 'glassdoor'
  | 'ziprecruiter'
  | 'careerbuilder';

export type KeywordMode = 'and' | 'or';
export type OfferSortBy = 'relevance' | 'name' | 'salary';
export type OfferSortDirection = 'asc' | 'desc';
export type SearchWorkMode = 'remote' | 'hybrid' | 'office' | 'unknown';

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
  workMode: SearchWorkMode;
  alreadyGeneratedResume: boolean;
  generatedResumeId: string | null;
  alreadyGeneratedCompanyInfo: boolean;
};

export type SearchScraperError = {
  source: OfferSource;
  message: string;
};

export type SearchScrapeMeta = {
  generatedAt: string;
  durationMs: number;
  wasStopped: boolean;
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
  id?: string | null;
  source: OfferSource;
  title: string;
  company: string;
  location: string;
  salary: string | null;
  url: string;
  skills: string[];
  companyContext?: string | null;
};

export type SearchGenerateJobDescriptionResponse = {
  jobDescription: string;
  sourceTextLength: number;
  usedLlm: boolean;
  companyContextSource: 'request' | 'cache' | 'none';
};

export type SearchCompanyInfoRequest = {
  id?: string | null;
  source: OfferSource;
  title: string;
  company: string;
  location: string;
  salary: string | null;
  url: string;
  skills: string[];
  question?: string | null;
};

export type SearchCompanyInfoSourcePage = {
  url: string;
  title: string;
};

export type SearchCompanyInfoEvidence = {
  url: string;
  title: string;
  snippet: string;
};

export type SearchCompanyInfoStats = {
  pagesVisited: number;
  chunksIndexed: number;
  relevantChunks: number;
  retrievalMethod: 'embedding' | 'lexical';
  usedLlm: boolean;
};

export type SearchCompanyInfoResponse = {
  company: string;
  websiteUrl: string | null;
  websiteFoundVia: 'offer_page' | 'search_engine' | 'unresolved';
  question: string;
  summary: string;
  highlights: string[];
  sourcePages: SearchCompanyInfoSourcePage[];
  evidence: SearchCompanyInfoEvidence[];
  stats: SearchCompanyInfoStats;
};

export type SearchScrapeParams = {
  limit: number;
  keywords: string;
  keywordMode: KeywordMode;
  salaryRangeOnly: boolean;
  sortBy: OfferSortBy;
  sortDirection: OfferSortDirection;
  sourceLimits: Record<OfferSource, string>;
  requestId?: string;
  timeoutSeconds?: number;
};

export const SEARCH_OFFER_SOURCES: OfferSource[] = [
  'nofluffjobs',
  'justjoinit',
  'bulldogjob',
  'theprotocol',
  'solidjobs',
  'pracujpl',
  'rocketjobs',
  'olxpraca',
  'indeed',
  'glassdoor',
  'ziprecruiter',
  'careerbuilder',
];

export const EMPTY_SEARCH_SCRAPED_BY_SOURCE: Record<OfferSource, number> = {
  nofluffjobs: 0,
  justjoinit: 0,
  bulldogjob: 0,
  theprotocol: 0,
  solidjobs: 0,
  pracujpl: 0,
  rocketjobs: 0,
  olxpraca: 0,
  indeed: 0,
  glassdoor: 0,
  ziprecruiter: 0,
  careerbuilder: 0,
};

function normalizeScrapeTargetLabel(
  value: unknown,
  fallback: number | 'max' = 0
): number | 'max' {
  if (value === 'max') {
    return 'max';
  }

  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    return value;
  }

  if (typeof value === 'string') {
    const trimmed = value.trim().toLowerCase();
    if (trimmed === 'max') {
      return 'max';
    }
    const parsed = Number.parseInt(trimmed, 10);
    if (Number.isFinite(parsed) && parsed >= 0) {
      return parsed;
    }
  }

  return fallback;
}

function normalizeScrapedCount(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    return value;
  }

  if (typeof value === 'string') {
    const parsed = Number.parseInt(value.trim(), 10);
    if (Number.isFinite(parsed) && parsed >= 0) {
      return parsed;
    }
  }

  return 0;
}

export function normalizeSearchProgressEvent(
  event: SearchProgressEvent
): SearchProgressEvent {
  return {
    ...event,
    scrapedBySource: SEARCH_OFFER_SOURCES.reduce(
      (accumulator, source) => ({
        ...accumulator,
        [source]: normalizeScrapedCount(event.scrapedBySource?.[source]),
      }),
      {} as Record<OfferSource, number>
    ),
  };
}

export function normalizeSearchScrapeResponse(
  payload: SearchScrapeResponse | null | undefined,
  fallbackRequestedBySource?: Partial<Record<OfferSource, number | 'max'>>
): SearchScrapeResponse | null {
  if (!payload?.meta) {
    return null;
  }

  const requestedScrapeBySource = SEARCH_OFFER_SOURCES.reduce(
    (accumulator, source) => ({
      ...accumulator,
      [source]: normalizeScrapeTargetLabel(
        payload.meta.requestedScrapeBySource?.[source],
        fallbackRequestedBySource?.[source] ?? 0
      ),
    }),
    {} as Record<OfferSource, number | 'max'>
  );
  const scrapedBySource = SEARCH_OFFER_SOURCES.reduce(
    (accumulator, source) => ({
      ...accumulator,
      [source]: normalizeScrapedCount(payload.meta.scrapedBySource?.[source]),
    }),
    {} as Record<OfferSource, number>
  );

  return {
    ...payload,
    meta: {
      ...payload.meta,
      requestedScrapeBySource,
      scrapedBySource,
    },
  };
}

const SOURCE_QUERY_KEYS: Record<OfferSource, string> = {
  nofluffjobs: 'scrapeLimitNoFluffJobs',
  justjoinit: 'scrapeLimitJustJoinIt',
  bulldogjob: 'scrapeLimitBulldogJob',
  theprotocol: 'scrapeLimitTheProtocol',
  solidjobs: 'scrapeLimitSolidJobs',
  pracujpl: 'scrapeLimitPracujPl',
  rocketjobs: 'scrapeLimitRocketJobs',
  olxpraca: 'scrapeLimitOlxPraca',
  indeed: 'scrapeLimitIndeed',
  glassdoor: 'scrapeLimitGlassdoor',
  ziprecruiter: 'scrapeLimitZipRecruiter',
  careerbuilder: 'scrapeLimitCareerBuilder',
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
  if (params.requestId) {
    query.set('requestId', params.requestId);
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
  const payload = normalizeSearchScrapeResponse(
    (await response.json()) as SearchScrapeResponse,
    undefined
  );

  if (!response.ok && !payload?.meta) {
    const message = `Search scrape failed with status ${response.status}`;
    throw new Error(message);
  }

  if (payload === null) {
    throw new Error('Search scrape returned an invalid payload.');
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

export async function getCompanyInfoFromSearchOffer(
  offer: SearchCompanyInfoRequest
): Promise<SearchCompanyInfoResponse> {
  const response = await fetch(`${API_BASE}/search/company-info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(offer),
  });

  const text = await response.text();
  if (!response.ok) {
    throw new Error(`Company info request failed (${response.status}): ${text}`);
  }

  try {
    return JSON.parse(text) as SearchCompanyInfoResponse;
  } catch {
    throw new Error('Company info request returned invalid JSON.');
  }
}

export async function stopSearchScrape(
  requestId: string
): Promise<{ requestId: string; stopRequested: boolean }> {
  const response = await apiPost('/search/scrape/stop', { requestId });
  const text = await response.text();

  if (!response.ok) {
    throw new Error(`Stopping scrape failed (${response.status}): ${text}`);
  }

  try {
    return JSON.parse(text) as { requestId: string; stopRequested: boolean };
  } catch {
    throw new Error('Stopping scrape returned invalid JSON.');
  }
}
