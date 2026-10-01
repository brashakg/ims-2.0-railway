// ============================================================================
// IMS 2.0 - Online vs in-store stock reconciliation API
// ============================================================================
// Import directly (not via the services/api barrel).

import api from './client';

export interface ReconcileItem {
  sku: string;
  name?: string;
  /** IMS on-hand; null = the shop list or the stock read failed (renders an
   *  em dash, classified ONHAND_UNKNOWN — never a confident 0). */
  in_store: number | null;
  /** Live Shopify listed qty; null = not covered by the live read (renders an
   *  em dash, classified LISTED_UNKNOWN — never a confident 0). */
  online: number | null;
  /** What IMS sends to the website for the mapped shops in view (the writer's
   *  own rule: its buffer, the online block); null = unknown. */
  recommended: number | null;
  /** Units listed beyond what IMS sends, counted Shopify location by location
   *  (never listed total minus recommended total); null = unknown. */
  delta: number | null;
  status: 'OVERSELL_RISK' | 'OVER_ALLOCATED' | 'ONHAND_UNKNOWN' | 'LISTED_UNKNOWN' | 'OK' | 'SHARES_SHOPIFY_ITEM' | 'NOT_ONLINE';
}

export interface ReconcileResult {
  items: ReconcileItem[];
  summary: {
    total?: number;
    oversell_risk?: number;
    over_allocated?: number;
    onhand_unknown?: number;
    listed_unknown?: number;
    ok?: number;
    not_online?: number;
    shares_item?: number;
    oversell_risk_units?: number;
    /** The writer's own safety buffer (Shopify integration config); null when
     *  it could not be read. */
    safety_buffer?: number | null;
  };
  /** IMS catalog carries Shopify-mapped products (post-BVI truth source). */
  online_configured?: boolean;
  /** True ONLY on FULL mapped coverage of the live Shopify read; partial
   *  coverage keeps this false — see the coverage counts below. */
  listed_qty_live?: boolean;
  /** Live-read coverage: online-mapped SKUs that got a live quantity vs all. */
  listed_live_rows?: number;
  listed_mapped_rows?: number;
  /** True when IMS could not read which listings are live on Shopify: every
   *  row is "Unverified", never "Not online". */
  live_listings_unknown?: boolean;
}

export const onlineStockApi = {
  reconcile: async (params?: { store_id?: string }) => {
    const res = await api.get('/catalog/online-stock-reconcile', {
      params: params?.store_id ? { store_id: params.store_id } : {},
    });
    return res.data as ReconcileResult;
  },
};
