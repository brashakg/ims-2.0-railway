// ============================================================================
// IMS 2.0 - Online vs in-store stock reconciliation API
// ============================================================================
// Import directly (not via the services/api barrel).

import api from './client';

export interface ReconcileItem {
  sku: string;
  name?: string;
  in_store: number;
  /** Live Shopify listed qty; null = not covered by the live read (renders an
   *  em dash, classified LISTED_UNKNOWN — never a confident 0). */
  online: number | null;
  recommended: number;
  delta: number | null;
  status: 'OVERSELL_RISK' | 'OVER_ALLOCATED' | 'LISTED_UNKNOWN' | 'OK' | 'NOT_ONLINE';
}

export interface ReconcileResult {
  items: ReconcileItem[];
  summary: {
    total?: number;
    oversell_risk?: number;
    over_allocated?: number;
    listed_unknown?: number;
    ok?: number;
    not_online?: number;
    oversell_risk_units?: number;
    safety_buffer?: number;
  };
  /** IMS catalog carries Shopify-mapped products (post-BVI truth source). */
  online_configured?: boolean;
  /** True ONLY on FULL mapped coverage of the live Shopify read; partial
   *  coverage keeps this false — see the coverage counts below. */
  listed_qty_live?: boolean;
  /** Live-read coverage: online-mapped SKUs that got a live quantity vs all. */
  listed_live_rows?: number;
  listed_mapped_rows?: number;
}

export const onlineStockApi = {
  reconcile: async (params?: { store_id?: string; safety_buffer?: number }) => {
    const res = await api.get('/catalog/online-stock-reconcile', {
      params: {
        ...(params?.store_id ? { store_id: params.store_id } : {}),
        safety_buffer: params?.safety_buffer ?? 0,
      },
    });
    return res.data as ReconcileResult;
  },
};
