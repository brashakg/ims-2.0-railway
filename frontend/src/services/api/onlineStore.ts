// ============================================================================
// IMS 2.0 - Online Store (e-commerce / BVI merge) module summary
// ============================================================================
// Phase 1 foundation: a single read for the Online Store module shell. Returns
// module status + per-section counts so the shell can show "what's live yet".
//
// GRACEFUL DEGRADATION: the backend GET /api/v1/online-store/summary endpoint is
// rolled out separately and may not exist in a stale deploy. A 404 (or any
// error) resolves to a "not yet available" placeholder rather than throwing, so
// the shell always renders. Import directly (not via the api barrel).

import api from './client';

/** Per-section count surfaced on the shell cards. All optional + nullable so a
 *  partial backend payload never breaks rendering. */
export interface OnlineStoreCounts {
  products?: number | null;
  variants?: number | null;
  collections?: number | null;
  menus?: number | null;
  images_pending_design?: number | null;
  customers?: number | null;
  orders?: number | null;
}

export interface OnlineStoreSummary {
  /** Whether the backend module endpoint answered at all. false => placeholder. */
  available: boolean;
  /** High-level module phase/status string from the backend (e.g. "FOUNDATION"). */
  status?: string | null;
  /** Whether IMS is the live Shopify writer yet (kill-switch). Default false. */
  shopify_writes_enabled?: boolean | null;
  counts?: OnlineStoreCounts | null;
  /** Optional human note from the backend (e.g. "shadow sync only"). */
  message?: string | null;
}

const PLACEHOLDER: OnlineStoreSummary = {
  available: false,
  status: 'COMING_SOON',
  shopify_writes_enabled: false,
  counts: {},
  message: null,
};

export const onlineStoreApi = {
  /** Fetch the module summary. Never throws: any error (incl. a 404 on a stale
   *  deploy) resolves to the COMING_SOON placeholder so the shell still renders. */
  getSummary: async (): Promise<OnlineStoreSummary> => {
    try {
      const res = await api.get('/online-store/summary');
      const data = (res?.data ?? {}) as Partial<OnlineStoreSummary>;
      return {
        available: true,
        status: data.status ?? 'FOUNDATION',
        shopify_writes_enabled: data.shopify_writes_enabled ?? false,
        counts: data.counts ?? {},
        message: data.message ?? null,
      };
    } catch {
      return PLACEHOLDER;
    }
  },
};
