// Sidebar / menu count badges — "Needs review (N)", "Missing photos (N)".
// ---------------------------------------------------------------------------
// The figures come from GET /catalog/online-summary (`catalog` block), the
// same server tally the Catalog screen's counts row reads — the badge and the
// page can never disagree. ONE fetch is shared by every badge on screen (the
// top menu and the phone drawer both render the nav model), cached for 60s;
// `refreshNavCounts()` re-fetches on demand (the Catalog page calls it after
// an approval so the number moves without a reload). Fail-soft: no number
// beats a wrong number, so any failure renders nothing.

import { useEffect, useState } from 'react';
import { catalogApi, type CatalogCounts } from '../../services/api/products';

export type NavBadgeKey = 'catalog-review' | 'catalog-missing-photos';

const TTL_MS = 60_000;
let cached: CatalogCounts | null = null;
let fetchedAt = 0;
let inflight: Promise<CatalogCounts | null> | null = null;
const listeners = new Set<(c: CatalogCounts | null) => void>();

function fetchCounts(): Promise<CatalogCounts | null> {
  if (!inflight) {
    inflight = catalogApi
      .getOnlineSummary()
      .then((res) => {
        cached = res?.catalog ?? null;
        fetchedAt = Date.now();
        listeners.forEach((fn) => fn(cached));
        return cached;
      })
      .catch(() => cached)
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

function loadCounts(): Promise<CatalogCounts | null> {
  if (cached && Date.now() - fetchedAt < TTL_MS) return Promise.resolve(cached);
  return fetchCounts();
}

/** Re-fetch now and push the fresh figures to every mounted badge. */
export function refreshNavCounts(): Promise<CatalogCounts | null> {
  return fetchCounts();
}

function pick(badge: NavBadgeKey, counts: CatalogCounts | null): number {
  if (!counts) return 0;
  return badge === 'catalog-review' ? counts.needs_review : counts.no_photo;
}

export function NavBadge({ badge }: { badge: NavBadgeKey }) {
  const [counts, setCounts] = useState<CatalogCounts | null>(cached);
  useEffect(() => {
    let alive = true;
    const onChange = (c: CatalogCounts | null) => {
      if (alive) setCounts(c);
    };
    listeners.add(onChange);
    void loadCounts().then(onChange);
    return () => {
      alive = false;
      listeners.delete(onChange);
    };
  }, []);
  const n = pick(badge, counts);
  if (!n) return null;
  const tone = badge === 'catalog-review' ? 'warn' : 'err';
  return (
    <span
      className={`chip ${tone} nav-badge`}
      data-testid={`nav-badge-${badge}`}
      aria-label={`${n} ${badge === 'catalog-review' ? 'waiting for review' : 'missing photos'}`}
    >
      {n > 999 ? '999+' : n}
    </span>
  );
}
