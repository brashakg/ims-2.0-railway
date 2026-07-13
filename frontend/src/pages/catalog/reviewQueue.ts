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

export const REVIEW_QUEUE_KEY = 'ims.review.queue';

export interface ReviewQueueFilters {
  search?: string;
  category?: string;
  brand?: string;
  /** The Catalog Manager grid page the ids came from (restores grid position
   *  on "Back to queue"). */
  page?: number;
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
}

export function writeReviewQueue(stash: ReviewQueueStash): void {
  try {
    window.sessionStorage.setItem(REVIEW_QUEUE_KEY, JSON.stringify(stash));
  } catch {
    /* storage full/blocked — the editor falls back to single-item fetches */
  }
}

export function readReviewQueue(): ReviewQueueStash | null {
  try {
    const raw = window.sessionStorage.getItem(REVIEW_QUEUE_KEY);
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
    };
  } catch {
    return null;
  }
}

export function clearReviewQueue(): void {
  try {
    window.sessionStorage.removeItem(REVIEW_QUEUE_KEY);
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
