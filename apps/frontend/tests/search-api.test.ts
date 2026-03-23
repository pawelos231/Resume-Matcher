import { describe, expect, it } from 'vitest';
import {
  normalizeSearchProgressEvent,
  normalizeSearchScrapeResponse,
  type SearchProgressEvent,
  type SearchScrapeResponse,
} from '@/lib/api/search';

describe('search api normalization', () => {
  it('fills missing source counts in legacy scrape payloads', () => {
    const payload = {
      meta: {
        generatedAt: '2026-03-15T00:00:00Z',
        durationMs: 321,
        wasStopped: false,
        requestedScrapeBySource: {
          nofluffjobs: 'max',
          justjoinit: 10,
        },
        scrapedTotalCount: 3,
        scrapedBySource: {
          nofluffjobs: 1,
          justjoinit: 2,
        },
        dedupedScrapedCount: 3,
        requestedLimit: 1000,
        returnedCount: 0,
        keywords: ['react'],
        keywordMode: 'and',
        salaryRangeOnly: false,
        sortBy: 'relevance',
        sortDirection: 'asc',
      },
      data: [],
      errors: [],
    } as SearchScrapeResponse;

    const normalized = normalizeSearchScrapeResponse(payload, {
      rocketjobs: 20,
      olxpraca: 20,
      indeed: 20,
      glassdoor: 20,
      ziprecruiter: 20,
      careerbuilder: 20,
    });

    expect(normalized).not.toBeNull();
    expect(normalized?.meta.requestedScrapeBySource.nofluffjobs).toBe('max');
    expect(normalized?.meta.requestedScrapeBySource.rocketjobs).toBe(20);
    expect(normalized?.meta.scrapedBySource.nofluffjobs).toBe(1);
    expect(normalized?.meta.scrapedBySource.careerbuilder).toBe(0);
  });

  it('fills missing source counts in progress events', () => {
    const progressEvent = {
      stage: 'scraping',
      progressPercent: 50,
      message: 'Scraping legacy payload',
      requestedScrapeBySource: {
        nofluffjobs: 'max',
      },
      scrapedTotalCount: 1,
      scrapedBySource: {
        nofluffjobs: 1,
      },
    } as SearchProgressEvent;

    const normalized = normalizeSearchProgressEvent(progressEvent);

    expect(normalized.scrapedBySource.nofluffjobs).toBe(1);
    expect(normalized.scrapedBySource.rocketjobs).toBe(0);
    expect(normalized.scrapedBySource.careerbuilder).toBe(0);
  });
});
