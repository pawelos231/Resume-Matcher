'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  FormEvent,
  useDeferredValue,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { ToggleSwitch } from '@/components/ui/toggle-switch';
import {
  EMPTY_SEARCH_SCRAPED_BY_SOURCE,
  buildSearchScrapeUrl,
  fetchSearchScrape,
  generateJobDescriptionFromSearchOffer,
  getCompanyInfoFromSearchOffer,
  normalizeSearchProgressEvent,
  normalizeSearchScrapeResponse,
  stopSearchScrape,
  type OfferSortBy,
  type OfferSortDirection,
  type OfferSource,
  type SearchCompanyInfoResponse,
  type SearchDoneEvent,
  type SearchOffer,
  type SearchWorkMode,
  type SearchProgressEvent,
  type SearchScrapeParams,
  type SearchScrapeResponse,
} from '@/lib/api/search';
import { improveResume, uploadJobDescriptions } from '@/lib/api/resume';
import {
  clearPendingOfferMarker,
  createOfferResumeMarker,
  getOfferRuntimeKey,
  markOfferResumeGenerated,
  readOfferResumeMap,
  savePendingOfferMarker,
  toJobOfferMarker,
  type OfferResumeMap,
} from '@/lib/search-offer-resume-map';

import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import SearchIcon from 'lucide-react/dist/esm/icons/search';
import AlertTriangle from 'lucide-react/dist/esm/icons/alert-triangle';
import ExternalLink from 'lucide-react/dist/esm/icons/external-link';

type BulkGenerationProgress = {
  total: number;
  processed: number;
  success: number;
  failed: number;
  currentOfferLabel: string | null;
};

type CompanyContextSource = 'request' | 'cache' | 'none';
type SelectableWorkMode = Exclude<SearchWorkMode, 'unknown'>;

type CompanyInfoMap = Record<string, SearchCompanyInfoResponse>;
type PersistedSearchControlsState = {
  limit: number;
  keywords: string;
  keywordMode: 'and' | 'or';
  salaryRangeOnly: boolean;
  sortBy: OfferSortBy;
  sortDirection: OfferSortDirection;
  scrapeTimeoutSeconds: string;
  sourceLimits: Record<OfferSource, string>;
  tableSearchText: string;
  selectedWorkModes: SelectableWorkMode[];
  hideAppliedOffers: boolean;
  pageSize: PageSizeOption;
  currentPage: number;
};
type PersistedSearchResultsState = {
  response: SearchScrapeResponse | null;
  companyInfoByOfferKey: CompanyInfoMap;
  companyInfoErrorsByOfferKey: Record<string, string>;
};

const SOURCE_CONFIG: Array<{
  key: OfferSource;
  label: string;
}> = [
  { key: 'nofluffjobs', label: 'NoFluffJobs' },
  { key: 'justjoinit', label: 'JustJoinIT' },
  { key: 'bulldogjob', label: 'Bulldogjob' },
  { key: 'theprotocol', label: 'theprotocol.it' },
  { key: 'solidjobs', label: 'Solid.jobs' },
  { key: 'pracujpl', label: 'Pracuj.pl' },
  { key: 'rocketjobs', label: 'RocketJobs' },
  { key: 'olxpraca', label: 'OLX Praca' },
  { key: 'indeed', label: 'Indeed' },
  { key: 'glassdoor', label: 'Glassdoor' },
  { key: 'ziprecruiter', label: 'ZipRecruiter' },
  { key: 'careerbuilder', label: 'CareerBuilder' },
];

const DEFAULT_SOURCE_LIMITS: Record<OfferSource, string> = {
  nofluffjobs: 'max',
  justjoinit: '10',
  bulldogjob: 'max',
  theprotocol: 'max',
  solidjobs: 'max',
  pracujpl: '50',
  rocketjobs: '20',
  olxpraca: '20',
  indeed: '20',
  glassdoor: '20',
  ziprecruiter: '20',
  careerbuilder: '20',
};

const MIN_SCRAPE_TIMEOUT_SECONDS = 10;
const MAX_SCRAPE_TIMEOUT_SECONDS = 600;
const PAGE_SIZE_OPTIONS = [25, 50, 100, 'max'] as const;
const FETCH_ADDITIONAL_COMPANY_INFO_STORAGE_KEY = 'search_fetch_additional_company_info_v2';
const SEARCH_PAGE_CONTROLS_STORAGE_KEY = 'search_page_controls_v2';
const SEARCH_PAGE_RESULTS_STORAGE_KEY = 'search_page_results_v2';
const WORK_MODE_OPTIONS: Array<{
  value: SelectableWorkMode;
  label: string;
  activeClassName: string;
}> = [
  { value: 'remote', label: 'Full remote', activeClassName: 'bg-[#1D4ED8] text-white' },
  { value: 'hybrid', label: 'Hybrid', activeClassName: 'bg-[#F97316] text-black' },
  { value: 'office', label: 'Office', activeClassName: 'bg-black text-white' },
];

type PageSizeOption = (typeof PAGE_SIZE_OPTIONS)[number];

function readSessionStorageJson<T>(key: string): T | null {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    const rawValue = window.sessionStorage.getItem(key);
    if (!rawValue) {
      return null;
    }
    return JSON.parse(rawValue) as T;
  } catch {
    return null;
  }
}

function isPageSizeOption(value: unknown): value is PageSizeOption {
  return PAGE_SIZE_OPTIONS.some((option) => option === value);
}

function isSelectableWorkMode(value: unknown): value is SelectableWorkMode {
  return WORK_MODE_OPTIONS.some((option) => option.value === value);
}

function parseScrapeTimeoutSeconds(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }

  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return undefined;
  }

  return Math.min(MAX_SCRAPE_TIMEOUT_SECONDS, Math.max(MIN_SCRAPE_TIMEOUT_SECONDS, parsed));
}

function getOfferWorkMode(offer: Pick<SearchOffer, 'workMode'>): SearchWorkMode {
  return offer.workMode ?? 'unknown';
}

function formatWorkModeLabel(workMode: SearchWorkMode): string {
  switch (workMode) {
    case 'remote':
      return 'Full remote';
    case 'hybrid':
      return 'Hybrid';
    case 'office':
      return 'Office';
    default:
      return 'Unknown';
  }
}

function filterOffers(
  offers: SearchOffer[],
  searchText: string,
  selectedWorkModes: SelectableWorkMode[]
): SearchOffer[] {
  const normalized = searchText.trim().toLowerCase();
  const tokens = normalized ? normalized.split(/\s+/).filter(Boolean) : [];

  if (!normalized && selectedWorkModes.length === 0) {
    return offers;
  }

  return offers.filter((offer) => {
    const workMode = getOfferWorkMode(offer);
    if (selectedWorkModes.length > 0) {
      if (workMode === 'unknown' || !selectedWorkModes.includes(workMode)) {
        return false;
      }
    }

    if (!normalized) {
      return true;
    }

    if (tokens.length === 0) {
      return true;
    }

    const searchable = [
      offer.id,
      offer.source,
      offer.title,
      offer.company,
      offer.location,
      offer.salary ?? '',
      offer.skills.join(' '),
      offer.matchedKeywords.join(' '),
      workMode,
      formatWorkModeLabel(workMode),
      offer.url,
    ]
      .join(' ')
      .toLowerCase();

    return tokens.every((token) => searchable.includes(token));
  });
}

function toNumericSalaryValue(salary: string | null): number | null {
  if (!salary) {
    return null;
  }

  const fragments = salary.match(/\d[\d\s,.]*/g) ?? [];
  const numericValues = fragments
    .map((fragment) => Number.parseInt(fragment.replace(/[^\d]/g, ''), 10))
    .filter((value) => Number.isFinite(value));

  if (numericValues.length === 0) {
    return null;
  }

  return Math.max(...numericValues);
}

function compareName(left: SearchOffer, right: SearchOffer): number {
  const titleCompare = left.title.localeCompare(right.title, undefined, { sensitivity: 'base' });
  if (titleCompare !== 0) {
    return titleCompare;
  }

  const companyCompare = left.company.localeCompare(right.company, undefined, {
    sensitivity: 'base',
  });
  if (companyCompare !== 0) {
    return companyCompare;
  }

  return left.url.localeCompare(right.url, undefined, { sensitivity: 'base' });
}

function sortOffers(
  offers: SearchOffer[],
  sortBy: OfferSortBy,
  sortDirection: OfferSortDirection
): SearchOffer[] {
  const cloned = [...offers];

  if (sortBy === 'relevance') {
    return sortDirection === 'desc' ? cloned.reverse() : cloned;
  }

  if (sortBy === 'name') {
    cloned.sort(compareName);
    return sortDirection === 'desc' ? cloned.reverse() : cloned;
  }

  return cloned.sort((left, right) => {
    const leftValue = toNumericSalaryValue(left.salary);
    const rightValue = toNumericSalaryValue(right.salary);

    if (leftValue === null && rightValue === null) {
      return compareName(left, right);
    }

    if (leftValue === null) {
      return 1;
    }

    if (rightValue === null) {
      return -1;
    }

    if (leftValue === rightValue) {
      return compareName(left, right);
    }

    return sortDirection === 'desc' ? rightValue - leftValue : leftValue - rightValue;
  });
}

function formatSourceScrapeCount(scraped: number, requested: number | 'max'): string {
  return requested === 'max' ? `${scraped} / max` : `${scraped} / ${requested}`;
}

function parseRequestedScrapeTargetLabel(
  value: string | undefined,
  fallback: string
): number | 'max' {
  const candidate = (value?.trim() || fallback).trim().toLowerCase();
  if (candidate === 'max') {
    return 'max';
  }

  const parsed = Number.parseInt(candidate, 10);
  return Number.isFinite(parsed) && parsed >= 0
    ? parsed
    : parseRequestedScrapeTargetLabel(fallback, '0');
}

function buildRequestedScrapeFallback(
  sourceLimits: Record<OfferSource, string>
): Record<OfferSource, number | 'max'> {
  return SOURCE_CONFIG.reduce(
    (accumulator, source) => ({
      ...accumulator,
      [source.key]: parseRequestedScrapeTargetLabel(
        sourceLimits[source.key],
        DEFAULT_SOURCE_LIMITS[source.key]
      ),
    }),
    {} as Record<OfferSource, number | 'max'>
  );
}

function formatElapsedDuration(elapsedMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }

  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function createScrapeRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }

  return `scrape-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export default function SearchPage() {
  const router = useRouter();
  const activeEventSourceRef = useRef<{ close: () => void } | null>(null);
  const activeScrapeRequestIdRef = useRef<string | null>(null);
  const hasHydratedPersistedSearchStateRef = useRef<boolean>(false);
  const shouldSkipNextPageResetRef = useRef<boolean>(false);

  const [limit, setLimit] = useState<number>(1000);
  const [keywords, setKeywords] = useState<string>('react,node,typescript');
  const [keywordMode, setKeywordMode] = useState<'and' | 'or'>('and');
  const [salaryRangeOnly, setSalaryRangeOnly] = useState<boolean>(false);
  const [sortBy, setSortBy] = useState<OfferSortBy>('relevance');
  const [sortDirection, setSortDirection] = useState<OfferSortDirection>('asc');
  const [scrapeTimeoutSeconds, setScrapeTimeoutSeconds] = useState<string>('10');
  const [sourceLimits, setSourceLimits] =
    useState<Record<OfferSource, string>>(DEFAULT_SOURCE_LIMITS);
  const [tableSearchText, setTableSearchText] = useState<string>('');
  const [selectedWorkModes, setSelectedWorkModes] = useState<SelectableWorkMode[]>([]);
  const [hideAppliedOffers, setHideAppliedOffers] = useState<boolean>(false);
  const [fetchAdditionalCompanyInfo, setFetchAdditionalCompanyInfo] = useState<boolean>(false);
  const [pageSize, setPageSize] = useState<PageSizeOption>(50);
  const [currentPage, setCurrentPage] = useState<number>(1);

  const [loading, setLoading] = useState<boolean>(false);
  const [isStoppingScrape, setIsStoppingScrape] = useState<boolean>(false);
  const [isBulkGenerating, setIsBulkGenerating] = useState<boolean>(false);
  const [bulkProgress, setBulkProgress] = useState<BulkGenerationProgress | null>(null);
  const [generatingEditOfferKey, setGeneratingEditOfferKey] = useState<string | null>(null);
  const [generatingOfferKey, setGeneratingOfferKey] = useState<string | null>(null);
  const [companyInfoLoadingKey, setCompanyInfoLoadingKey] = useState<string | null>(null);
  const [companyInfoByOfferKey, setCompanyInfoByOfferKey] = useState<CompanyInfoMap>({});
  const [companyInfoErrorsByOfferKey, setCompanyInfoErrorsByOfferKey] = useState<
    Record<string, string>
  >({});
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<SearchScrapeResponse | null>(null);
  const [offerResumeMap, setOfferResumeMap] = useState<OfferResumeMap>({});
  const [progressPercent, setProgressPercent] = useState<number>(0);
  const [progressMessage, setProgressMessage] = useState<string>('');
  const [progressBySource, setProgressBySource] = useState<Record<OfferSource, number>>({
    ...EMPTY_SEARCH_SCRAPED_BY_SOURCE,
  });
  const [activeScrapeRequestId, setActiveScrapeRequestId] = useState<string | null>(null);
  const [requestStartedAt, setRequestStartedAt] = useState<number | null>(null);
  const [elapsedRequestMs, setElapsedRequestMs] = useState<number>(0);
  const deferredTableSearchText = useDeferredValue(tableSearchText);

  useEffect(() => {
    setOfferResumeMap(readOfferResumeMap());
  }, []);

  useLayoutEffect(() => {
    let hydratedSourceLimits: Record<OfferSource, string> = { ...DEFAULT_SOURCE_LIMITS };
    const persistedControls = readSessionStorageJson<PersistedSearchControlsState>(
      SEARCH_PAGE_CONTROLS_STORAGE_KEY
    );
    if (persistedControls) {
      if (typeof persistedControls.limit === 'number' && Number.isFinite(persistedControls.limit)) {
        setLimit(Math.max(1, persistedControls.limit));
      }
      if (typeof persistedControls.keywords === 'string') {
        setKeywords(persistedControls.keywords);
      }
      if (persistedControls.keywordMode === 'and' || persistedControls.keywordMode === 'or') {
        setKeywordMode(persistedControls.keywordMode);
      }
      if (typeof persistedControls.salaryRangeOnly === 'boolean') {
        setSalaryRangeOnly(persistedControls.salaryRangeOnly);
      }
      if (
        persistedControls.sortBy === 'relevance' ||
        persistedControls.sortBy === 'name' ||
        persistedControls.sortBy === 'salary'
      ) {
        setSortBy(persistedControls.sortBy);
      }
      if (persistedControls.sortDirection === 'asc' || persistedControls.sortDirection === 'desc') {
        setSortDirection(persistedControls.sortDirection);
      }
      if (typeof persistedControls.scrapeTimeoutSeconds === 'string') {
        setScrapeTimeoutSeconds(persistedControls.scrapeTimeoutSeconds);
      }
      if (persistedControls.sourceLimits && typeof persistedControls.sourceLimits === 'object') {
        hydratedSourceLimits = {
          ...DEFAULT_SOURCE_LIMITS,
          ...persistedControls.sourceLimits,
        };
        setSourceLimits(hydratedSourceLimits);
      }
      if (typeof persistedControls.tableSearchText === 'string') {
        setTableSearchText(persistedControls.tableSearchText);
      }
      if (Array.isArray(persistedControls.selectedWorkModes)) {
        setSelectedWorkModes(
          persistedControls.selectedWorkModes.filter((value) => isSelectableWorkMode(value))
        );
      }
      if (typeof persistedControls.hideAppliedOffers === 'boolean') {
        setHideAppliedOffers(persistedControls.hideAppliedOffers);
      }
      if (isPageSizeOption(persistedControls.pageSize)) {
        setPageSize(persistedControls.pageSize);
      }
      if (
        typeof persistedControls.currentPage === 'number' &&
        Number.isFinite(persistedControls.currentPage)
      ) {
        setCurrentPage(Math.max(1, Math.floor(persistedControls.currentPage)));
        shouldSkipNextPageResetRef.current = true;
      }
    }

    const persistedResults = readSessionStorageJson<PersistedSearchResultsState>(
      SEARCH_PAGE_RESULTS_STORAGE_KEY
    );
    if (persistedResults) {
      setResponse(
        normalizeSearchScrapeResponse(
          persistedResults.response ?? null,
          buildRequestedScrapeFallback(hydratedSourceLimits)
        )
      );
      setCompanyInfoByOfferKey(persistedResults.companyInfoByOfferKey ?? {});
      setCompanyInfoErrorsByOfferKey(persistedResults.companyInfoErrorsByOfferKey ?? {});
    }

    hasHydratedPersistedSearchStateRef.current = true;
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    const storedValue = window.localStorage.getItem(FETCH_ADDITIONAL_COMPANY_INFO_STORAGE_KEY);
    setFetchAdditionalCompanyInfo(storedValue === 'true');
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }

    window.localStorage.setItem(
      FETCH_ADDITIONAL_COMPANY_INFO_STORAGE_KEY,
      fetchAdditionalCompanyInfo ? 'true' : 'false'
    );
  }, [fetchAdditionalCompanyInfo]);

  useEffect(() => {
    if (!loading || requestStartedAt === null) {
      return;
    }

    setElapsedRequestMs(Date.now() - requestStartedAt);

    const intervalId = window.setInterval(() => {
      setElapsedRequestMs(Date.now() - requestStartedAt);
    }, 1000);

    return () => window.clearInterval(intervalId);
  }, [loading, requestStartedAt]);

  useEffect(() => {
    return () => {
      const activeEventSource = activeEventSourceRef.current as { close: () => void } | null;
      if (activeEventSource !== null) {
        activeEventSource.close();
      }
      activeEventSourceRef.current = null;

      const requestId = activeScrapeRequestIdRef.current;
      if (requestId) {
        void stopSearchScrape(requestId).catch(() => undefined);
      }
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined' || !hasHydratedPersistedSearchStateRef.current) {
      return;
    }

    const payload: PersistedSearchControlsState = {
      limit,
      keywords,
      keywordMode,
      salaryRangeOnly,
      sortBy,
      sortDirection,
      scrapeTimeoutSeconds,
      sourceLimits,
      tableSearchText,
      selectedWorkModes,
      hideAppliedOffers,
      pageSize,
      currentPage,
    };

    window.sessionStorage.setItem(SEARCH_PAGE_CONTROLS_STORAGE_KEY, JSON.stringify(payload));
  }, [
    hideAppliedOffers,
    keywordMode,
    keywords,
    limit,
    pageSize,
    salaryRangeOnly,
    scrapeTimeoutSeconds,
    sortBy,
    sortDirection,
    sourceLimits,
    selectedWorkModes,
    tableSearchText,
    currentPage,
  ]);

  useEffect(() => {
    if (typeof window === 'undefined' || !hasHydratedPersistedSearchStateRef.current || loading) {
      return;
    }

    const hasPersistedResults =
      response !== null ||
      Object.keys(companyInfoByOfferKey).length > 0 ||
      Object.keys(companyInfoErrorsByOfferKey).length > 0;

    if (!hasPersistedResults) {
      window.sessionStorage.removeItem(SEARCH_PAGE_RESULTS_STORAGE_KEY);
      return;
    }

    const payload: PersistedSearchResultsState = {
      response,
      companyInfoByOfferKey,
      companyInfoErrorsByOfferKey,
    };

    window.sessionStorage.setItem(SEARCH_PAGE_RESULTS_STORAGE_KEY, JSON.stringify(payload));
  }, [companyInfoByOfferKey, companyInfoErrorsByOfferKey, loading, response]);

  const sortedOffers = useMemo(
    () => sortOffers(response?.data ?? [], sortBy, sortDirection),
    [response, sortBy, sortDirection]
  );

  const updateOfferInResponse = (
    offer: Pick<SearchOffer, 'source' | 'id' | 'url'>,
    updates: Partial<SearchOffer>
  ): void => {
    setResponse((previous) => {
      if (!previous) {
        return previous;
      }

      const runtimeKey = getOfferRuntimeKey(offer);
      return {
        ...previous,
        data: previous.data.map((item) =>
          getOfferRuntimeKey(item) === runtimeKey ? { ...item, ...updates } : item
        ),
      };
    });
  };

  const hasGeneratedResumeForOffer = (offer: SearchOffer): boolean => {
    const runtimeKey = getOfferRuntimeKey(offer);
    return Boolean(
      offer.generatedResumeId || offer.alreadyGeneratedResume || offerResumeMap[runtimeKey]
    );
  };

  const hasGeneratedCompanyInfoForOffer = (offer: SearchOffer): boolean => {
    const runtimeKey = getOfferRuntimeKey(offer);
    return Boolean(offer.alreadyGeneratedCompanyInfo || companyInfoByOfferKey[runtimeKey]);
  };

  const statusFilteredOffers = useMemo(() => {
    const offers = sortedOffers;
    if (!hideAppliedOffers) {
      return offers;
    }
    return offers.filter((offer) => {
      const runtimeKey = getOfferRuntimeKey(offer);
      return !(
        offer.generatedResumeId ||
        offer.alreadyGeneratedResume ||
        offerResumeMap[runtimeKey]
      );
    });
  }, [sortedOffers, hideAppliedOffers, offerResumeMap]);

  const appliedOffersCount = useMemo(() => {
    const offers = response?.data ?? [];
    return offers.reduce((count, offer) => {
      const runtimeKey = getOfferRuntimeKey(offer);
      return (
        count +
        (offer.generatedResumeId || offer.alreadyGeneratedResume || offerResumeMap[runtimeKey]
          ? 1
          : 0)
      );
    }, 0);
  }, [response, offerResumeMap]);

  const workModeCounts = useMemo(
    () =>
      statusFilteredOffers.reduce<Record<SelectableWorkMode, number>>(
        (counts, offer) => {
          const workMode = getOfferWorkMode(offer);
          if (workMode !== 'unknown') {
            counts[workMode] += 1;
          }
          return counts;
        },
        {
          remote: 0,
          hybrid: 0,
          office: 0,
        }
      ),
    [statusFilteredOffers]
  );

  const displayedOffers = useMemo(
    () => filterOffers(statusFilteredOffers, deferredTableSearchText, selectedWorkModes),
    [statusFilteredOffers, deferredTableSearchText, selectedWorkModes]
  );

  const bulkGenerationOffers = useMemo(
    () =>
      displayedOffers.filter((offer) => {
        const runtimeKey = getOfferRuntimeKey(offer);
        return !(
          offer.generatedResumeId ||
          offer.alreadyGeneratedResume ||
          offerResumeMap[runtimeKey]
        );
      }),
    [displayedOffers, offerResumeMap]
  );

  const totalPages = useMemo(() => {
    if (pageSize === 'max') {
      return 1;
    }

    return Math.max(1, Math.ceil(displayedOffers.length / pageSize));
  }, [displayedOffers.length, pageSize]);

  const pagedOffers = useMemo(() => {
    if (pageSize === 'max') {
      return displayedOffers;
    }

    const startIndex = (currentPage - 1) * pageSize;
    return displayedOffers.slice(startIndex, startIndex + pageSize);
  }, [currentPage, displayedOffers, pageSize]);

  const pageRangeStart = useMemo(() => {
    if (displayedOffers.length === 0) {
      return 0;
    }

    if (pageSize === 'max') {
      return 1;
    }

    return (currentPage - 1) * pageSize + 1;
  }, [currentPage, displayedOffers.length, pageSize]);

  const pageRangeEnd = useMemo(() => {
    if (displayedOffers.length === 0) {
      return 0;
    }

    if (pageSize === 'max') {
      return displayedOffers.length;
    }

    return Math.min(displayedOffers.length, currentPage * pageSize);
  }, [currentPage, displayedOffers.length, pageSize]);

  useEffect(() => {
    if (!hasHydratedPersistedSearchStateRef.current) {
      return;
    }

    if (shouldSkipNextPageResetRef.current) {
      shouldSkipNextPageResetRef.current = false;
      return;
    }

    setCurrentPage(1);
  }, [
    deferredTableSearchText,
    hideAppliedOffers,
    pageSize,
    response,
    selectedWorkModes,
    sortBy,
    sortDirection,
  ]);

  useEffect(() => {
    setCurrentPage((previousPage) => Math.min(previousPage, totalPages));
  }, [totalPages]);

  const buildScrapeParams = (requestId?: string): SearchScrapeParams => ({
    limit,
    keywords,
    keywordMode,
    salaryRangeOnly,
    sortBy,
    sortDirection,
    sourceLimits,
    requestId,
    timeoutSeconds: parseScrapeTimeoutSeconds(scrapeTimeoutSeconds),
  });

  const runScrapeFallback = async (requestId: string): Promise<void> => {
    const fallback = await fetchSearchScrape(buildScrapeParams(requestId));
    const normalizedPayload = normalizeSearchScrapeResponse(
      fallback.payload,
      buildRequestedScrapeFallback(sourceLimits)
    );
    if (normalizedPayload === null) {
      throw new Error('Could not normalize scrape response.');
    }

    setResponse(normalizedPayload);
    setProgressPercent(100);
    setProgressMessage(
      normalizedPayload.meta.wasStopped
        ? 'Scraping stopped. Returned partial results.'
        : 'Scraping completed'
    );
    setProgressBySource(normalizedPayload.meta.scrapedBySource);
    if (normalizedPayload.meta.wasStopped) {
      setNotice('Scraping stopped. Returned partial results.');
    }
    if (fallback.status >= 400) {
      setError('Scrape finished with source errors.');
    }
  };

  const handleStopScraping = async (): Promise<void> => {
    const requestId = activeScrapeRequestIdRef.current;
    if (!loading || !requestId || isStoppingScrape) {
      return;
    }

    setError(null);
    setNotice('Stopping scrape. Waiting for partial results...');
    setProgressMessage('Stopping scrape and returning partial results...');
    setIsStoppingScrape(true);

    try {
      const result = await stopSearchScrape(requestId);
      if (!result.stopRequested) {
        setIsStoppingScrape(false);
        setNotice(null);
        setError('Could not stop the active scrape. Try again in a moment.');
      }
    } catch (stopError) {
      setIsStoppingScrape(false);
      setNotice(null);
      setError(
        stopError instanceof Error ? stopError.message : 'Could not stop the active scrape.'
      );
    }
  };

  const getMasterResumeId = (): string => {
    const masterResumeId =
      typeof window !== 'undefined' ? window.localStorage.getItem('master_resume_id') : null;
    if (!masterResumeId) {
      throw new Error('Set your master resume first on Dashboard, then generate from an offer.');
    }
    return masterResumeId;
  };

  const fetchCompanyInfoForOffer = async (
    offer: SearchOffer,
    options?: { reportError?: boolean }
  ): Promise<SearchCompanyInfoResponse> => {
    const runtimeKey = getOfferRuntimeKey(offer);
    const cachedCompanyInfo = companyInfoByOfferKey[runtimeKey];
    if (cachedCompanyInfo) {
      return cachedCompanyInfo;
    }

    const reportError = options?.reportError ?? true;
    setCompanyInfoLoadingKey(runtimeKey);
    if (reportError) {
      setCompanyInfoErrorsByOfferKey((previous) => {
        const next = { ...previous };
        delete next[runtimeKey];
        return next;
      });
    }

    try {
      const companyInfo = await getCompanyInfoFromSearchOffer({
        id: offer.id,
        source: offer.source,
        title: offer.title,
        company: offer.company,
        location: offer.location,
        salary: offer.salary,
        url: offer.url,
        skills: offer.skills,
      });
      setCompanyInfoByOfferKey((previous) => ({
        ...previous,
        [runtimeKey]: companyInfo,
      }));
      updateOfferInResponse(offer, {
        alreadyGeneratedCompanyInfo: true,
      });
      return companyInfo;
    } catch (companyInfoError) {
      if (reportError) {
        setCompanyInfoErrorsByOfferKey((previous) => ({
          ...previous,
          [runtimeKey]:
            companyInfoError instanceof Error
              ? companyInfoError.message
              : 'Could not get company info for this offer.',
        }));
      }
      throw companyInfoError;
    } finally {
      setCompanyInfoLoadingKey((current) => (current === runtimeKey ? null : current));
    }
  };

  const buildCompanyInfoContextText = (companyInfo: SearchCompanyInfoResponse): string => {
    const sections = [`Company summary:\n${companyInfo.summary.trim()}`];

    if (companyInfo.highlights.length > 0) {
      sections.push(
        `Company highlights:\n${companyInfo.highlights
          .slice(0, 5)
          .map((highlight) => `- ${highlight}`)
          .join('\n')}`
      );
    }

    if (companyInfo.evidence.length > 0) {
      sections.push(
        `Supporting company context:\n${companyInfo.evidence
          .slice(0, 2)
          .map((item) => `- ${item.snippet}`)
          .join('\n')}`
      );
    }

    return sections.join('\n\n').trim();
  };

  const buildTailoringJobDescription = async (
    offer: SearchOffer
  ): Promise<{
    jobDescription: string;
    companyInfoAttempted: boolean;
    companyContextSource: CompanyContextSource;
  }> => {
    let companyContext: string | null = null;
    let companyInfoAttempted = false;

    if (fetchAdditionalCompanyInfo) {
      companyInfoAttempted = true;
      try {
        const companyInfo = await fetchCompanyInfoForOffer(offer, { reportError: false });
        companyContext = buildCompanyInfoContextText(companyInfo);
      } catch (companyInfoError) {
        console.warn(
          'Could not fetch additional company info for offer generation:',
          companyInfoError
        );
      }
    }

    const generated = await generateJobDescriptionFromSearchOffer({
      id: offer.id,
      source: offer.source,
      title: offer.title,
      company: offer.company,
      location: offer.location,
      salary: offer.salary,
      url: offer.url,
      skills: offer.skills,
      companyContext,
    });

    return {
      jobDescription: generated.jobDescription,
      companyInfoAttempted,
      companyContextSource: generated.companyContextSource,
    };
  };

  const generateResumeForOffer = async (
    offer: SearchOffer,
    masterResumeId: string
  ): Promise<{
    companyInfoAttempted: boolean;
    companyContextSource: CompanyContextSource;
    resumeId: string;
  }> => {
    const tailoringPayload = await buildTailoringJobDescription(offer);
    const jobId = await uploadJobDescriptions(
      [tailoringPayload.jobDescription],
      masterResumeId,
      toJobOfferMarker(createOfferResumeMarker(offer))
    );
    const improved = await improveResume(masterResumeId, jobId);
    const resumeId = improved?.data?.resume_id;
    if (!resumeId) {
      throw new Error('Resume was generated but no resume ID was returned.');
    }

    const marker = createOfferResumeMarker(offer);
    const nextMap = markOfferResumeGenerated(marker, resumeId);
    setOfferResumeMap(nextMap);
    updateOfferInResponse(offer, {
      alreadyGeneratedResume: true,
      generatedResumeId: resumeId,
    });
    return {
      companyInfoAttempted: tailoringPayload.companyInfoAttempted,
      companyContextSource: tailoringPayload.companyContextSource,
      resumeId,
    };
  };

  const handleGenerateAndEditResumeFromOffer = async (offer: SearchOffer): Promise<void> => {
    const runtimeKey = getOfferRuntimeKey(offer);
    setNotice(null);
    setError(null);

    setGeneratingEditOfferKey(runtimeKey);
    try {
      getMasterResumeId();
      const tailoringPayload = await buildTailoringJobDescription(offer);

      if (typeof window !== 'undefined') {
        window.sessionStorage.setItem(
          'tailor_prefill_job_description',
          tailoringPayload.jobDescription
        );
        savePendingOfferMarker(createOfferResumeMarker(offer));
      }

      router.push('/tailor?prefill=search');
    } catch (generationError) {
      clearPendingOfferMarker();
      setError(
        generationError instanceof Error
          ? generationError.message
          : 'Could not generate job description from offer.'
      );
    } finally {
      setGeneratingEditOfferKey(null);
    }
  };

  const handleGenerateResumeFromOffer = async (offer: SearchOffer): Promise<void> => {
    const runtimeKey = getOfferRuntimeKey(offer);
    setNotice(null);
    setError(null);

    setGeneratingOfferKey(runtimeKey);
    try {
      const masterResumeId = getMasterResumeId();
      const generationResult = await generateResumeForOffer(offer, masterResumeId);
      if (generationResult.companyContextSource === 'request') {
        setNotice('Tailored resume generated from a combined offer and company description.');
      } else if (generationResult.companyContextSource === 'cache') {
        setNotice('Tailored resume generated using cached company info for this offer.');
      } else if (generationResult.companyInfoAttempted) {
        setNotice(
          'Tailored resume generated from offer data; additional company info was unavailable.'
        );
      } else {
        setNotice('Tailored resume generated for this offer.');
      }
    } catch (generationError) {
      setError(
        generationError instanceof Error
          ? generationError.message
          : 'Could not generate resume for this offer.'
      );
    } finally {
      setGeneratingOfferKey(null);
    }
  };

  const handleGetCompanyInfo = async (offer: SearchOffer): Promise<void> => {
    setError(null);
    setNotice(null);

    try {
      await fetchCompanyInfoForOffer(offer, { reportError: true });
    } catch {
      // Inline company-info error state is already updated in fetchCompanyInfoForOffer.
    }
  };

  const handleGenerateResumeForAll = async (): Promise<void> => {
    if (!displayedOffers.length) {
      setError('No offers available for bulk generation.');
      return;
    }

    if (!bulkGenerationOffers.length) {
      setNotice('All filtered offers already have generated resumes.');
      setError(null);
      return;
    }

    setNotice(null);
    setError(null);

    let masterResumeId: string;
    try {
      masterResumeId = getMasterResumeId();
    } catch (missingMasterError) {
      setError(
        missingMasterError instanceof Error
          ? missingMasterError.message
          : 'Set your master resume first on Dashboard, then try again.'
      );
      return;
    }

    const offersToProcess = bulkGenerationOffers;
    let processed = 0;
    let success = 0;
    let failed = 0;
    const failedOffers: string[] = [];

    setIsBulkGenerating(true);
    setBulkProgress({
      total: offersToProcess.length,
      processed,
      success,
      failed,
      currentOfferLabel: null,
    });

    for (const offer of offersToProcess) {
      const runtimeKey = getOfferRuntimeKey(offer);
      const currentOfferLabel = `${offer.title} @ ${offer.company}`;
      setGeneratingOfferKey(runtimeKey);
      setBulkProgress({
        total: offersToProcess.length,
        processed,
        success,
        failed,
        currentOfferLabel,
      });

      try {
        await generateResumeForOffer(offer, masterResumeId);
        success += 1;
      } catch (offerError) {
        failed += 1;
        if (failedOffers.length < 3) {
          failedOffers.push(
            `${offer.title}: ${offerError instanceof Error ? offerError.message : 'Unknown error'}`
          );
        }
      } finally {
        processed += 1;
        setBulkProgress({
          total: offersToProcess.length,
          processed,
          success,
          failed,
          currentOfferLabel,
        });
      }
    }

    setGeneratingOfferKey(null);
    setIsBulkGenerating(false);
    setBulkProgress({
      total: offersToProcess.length,
      processed,
      success,
      failed,
      currentOfferLabel: null,
    });

    if (failed > 0) {
      const suffix = failedOffers.length ? ` First errors: ${failedOffers.join(' | ')}` : '';
      setError(`Bulk generation finished. Success: ${success}, failed: ${failed}.${suffix}`);
      return;
    }

    if (fetchAdditionalCompanyInfo) {
      setNotice(
        `Bulk generation finished. Generated ${success} tailored resumes using combined offer and company descriptions when available.`
      );
      return;
    }

    setNotice(`Bulk generation finished. Generated ${success} tailored resumes.`);
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const startedAt = Date.now();
    const requestId = createScrapeRequestId();

    activeEventSourceRef.current?.close();
    activeEventSourceRef.current = null;
    activeScrapeRequestIdRef.current = requestId;

    setLoading(true);
    setIsStoppingScrape(false);
    setIsBulkGenerating(false);
    setBulkProgress(null);
    setNotice(null);
    setError(null);
    setResponse(null);
    setCompanyInfoByOfferKey({});
    setCompanyInfoErrorsByOfferKey({});
    setCompanyInfoLoadingKey(null);
    setProgressPercent(0);
    setProgressMessage('Starting scrape...');
    setProgressBySource({ ...EMPTY_SEARCH_SCRAPED_BY_SOURCE });
    setActiveScrapeRequestId(requestId);
    setRequestStartedAt(startedAt);
    setElapsedRequestMs(0);

    try {
      if (typeof EventSource === 'undefined') {
        await runScrapeFallback(requestId);
        return;
      }

      await new Promise<void>((resolve, reject) => {
        let completed = false;
        const streamUrl = buildSearchScrapeUrl(buildScrapeParams(requestId), true);
        const eventSource = new EventSource(streamUrl);
        activeEventSourceRef.current = eventSource;

        eventSource.addEventListener('progress', (rawEvent) => {
          try {
            const progressEvent = normalizeSearchProgressEvent(
              JSON.parse(
                (rawEvent as MessageEvent).data
              ) as SearchProgressEvent
            );
            setProgressPercent(Math.max(0, Math.min(100, progressEvent.progressPercent)));
            setProgressMessage(progressEvent.message);
            setProgressBySource(progressEvent.scrapedBySource);
          } catch {
            // ignore malformed progress events
          }
        });

        eventSource.addEventListener('done', (rawEvent) => {
          completed = true;
          eventSource.close();
          if (activeEventSourceRef.current === eventSource) {
            activeEventSourceRef.current = null;
          }

          try {
            const done = JSON.parse((rawEvent as MessageEvent).data) as SearchDoneEvent;
            const normalizedPayload = normalizeSearchScrapeResponse(
              done.payload,
              buildRequestedScrapeFallback(sourceLimits)
            );
            if (normalizedPayload === null) {
              reject(new Error('Could not normalize scrape stream result.'));
              return;
            }
            setResponse(normalizedPayload);
            setProgressPercent(100);
            setProgressMessage(
              normalizedPayload.meta.wasStopped
                ? 'Scraping stopped. Returned partial results.'
                : 'Scraping completed'
            );
            setProgressBySource(normalizedPayload.meta.scrapedBySource);
            if (normalizedPayload.meta.wasStopped) {
              setNotice('Scraping stopped. Returned partial results.');
            }
            if (done.status >= 400) {
              setError('Scrape finished with source errors.');
            }
            resolve();
          } catch {
            reject(new Error('Could not read scrape stream result.'));
          }
        });

        eventSource.addEventListener('error', () => {
          if (completed) {
            return;
          }
          eventSource.close();
          if (activeEventSourceRef.current === eventSource) {
            activeEventSourceRef.current = null;
          }
          reject(new Error('Could not connect to scrape stream.'));
        });
      });
    } catch (scrapeError) {
      setError(scrapeError instanceof Error ? scrapeError.message : 'Could not fetch offers.');
    } finally {
      setElapsedRequestMs(Date.now() - startedAt);
      const activeEventSource = activeEventSourceRef.current as { close: () => void } | null;
      if (activeEventSource !== null) {
        activeEventSource.close();
      }
      activeEventSourceRef.current = null;
      activeScrapeRequestIdRef.current = null;
      setActiveScrapeRequestId(null);
      setIsStoppingScrape(false);
      setLoading(false);
    }
  };

  return (
    <div
      className="min-h-screen w-full bg-[#F0F0E8] p-4 md:p-8"
      style={{
        backgroundImage:
          'linear-gradient(rgba(29, 78, 216, 0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(29, 78, 216, 0.08) 1px, transparent 1px)',
        backgroundSize: '40px 40px',
      }}
    >
      <div className="mx-auto max-w-7xl space-y-6">
        <Card className="border-2 border-black bg-white shadow-[8px_8px_0px_0px_#000000]">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="space-y-2">
              <p className="font-mono text-xs uppercase tracking-wider text-[#1D4ED8]">
                // provider search pipeline
              </p>
              <CardTitle className="text-3xl md:text-4xl">Search</CardTitle>
              <CardDescription className="font-sans text-sm text-[#4B5563]">
                Scraping ofert z providerow: NoFluffJobs, JustJoinIT, Bulldogjob, theprotocol.it,
                Solid.jobs, Pracuj.pl, RocketJobs, OLX Praca, Indeed, Glassdoor, ZipRecruiter,
                CareerBuilder.
              </CardDescription>
            </div>
            <Link href="/dashboard">
              <Button variant="outline" size="sm">
                <ArrowLeft className="h-4 w-4" />
                Dashboard
              </Button>
            </Link>
          </div>
        </Card>

        <Card className="border-2 border-black bg-white shadow-[6px_6px_0px_0px_#000000]">
          <form onSubmit={onSubmit} className="space-y-5">
            <div className="grid gap-4 md:grid-cols-[140px_1fr_160px]">
              <label className="space-y-1">
                <span className="font-mono text-xs uppercase tracking-wider text-black">Limit</span>
                <Input
                  type="number"
                  min={1}
                  max={10000}
                  value={limit}
                  onChange={(event) => {
                    const parsed = Number.parseInt(event.target.value || '1', 10);
                    setLimit(Number.isFinite(parsed) ? Math.max(1, parsed) : 1);
                  }}
                />
              </label>

              <label className="space-y-1">
                <span className="font-mono text-xs uppercase tracking-wider text-black">
                  Keywords
                </span>
                <Input
                  type="text"
                  value={keywords}
                  onChange={(event) => setKeywords(event.target.value)}
                  placeholder="react,node,typescript"
                />
              </label>

              <label className="space-y-1">
                <span className="font-mono text-xs uppercase tracking-wider text-black">
                  Timeout (s)
                </span>
                <Input
                  type="number"
                  min={MIN_SCRAPE_TIMEOUT_SECONDS}
                  max={MAX_SCRAPE_TIMEOUT_SECONDS}
                  value={scrapeTimeoutSeconds}
                  onChange={(event) => setScrapeTimeoutSeconds(event.target.value)}
                  placeholder="10"
                />
              </label>
            </div>

            <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
              Timeout applies per source scrape request. Range: {MIN_SCRAPE_TIMEOUT_SECONDS}-
              {MAX_SCRAPE_TIMEOUT_SECONDS}s.
            </p>

            <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
              {SOURCE_CONFIG.map((source) => (
                <label key={source.key} className="space-y-1">
                  <span className="font-mono text-xs uppercase tracking-wider text-black">
                    {source.label}
                  </span>
                  <Input
                    type="text"
                    value={sourceLimits[source.key]}
                    onChange={(event) =>
                      setSourceLimits((previous) => ({
                        ...previous,
                        [source.key]: event.target.value,
                      }))
                    }
                    placeholder='np. "300" lub "max"'
                  />
                </label>
              ))}
            </div>

            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-5">
                <label className="flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-black">
                  <input
                    type="checkbox"
                    checked={keywordMode === 'or'}
                    onChange={(event) => setKeywordMode(event.target.checked ? 'or' : 'and')}
                    className="h-4 w-4 rounded-none border border-black"
                  />
                  Keyword mode OR
                </label>
                <label className="flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-black">
                  <input
                    type="checkbox"
                    checked={salaryRangeOnly}
                    onChange={(event) => setSalaryRangeOnly(event.target.checked)}
                    className="h-4 w-4 rounded-none border border-black"
                  />
                  Salary range only
                </label>
              </div>

              <div className="border-2 border-black bg-[#F0F0E8] p-4 shadow-[4px_4px_0px_0px_#000000]">
                <ToggleSwitch
                  checked={fetchAdditionalCompanyInfo}
                  onCheckedChange={setFetchAdditionalCompanyInfo}
                  disabled={Boolean(
                    isBulkGenerating ||
                    generatingOfferKey ||
                    generatingEditOfferKey ||
                    companyInfoLoadingKey
                  )}
                  label="Fetch additional company info"
                  description="Fetch additional company info before generating or prefilling a tailored resume."
                  className="border-0 bg-transparent p-0 shadow-none"
                />
              </div>

              <div className="flex flex-col gap-3 md:flex-row md:flex-wrap md:items-end">
                <Button
                  type="submit"
                  disabled={loading || isBulkGenerating}
                  className="min-w-[180px]"
                >
                  {loading ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Scraping
                    </>
                  ) : (
                    <>
                      <SearchIcon className="h-4 w-4" />
                      Run Search
                    </>
                  )}
                </Button>
                <div className="grid gap-3 sm:grid-cols-2 md:min-w-[340px]">
                  <label className="space-y-1">
                    <span className="font-mono text-xs uppercase tracking-wider text-black">
                      Sort By
                    </span>
                    <select
                      className="h-10 w-full rounded-none border border-black bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-blue-700 disabled:cursor-not-allowed disabled:bg-[#E5E5E0] disabled:text-[#4B5563]"
                      value={sortBy}
                      disabled={loading}
                      onChange={(event) => setSortBy(event.target.value as OfferSortBy)}
                    >
                      <option value="relevance">relevance</option>
                      <option value="name">name</option>
                      <option value="salary">salary</option>
                    </select>
                  </label>

                  <label className="space-y-1">
                    <span className="font-mono text-xs uppercase tracking-wider text-black">
                      Direction
                    </span>
                    <select
                      className="h-10 w-full rounded-none border border-black bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-blue-700 disabled:cursor-not-allowed disabled:bg-[#E5E5E0] disabled:text-[#4B5563]"
                      value={sortDirection}
                      disabled={loading}
                      onChange={(event) =>
                        setSortDirection(event.target.value as OfferSortDirection)
                      }
                    >
                      <option value="asc">asc</option>
                      <option value="desc">desc</option>
                    </select>
                  </label>
                </div>
                {loading && (
                  <Button
                    type="button"
                    variant="warning"
                    onClick={() => void handleStopScraping()}
                    disabled={isStoppingScrape || !activeScrapeRequestId}
                    className="min-w-[180px]"
                  >
                    {isStoppingScrape ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Stopping
                      </>
                    ) : (
                      'Stop Scrape'
                    )}
                  </Button>
                )}
              </div>
            </div>
          </form>
        </Card>

        {loading && (
          <Card className="border-2 border-black bg-white shadow-[6px_6px_0px_0px_#000000]">
            <div className="space-y-3">
              <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <p className="font-mono text-xs uppercase tracking-wider text-black">
                  {progressMessage || 'Scraping in progress'}
                </p>
                <p className="font-mono text-xs uppercase tracking-wider text-[#1D4ED8]">
                  Elapsed: {formatElapsedDuration(elapsedRequestMs)}
                </p>
              </div>
              <div className="h-3 w-full border border-black bg-[#E5E5E0]">
                <div
                  className="h-full bg-[#1D4ED8] transition-all"
                  style={{ width: `${Math.max(1, Math.min(100, progressPercent))}%` }}
                />
              </div>
              <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-6">
                {SOURCE_CONFIG.map((source) => (
                  <p
                    key={source.key}
                    className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]"
                  >
                    {source.label}: {progressBySource[source.key]}
                  </p>
                ))}
              </div>
            </div>
          </Card>
        )}

        {error && (
          <Card className="border-2 border-[#DC2626] bg-red-50 shadow-[6px_6px_0px_0px_#000000]">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 text-[#DC2626]" />
              <p className="font-mono text-xs uppercase tracking-wider text-[#B91C1C]">{error}</p>
            </div>
          </Card>
        )}

        {notice && (
          <Card className="border-2 border-[#15803D] bg-green-50 shadow-[6px_6px_0px_0px_#000000]">
            <p className="font-mono text-xs uppercase tracking-wider text-[#166534]">{notice}</p>
          </Card>
        )}

        {response && (
          <>
            <Card className="border-2 border-black bg-white shadow-[6px_6px_0px_0px_#000000]">
              <div className="grid gap-2 md:grid-cols-4">
                <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                  Returned: {response.meta.returnedCount}
                </p>
                <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                  Scraped: {response.meta.scrapedTotalCount}
                </p>
                <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                  Dedupe: {response.meta.dedupedScrapedCount}
                </p>
                <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                  Duration: {response.meta.durationMs}ms
                </p>
              </div>
              {response.meta.wasStopped && (
                <p className="mt-3 font-mono text-[11px] uppercase tracking-wider text-[#C2410C]">
                  Result type: partial (stopped by user)
                </p>
              )}
              <div className="mt-4 grid gap-2 md:grid-cols-3 xl:grid-cols-6">
                {SOURCE_CONFIG.map((source) => (
                  <p
                    key={source.key}
                    className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]"
                  >
                    {source.label}:{' '}
                    {formatSourceScrapeCount(
                      response.meta.scrapedBySource[source.key],
                      response.meta.requestedScrapeBySource[source.key]
                    )}
                  </p>
                ))}
              </div>
              <div className="mt-4 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                <Button
                  type="button"
                  onClick={() => void handleGenerateResumeForAll()}
                  disabled={
                    isBulkGenerating ||
                    Boolean(generatingOfferKey || generatingEditOfferKey) ||
                    bulkGenerationOffers.length === 0
                  }
                >
                  {isBulkGenerating ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Generating All Resumes
                    </>
                  ) : (
                    `Generate Resume For All Filtered Results (${bulkGenerationOffers.length})`
                  )}
                </Button>
                {bulkProgress && (
                  <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                    {bulkProgress.currentOfferLabel
                      ? `Now: ${bulkProgress.currentOfferLabel} | `
                      : ''}
                    Progress: {bulkProgress.processed}/{bulkProgress.total} | Success:{' '}
                    {bulkProgress.success} | Failed: {bulkProgress.failed}
                  </p>
                )}
              </div>
            </Card>

            <Card className="border-2 border-black bg-white shadow-[6px_6px_0px_0px_#000000]">
              <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]">
                <label className="space-y-1">
                  <span className="font-mono text-xs uppercase tracking-wider text-black">
                    Search in current table
                  </span>
                  <Input
                    type="text"
                    value={tableSearchText}
                    onChange={(event) => setTableSearchText(event.target.value)}
                    placeholder='np. "react warszawa"'
                  />
                </label>
                <label className="space-y-1">
                  <span className="font-mono text-xs uppercase tracking-wider text-black">
                    Page size
                  </span>
                  <select
                    className="h-10 w-full rounded-none border border-black bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-blue-700"
                    value={String(pageSize)}
                    onChange={(event) => {
                      const value = event.target.value;
                      setPageSize(
                        value === 'max' ? 'max' : (Number.parseInt(value, 10) as PageSizeOption)
                      );
                    }}
                  >
                    {PAGE_SIZE_OPTIONS.map((option) => (
                      <option key={String(option)} value={String(option)}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="space-y-2 md:col-span-2">
                  <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
                    <span className="font-mono text-xs uppercase tracking-wider text-black">
                      Work mode
                    </span>
                    <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                      {selectedWorkModes.length === 0
                        ? 'Showing all work modes'
                        : `Selected: ${selectedWorkModes
                            .map((workMode) => formatWorkModeLabel(workMode))
                            .join(', ')}`}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {WORK_MODE_OPTIONS.map((option) => {
                      const isSelected = selectedWorkModes.includes(option.value);
                      const count = workModeCounts[option.value];

                      return (
                        <button
                          key={option.value}
                          type="button"
                          onClick={() =>
                            setSelectedWorkModes((previous) =>
                              previous.includes(option.value)
                                ? previous.filter((item) => item !== option.value)
                                : [...previous, option.value]
                            )
                          }
                          disabled={count === 0 && !isSelected}
                          className={`rounded-none border-2 border-black px-3 py-2 font-mono text-[11px] uppercase tracking-wider shadow-[2px_2px_0px_0px_#000000] transition-all hover:translate-y-[1px] hover:translate-x-[1px] hover:shadow-none disabled:cursor-not-allowed disabled:bg-white disabled:text-[#4B5563] disabled:shadow-none ${
                            isSelected ? option.activeClassName : 'bg-[#F0F0E8] text-black'
                          }`}
                        >
                          {option.label} ({count})
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
              <div className="mt-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <label className="flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-black">
                  <input
                    type="checkbox"
                    checked={hideAppliedOffers}
                    onChange={(event) => setHideAppliedOffers(event.target.checked)}
                    className="h-4 w-4 rounded-none border border-black"
                  />
                  Hide offers with generated resume ({appliedOffersCount})
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => setCurrentPage((previousPage) => Math.max(1, previousPage - 1))}
                    disabled={currentPage <= 1 || totalPages <= 1}
                  >
                    Previous
                  </Button>
                  <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                    Page {displayedOffers.length === 0 ? 0 : currentPage}/
                    {displayedOffers.length === 0 ? 0 : totalPages}
                  </p>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      setCurrentPage((previousPage) => Math.min(totalPages, previousPage + 1))
                    }
                    disabled={currentPage >= totalPages || totalPages <= 1}
                  >
                    Next
                  </Button>
                </div>
              </div>
              <div className="mt-2 flex flex-col gap-1 font-mono text-[11px] uppercase tracking-wider text-[#4B5563] md:flex-row md:items-center md:justify-between">
                <p>
                  Visible offers: {displayedOffers.length}/{response.data.length}
                </p>
                <p>
                  Showing: {pageRangeStart}-{pageRangeEnd} | Ready for bulk generation:{' '}
                  {bulkGenerationOffers.length}
                </p>
              </div>
            </Card>

            {response.errors.length > 0 && (
              <Card className="border-2 border-[#F97316] bg-orange-50 shadow-[6px_6px_0px_0px_#000000]">
                <div className="space-y-2">
                  <p className="font-mono text-xs uppercase tracking-wider text-[#C2410C]">
                    Partial source errors
                  </p>
                  {response.errors.map((item, index) => (
                    <p
                      key={`${item.source}-${index}`}
                      className="font-mono text-[11px] uppercase tracking-wider text-[#9A3412]"
                    >
                      {item.source}: {item.message}
                    </p>
                  ))}
                </div>
              </Card>
            )}

            {displayedOffers.length === 0 ? (
              <Card className="border-2 border-black bg-white shadow-[6px_6px_0px_0px_#000000]">
                <p className="font-mono text-xs uppercase tracking-wider text-[#4B5563]">
                  No offers match current filters.
                </p>
              </Card>
            ) : (
              <div className="space-y-3">
                {pagedOffers.map((offer) => {
                  const runtimeKey = getOfferRuntimeKey(offer);
                  const offerWorkMode = getOfferWorkMode(offer);
                  const alreadyApplied = hasGeneratedResumeForOffer(offer);
                  const alreadyGeneratedCompanyInfo = hasGeneratedCompanyInfoForOffer(offer);
                  const companyInfo = companyInfoByOfferKey[runtimeKey];
                  const companyInfoError = companyInfoErrorsByOfferKey[runtimeKey];
                  const isCompanyInfoLoading = companyInfoLoadingKey === runtimeKey;
                  const hasStatusBadges = alreadyApplied || alreadyGeneratedCompanyInfo;

                  return (
                    <Card
                      key={`${offer.source}-${offer.id}-${offer.url}`}
                      className="border-2 border-black bg-white shadow-[4px_4px_0px_0px_#000000]"
                    >
                      <div className={`relative space-y-2 ${hasStatusBadges ? 'pt-12' : ''}`}>
                        {hasStatusBadges && (
                          <div className="absolute right-0 top-0 flex flex-col items-end gap-1">
                            {alreadyApplied && (
                              <span className="border border-black bg-[#15803D] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-white">
                                Already Applied
                              </span>
                            )}
                            {alreadyGeneratedCompanyInfo && (
                              <span className="border border-black bg-[#1D4ED8] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-white">
                                Already Generated Company Info
                              </span>
                            )}
                          </div>
                        )}
                        <p className="font-mono text-[11px] uppercase tracking-wider text-[#1D4ED8]">
                          {offer.source}
                        </p>
                        <h3 className="font-serif text-2xl leading-tight">{offer.title}</h3>
                        <p className="font-sans text-sm text-[#4B5563]">
                          {offer.company}
                          {offer.location ? ` - ${offer.location}` : ''}
                        </p>
                        {(offer.salary || offerWorkMode !== 'unknown') && (
                          <div className="flex flex-wrap items-center gap-2">
                            {offer.salary && (
                              <p className="font-mono text-xs uppercase tracking-wider text-[#15803D]">
                                {offer.salary}
                              </p>
                            )}
                            {offerWorkMode !== 'unknown' && (
                              <span className="border border-black bg-[#F0F0E8] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-black">
                                {formatWorkModeLabel(offerWorkMode)}
                              </span>
                            )}
                          </div>
                        )}
                        {offer.skills.length > 0 && (
                          <p className="font-sans text-sm text-[#4B5563]">
                            Skills: {offer.skills.join(', ')}
                          </p>
                        )}
                        {offer.matchedKeywords.length > 0 && (
                          <p className="font-mono text-[11px] uppercase tracking-wider text-[#1D4ED8]">
                            Match: {offer.matchedKeywords.join(', ')}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-2">
                          <Button
                            type="button"
                            size="sm"
                            onClick={() => void handleGenerateAndEditResumeFromOffer(offer)}
                            disabled={Boolean(
                              isBulkGenerating || generatingOfferKey || generatingEditOfferKey
                            )}
                          >
                            {generatingEditOfferKey === runtimeKey ? (
                              <>
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                Preparing Editor
                              </>
                            ) : (
                              'Generate And Edit Resume'
                            )}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            onClick={() => void handleGenerateResumeFromOffer(offer)}
                            disabled={Boolean(
                              isBulkGenerating || generatingOfferKey || generatingEditOfferKey
                            )}
                          >
                            {generatingOfferKey === runtimeKey ? (
                              <>
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                Generating Resume
                              </>
                            ) : (
                              'Generate Resume'
                            )}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="secondary"
                            onClick={() => void handleGetCompanyInfo(offer)}
                            disabled={Boolean(
                              isBulkGenerating ||
                              generatingOfferKey ||
                              generatingEditOfferKey ||
                              companyInfoLoadingKey
                            )}
                          >
                            {isCompanyInfoLoading ? (
                              <>
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                Getting Company Info
                              </>
                            ) : (
                              'Get Company Info'
                            )}
                          </Button>
                          <a
                            href={offer.url}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="inline-flex items-center gap-2 border border-black bg-[#1D4ED8] px-3 py-1 font-mono text-[11px] uppercase tracking-wider text-white shadow-[2px_2px_0px_0px_#000000] transition-all hover:translate-y-[1px] hover:translate-x-[1px] hover:shadow-none"
                          >
                            Open Offer
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        </div>
                        {(companyInfo || companyInfoError || isCompanyInfoLoading) && (
                          <div className="mt-4 border-t-2 border-black pt-4">
                            <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(240px,1fr)]">
                              <div className="space-y-3">
                                <div className="space-y-1">
                                  <p className="font-mono text-[11px] uppercase tracking-wider text-[#1D4ED8]">
                                    Company Intelligence
                                  </p>
                                  {isCompanyInfoLoading ? (
                                    <p className="font-sans text-sm text-[#4B5563]">
                                      Crawling the company site, chunking content, and extracting
                                      the most relevant fragments.
                                    </p>
                                  ) : companyInfoError ? (
                                    <p className="font-sans text-sm text-[#DC2626]">
                                      {companyInfoError}
                                    </p>
                                  ) : (
                                    <p className="font-sans text-sm leading-6 text-black">
                                      {companyInfo?.summary ?? ''}
                                    </p>
                                  )}
                                </div>

                                {companyInfo && companyInfo.highlights.length > 0 && (
                                  <ul className="space-y-2">
                                    {companyInfo.highlights.map((highlight, index) => (
                                      <li
                                        key={`${runtimeKey}-highlight-${index}`}
                                        className="border border-black bg-[#F0F0E8] px-3 py-2 font-sans text-sm text-black"
                                      >
                                        {highlight}
                                      </li>
                                    ))}
                                  </ul>
                                )}

                                {companyInfo && companyInfo.evidence.length > 0 && (
                                  <div className="space-y-2">
                                    <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                                      Evidence
                                    </p>
                                    {companyInfo.evidence.map((item, index) => (
                                      <div
                                        key={`${runtimeKey}-evidence-${index}`}
                                        className="border border-black px-3 py-2"
                                      >
                                        <p className="font-mono text-[11px] uppercase tracking-wider text-[#1D4ED8]">
                                          {item.title || item.url}
                                        </p>
                                        <p className="mt-1 font-sans text-sm text-[#4B5563]">
                                          {item.snippet}
                                        </p>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>

                              {companyInfo && (
                                <div className="space-y-3 border-2 border-black bg-[#F0F0E8] p-4">
                                  <div className="space-y-1">
                                    <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                                      Resolved Website
                                    </p>
                                    {companyInfo.websiteUrl ? (
                                      <a
                                        href={companyInfo.websiteUrl}
                                        target="_blank"
                                        rel="noreferrer noopener"
                                        className="font-sans text-sm text-[#1D4ED8] underline"
                                      >
                                        {companyInfo.websiteUrl}
                                      </a>
                                    ) : (
                                      <p className="font-sans text-sm text-[#4B5563]">
                                        Not resolved
                                      </p>
                                    )}
                                  </div>
                                  <div className="space-y-1">
                                    <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                                      Crawl Stats
                                    </p>
                                    <p className="font-sans text-sm text-black">
                                      Pages: {companyInfo.stats.pagesVisited}
                                    </p>
                                    <p className="font-sans text-sm text-black">
                                      Chunks: {companyInfo.stats.chunksIndexed}
                                    </p>
                                    <p className="font-sans text-sm text-black">
                                      Top-k: {companyInfo.stats.relevantChunks}
                                    </p>
                                    <p className="font-sans text-sm text-black">
                                      Retrieval: {companyInfo.stats.retrievalMethod}
                                    </p>
                                    <p className="font-sans text-sm text-black">
                                      LLM extraction: {companyInfo.stats.usedLlm ? 'yes' : 'no'}
                                    </p>
                                    <p className="font-sans text-sm text-black">
                                      Found via: {companyInfo.websiteFoundVia.replace('_', ' ')}
                                    </p>
                                  </div>
                                  {companyInfo.sourcePages.length > 0 && (
                                    <div className="space-y-1">
                                      <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                                        Source Pages
                                      </p>
                                      {companyInfo.sourcePages.map((page, index) => (
                                        <a
                                          key={`${runtimeKey}-source-page-${index}`}
                                          href={page.url}
                                          target="_blank"
                                          rel="noreferrer noopener"
                                          className="block font-sans text-sm text-[#1D4ED8] underline"
                                        >
                                          {page.title || page.url}
                                        </a>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    </Card>
                  );
                })}
              </div>
            )}

            {displayedOffers.length > 0 && totalPages > 1 && (
              <Card className="border-2 border-black bg-white shadow-[6px_6px_0px_0px_#000000]">
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <p className="font-mono text-[11px] uppercase tracking-wider text-[#4B5563]">
                    Page {currentPage}/{totalPages} | Showing {pageRangeStart}-{pageRangeEnd}
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        setCurrentPage((previousPage) => Math.max(1, previousPage - 1))
                      }
                      disabled={currentPage <= 1}
                    >
                      Previous
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        setCurrentPage((previousPage) => Math.min(totalPages, previousPage + 1))
                      }
                      disabled={currentPage >= totalPages}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  );
}
