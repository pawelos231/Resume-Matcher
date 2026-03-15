import type { SearchOffer } from '@/lib/api/search';

export const OFFER_RESUME_MAP_STORAGE_KEY = 'search_offer_resume_map_v1';
export const TAILOR_PREFILL_OFFER_MARKER_STORAGE_KEY = 'tailor_prefill_offer_resume_marker';

export type OfferResumeEntry = {
  resumeId: string;
  createdAt: string;
  source: SearchOffer['source'];
  title: string;
  company: string;
  url: string;
};

export type OfferResumeMap = Record<string, OfferResumeEntry>;

export type OfferResumeMarker = {
  offerKey: string;
  offer: Pick<SearchOffer, 'source' | 'title' | 'company' | 'url'>;
};

export function getOfferRuntimeKey(offer: Pick<SearchOffer, 'source' | 'id' | 'url'>): string {
  return `${offer.source}:${offer.id}:${offer.url}`;
}

export function createOfferResumeMarker(offer: SearchOffer): OfferResumeMarker {
  return {
    offerKey: getOfferRuntimeKey(offer),
    offer: {
      source: offer.source,
      title: offer.title,
      company: offer.company,
      url: offer.url,
    },
  };
}

function readStorageValue(key: string): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  return window.localStorage.getItem(key);
}

function writeStorageValue(key: string, value: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.localStorage.setItem(key, value);
}

export function readOfferResumeMap(): OfferResumeMap {
  const raw = readStorageValue(OFFER_RESUME_MAP_STORAGE_KEY);
  if (!raw) {
    return {};
  }

  try {
    const parsed = JSON.parse(raw) as OfferResumeMap;
    if (!parsed || typeof parsed !== 'object') {
      return {};
    }
    return parsed;
  } catch {
    return {};
  }
}

export function saveOfferResumeMap(map: OfferResumeMap): void {
  writeStorageValue(OFFER_RESUME_MAP_STORAGE_KEY, JSON.stringify(map));
}

export function removeOfferResumeEntriesByResumeIds(resumeIds: string[]): OfferResumeMap {
  if (resumeIds.length === 0) {
    return readOfferResumeMap();
  }

  const resumeIdSet = new Set(resumeIds);
  const current = readOfferResumeMap();
  const next = Object.fromEntries(
    Object.entries(current).filter(([, entry]) => !resumeIdSet.has(entry.resumeId))
  ) as OfferResumeMap;
  saveOfferResumeMap(next);
  return next;
}

export function removeOfferResumeEntriesByResumeId(resumeId: string): OfferResumeMap {
  return removeOfferResumeEntriesByResumeIds([resumeId]);
}

export function markOfferResumeGenerated(
  marker: OfferResumeMarker,
  resumeId: string
): OfferResumeMap {
  const current = readOfferResumeMap();
  const next: OfferResumeMap = {
    ...current,
    [marker.offerKey]: {
      resumeId,
      createdAt: new Date().toISOString(),
      source: marker.offer.source,
      title: marker.offer.title,
      company: marker.offer.company,
      url: marker.offer.url,
    },
  };
  saveOfferResumeMap(next);
  return next;
}

export function savePendingOfferMarker(marker: OfferResumeMarker): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.sessionStorage.setItem(TAILOR_PREFILL_OFFER_MARKER_STORAGE_KEY, JSON.stringify(marker));
}

export function readPendingOfferMarker(): OfferResumeMarker | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const raw = window.sessionStorage.getItem(TAILOR_PREFILL_OFFER_MARKER_STORAGE_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as OfferResumeMarker;
    if (!parsed || typeof parsed !== 'object' || !parsed.offerKey || !parsed.offer) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function clearPendingOfferMarker(): void {
  if (typeof window === 'undefined') {
    return;
  }
  window.sessionStorage.removeItem(TAILOR_PREFILL_OFFER_MARKER_STORAGE_KEY);
}
