'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { FormEvent, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import {
  buildSearchScrapeUrl,
  fetchSearchScrape,
  generateJobDescriptionFromSearchOffer,
  stopSearchScrape,
  type OfferSortBy,
  type OfferSortDirection,
  type OfferSource,
  type SearchDoneEvent,
  type SearchOffer,
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
];

const DEFAULT_SOURCE_LIMITS: Record<OfferSource, string> = {
  nofluffjobs: 'max',
  justjoinit: '10',
  bulldogjob: 'max',
  theprotocol: 'max',
  solidjobs: 'max',
  pracujpl: '50',
};

const EMPTY_SCRAPED_BY_SOURCE: Record<OfferSource, number> = {
  nofluffjobs: 0,
  justjoinit: 0,
  bulldogjob: 0,
  theprotocol: 0,
  solidjobs: 0,
  pracujpl: 0,
};

const MIN_SCRAPE_TIMEOUT_SECONDS = 15;
const MAX_SCRAPE_TIMEOUT_SECONDS = 600;
const PAGE_SIZE_OPTIONS = [25, 50, 100, 'max'] as const;

type PageSizeOption = (typeof PAGE_SIZE_OPTIONS)[number];

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

function filterOffers(offers: SearchOffer[], searchText: string): SearchOffer[] {
  const normalized = searchText.trim().toLowerCase();
  if (!normalized) {
    return offers;
  }

  const tokens = normalized.split(/\s+/).filter(Boolean);
  if (tokens.length === 0) {
    return offers;
  }

  return offers.filter((offer) => {
    const searchable = [
      offer.id,
      offer.source,
      offer.title,
      offer.company,
      offer.location,
      offer.salary ?? '',
      offer.skills.join(' '),
      offer.matchedKeywords.join(' '),
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

  const [limit, setLimit] = useState<number>(1000);
  const [keywords, setKeywords] = useState<string>('react,node,typescript');
  const [keywordMode, setKeywordMode] = useState<'and' | 'or'>('and');
  const [salaryRangeOnly, setSalaryRangeOnly] = useState<boolean>(false);
  const [sortBy, setSortBy] = useState<OfferSortBy>('relevance');
  const [sortDirection, setSortDirection] = useState<OfferSortDirection>('asc');
  const [scrapeTimeoutSeconds, setScrapeTimeoutSeconds] = useState<string>('180');
  const [sourceLimits, setSourceLimits] =
    useState<Record<OfferSource, string>>(DEFAULT_SOURCE_LIMITS);
  const [tableSearchText, setTableSearchText] = useState<string>('');
  const [hideAppliedOffers, setHideAppliedOffers] = useState<boolean>(false);
  const [pageSize, setPageSize] = useState<PageSizeOption>(50);
  const [currentPage, setCurrentPage] = useState<number>(1);

  const [loading, setLoading] = useState<boolean>(false);
  const [isStoppingScrape, setIsStoppingScrape] = useState<boolean>(false);
  const [isBulkGenerating, setIsBulkGenerating] = useState<boolean>(false);
  const [bulkProgress, setBulkProgress] = useState<BulkGenerationProgress | null>(null);
  const [generatingEditOfferKey, setGeneratingEditOfferKey] = useState<string | null>(null);
  const [generatingOfferKey, setGeneratingOfferKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<SearchScrapeResponse | null>(null);
  const [offerResumeMap, setOfferResumeMap] = useState<OfferResumeMap>({});
  const [progressPercent, setProgressPercent] = useState<number>(0);
  const [progressMessage, setProgressMessage] = useState<string>('');
  const [progressBySource, setProgressBySource] = useState<Record<OfferSource, number>>({
    ...EMPTY_SCRAPED_BY_SOURCE,
  });
  const [activeScrapeRequestId, setActiveScrapeRequestId] = useState<string | null>(null);
  const [requestStartedAt, setRequestStartedAt] = useState<number | null>(null);
  const [elapsedRequestMs, setElapsedRequestMs] = useState<number>(0);
  const deferredTableSearchText = useDeferredValue(tableSearchText);

  useEffect(() => {
    setOfferResumeMap(readOfferResumeMap());
  }, []);

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

  const sortedOffers = useMemo(
    () => sortOffers(response?.data ?? [], sortBy, sortDirection),
    [response, sortBy, sortDirection]
  );

  const statusFilteredOffers = useMemo(() => {
    const offers = sortedOffers;
    if (!hideAppliedOffers) {
      return offers;
    }
    return offers.filter((offer) => !offerResumeMap[getOfferRuntimeKey(offer)]);
  }, [sortedOffers, hideAppliedOffers, offerResumeMap]);

  const appliedOffersCount = useMemo(() => {
    const offers = response?.data ?? [];
    return offers.reduce((count, offer) => {
      return count + (offerResumeMap[getOfferRuntimeKey(offer)] ? 1 : 0);
    }, 0);
  }, [response, offerResumeMap]);

  const displayedOffers = useMemo(
    () => filterOffers(statusFilteredOffers, deferredTableSearchText),
    [statusFilteredOffers, deferredTableSearchText]
  );

  const bulkGenerationOffers = useMemo(
    () => displayedOffers.filter((offer) => !offerResumeMap[getOfferRuntimeKey(offer)]),
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
    setCurrentPage(1);
  }, [deferredTableSearchText, hideAppliedOffers, pageSize, response, sortBy, sortDirection]);

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

    setResponse(fallback.payload);
    setProgressPercent(100);
    setProgressMessage(
      fallback.payload.meta.wasStopped
        ? 'Scraping stopped. Returned partial results.'
        : 'Scraping completed'
    );
    setProgressBySource(fallback.payload.meta.scrapedBySource);
    if (fallback.payload.meta.wasStopped) {
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

  const generateResumeForOffer = async (
    offer: SearchOffer,
    masterResumeId: string
  ): Promise<string> => {
    const generated = await generateJobDescriptionFromSearchOffer({
      source: offer.source,
      title: offer.title,
      company: offer.company,
      location: offer.location,
      salary: offer.salary,
      url: offer.url,
      skills: offer.skills,
    });

    const jobId = await uploadJobDescriptions([generated.jobDescription], masterResumeId);
    const improved = await improveResume(masterResumeId, jobId);
    const resumeId = improved?.data?.resume_id;
    if (!resumeId) {
      throw new Error('Resume was generated but no resume ID was returned.');
    }

    const marker = createOfferResumeMarker(offer);
    const nextMap = markOfferResumeGenerated(marker, resumeId);
    setOfferResumeMap(nextMap);
    return resumeId;
  };

  const handleGenerateAndEditResumeFromOffer = async (offer: SearchOffer): Promise<void> => {
    const runtimeKey = getOfferRuntimeKey(offer);
    setNotice(null);
    setError(null);

    setGeneratingEditOfferKey(runtimeKey);
    try {
      getMasterResumeId();
      const generated = await generateJobDescriptionFromSearchOffer({
        source: offer.source,
        title: offer.title,
        company: offer.company,
        location: offer.location,
        salary: offer.salary,
        url: offer.url,
        skills: offer.skills,
      });

      if (typeof window !== 'undefined') {
        window.sessionStorage.setItem('tailor_prefill_job_description', generated.jobDescription);
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
      await generateResumeForOffer(offer, masterResumeId);
      setNotice('Tailored resume generated for this offer.');
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
    setProgressPercent(0);
    setProgressMessage('Starting scrape...');
    setProgressBySource({ ...EMPTY_SCRAPED_BY_SOURCE });
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
            const progressEvent = JSON.parse(
              (rawEvent as MessageEvent).data
            ) as SearchProgressEvent;
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
            setResponse(done.payload);
            setProgressPercent(100);
            setProgressMessage(
              done.payload.meta.wasStopped
                ? 'Scraping stopped. Returned partial results.'
                : 'Scraping completed'
            );
            setProgressBySource(done.payload.meta.scrapedBySource);
            if (done.payload.meta.wasStopped) {
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
                Solid.jobs, Pracuj.pl.
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
            <div className="grid gap-4 md:grid-cols-[140px_1fr_160px_160px_140px]">
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
                  placeholder="180"
                />
              </label>

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
                  onChange={(event) => setSortDirection(event.target.value as OfferSortDirection)}
                >
                  <option value="asc">asc</option>
                  <option value="desc">desc</option>
                </select>
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

            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
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
                  const alreadyApplied = Boolean(offerResumeMap[runtimeKey]);

                  return (
                    <Card
                      key={`${offer.source}-${offer.id}-${offer.url}`}
                      className="border-2 border-black bg-white shadow-[4px_4px_0px_0px_#000000]"
                    >
                      <div className={`relative space-y-2 ${alreadyApplied ? 'pt-6' : ''}`}>
                        {alreadyApplied && (
                          <span className="absolute right-0 top-0 border border-black bg-[#15803D] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-white">
                            Already Applied
                          </span>
                        )}
                        <p className="font-mono text-[11px] uppercase tracking-wider text-[#1D4ED8]">
                          {offer.source}
                        </p>
                        <h3 className="font-serif text-2xl leading-tight">{offer.title}</h3>
                        <p className="font-sans text-sm text-[#4B5563]">
                          {offer.company}
                          {offer.location ? ` - ${offer.location}` : ''}
                        </p>
                        {offer.salary && (
                          <p className="font-mono text-xs uppercase tracking-wider text-[#15803D]">
                            {offer.salary}
                          </p>
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
