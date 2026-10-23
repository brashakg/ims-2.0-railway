// ============================================================================
// IMS 2.0 - Review queue handoff (Catalog Manager -> full-page ?review editor)
// ============================================================================
// The "Needs review — imported" grid and the full-page review editor
// (/catalog/add?review=<id>) share the reviewer's working queue through ONE
// sessionStorage stash. The Catalog Manager writes it whenever the review
// segment renders/filters (ids = the current page of imported docs) and the
// drawer's "Edit everything" pins the index before navigating; the editor
// reads it for "Item N of M" + Prev/Next neighbours and keeps the index in
// step as the reviewer advances. sessionStorage (not the URL) because the ids
// list can be 48 entries long; per-tab isolation is a feature (two reviewers,
// two queues). Everything here fails soft — a missing/corrupt stash just
// degrades to the editor's single-item fallback fetch.

import { readFilters, type SortKey } from './catalogListState';

export const REVIEW_QUEUE_KEY = 'ims.review.queue';
/** The same stash, its own slot: the Catalog list's products for the edit
 *  page's Save & next / Skip / "Item N of M" (?edit=<id>). */
export const EDIT_QUEUE_KEY = 'ims.edit.queue';

export interface ReviewQueueFilters {
  search?: string;
  category?: string;
  brand?: string;
  /** The Catalog Manager grid page the ids came from (restores grid position
   *  on "Back to queue"). */
  page?: number;
  /** The review list's whole address (query string): page, Sort by, rows
   *  per page, search and filters -- "Back to queue" returns to it as it was. */
  address?: string;
}

export interface ReviewQueueStash {
  /** Imported-doc ids of the grid page the reviewer entered from. */
  ids: string[];
  /** Position of the item currently open in the editor (index into ids). */
  index: number;
  /** How many queue items precede ids[0] (i.e. (page-1)*pageSize) — feeds the
   *  global "Item N of M" numbering. */
  offset?: number;
  /** Total items in the review queue at stash time (the M in "N of M"). */
  total?: number;
  filters?: ReviewQueueFilters;
  /** Edit queue only: the list the ids came from -- its path (/catalog or
   *  /catalog/missing-photos) and the exact getProducts params it asked with
   *  (no skip/limit), so the editor fetches the list's NEXT page in the same
   *  filter and sort when this page runs out. */
  list?: { path: string; params: Record<string, unknown>; pageSize: number };
}

export function writeReviewQueue(stash: ReviewQueueStash, key = REVIEW_QUEUE_KEY): void {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(stash));
  } catch {
    /* storage full/blocked — the editor falls back to single-item fetches */
  }
}

export function readReviewQueue(key = REVIEW_QUEUE_KEY): ReviewQueueStash | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ReviewQueueStash;
    if (!parsed || !Array.isArray(parsed.ids)) return null;
    return {
      ids: parsed.ids.map((x) => String(x)).filter(Boolean),
      index: Number.isFinite(parsed.index) ? Math.max(0, Math.trunc(parsed.index)) : 0,
      offset:
        Number.isFinite(parsed.offset as number) && (parsed.offset as number) >= 0
          ? Math.trunc(parsed.offset as number)
          : undefined,
      total:
        Number.isFinite(parsed.total as number) && (parsed.total as number) >= 0
          ? Math.trunc(parsed.total as number)
          : undefined,
      filters: parsed.filters && typeof parsed.filters === 'object' ? parsed.filters : undefined,
      list:
        parsed.list && typeof parsed.list === 'object' && typeof parsed.list.path === 'string'
          ? parsed.list
          : undefined,
    };
  } catch {
    return null;
  }
}

export function clearReviewQueue(key = REVIEW_QUEUE_KEY): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* nothing to clear */
  }
}

/** Drop an id from the stash (approved / vanished) keeping offset+total
 *  honest. Returns the updated stash, or null when none existed. */
export function removeFromReviewQueue(id: string): ReviewQueueStash | null {
  const stash = readReviewQueue();
  if (!stash) return null;
  const at = stash.ids.indexOf(id);
  if (at === -1) return stash;
  const ids = stash.ids.filter((x) => x !== id);
  const next: ReviewQueueStash = {
    ...stash,
    ids,
    index: Math.min(stash.index, Math.max(0, ids.length - 1)),
    total: stash.total !== undefined ? Math.max(0, stash.total - 1) : undefined,
  };
  writeReviewQueue(next);
  return next;
}

/** The review queue moved from `?segment=review` on /catalog to its own
 *  address /catalog/review. Bookmarks, the old full-page editor's "Back to
 *  queue" links and anything else already in the wild still carry the query
 *  form, so /catalog forwards them -- keeping `page` and `focus` (and any
 *  other filter) intact, so the reviewer lands exactly where they left.
 *
 *  Returns the address to forward to, or null when this is a plain /catalog
 *  visit. Pure; `search` is a location.search string (leading '?' optional). */
export function legacyReviewRedirect(search: string): string | null {
  const sp = new URLSearchParams(search);
  if (sp.get('segment') !== 'review') return null;
  sp.delete('segment');
  const rest = sp.toString();
  return `/catalog/review${rest ? `?${rest}` : ''}`;
}

/** "Back to queue": the review list's own address as the reviewer left it
 *  (page, Sort by, rows per page, search, filters), with ?focus re-opening
 *  this product's card. A stash from before `address` existed keeps just its
 *  page. Pure. */
export function backToQueueAddress(stash: ReviewQueueStash | null, focusId: string): string {
  const f = stash?.filters;
  const sp = new URLSearchParams(
    typeof f?.address === 'string' ? f.address : f?.page && f.page > 1 ? `page=${f.page}` : ''
  );
  sp.set('focus', focusId);
  return `/catalog/review?${sp.toString()}`;
}

/** The review list's Sort by as the reviewer left it (its address; newest
 *  import first when none): the editor fetching the next waiting item walks
 *  the queue in the same order as the list. Pure. */
export function queueSort(stash: ReviewQueueStash | null): SortKey {
  const address = stash?.filters?.address;
  return readFilters(new URLSearchParams(typeof address === 'string' ? address : ''), null, 'review')
    .sort;
}

/** Back to the Catalog list the edit queue came from, as the user left it
 *  (filters, sort, page), with ?focus opening this product's card. No queue
 *  = the plain Catalog. Pure. */
export function editListAddress(stash: ReviewQueueStash | null, focusId: string): string {
  const sp = new URLSearchParams(stash?.list ? stash.filters?.address ?? '' : '');
  sp.set('focus', focusId);
  return `${stash?.list?.path ?? '/catalog'}?${sp.toString()}`;
}
