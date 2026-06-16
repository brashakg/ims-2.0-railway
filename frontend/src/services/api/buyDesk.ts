// ============================================================================
// IMS 2.0 — Buy Desk API
// ============================================================================
// Typed wrapper for backend/api/routers/buy_desk.py:
//   GET /buy-desk/rows  -- per-product catalog readiness + ecom state + on-hand
//                          + on-order + a netted buy signal. Read-only.
// Shapes mirror backend/api/services/buy_desk.build_row verbatim.

import api from './client';

export type EcomState = 'NOT_LISTED' | 'STAGED' | 'LIVE' | 'PUSH_LOCKED';

export interface BuyDeskReadiness {
  complete: boolean;
  missing: string[];
  blockers: string[];
  purchasable: boolean;
}

export interface BuyDeskRow {
  product_id: string;
  sku: string | null;
  name: string | null;
  brand: string | null;
  category: string | null;
  catalog_status: string | null;
  readiness: BuyDeskReadiness;
  ecom_state: EcomState;
  on_hand: number;
  on_order: number;
  /** Suggested order qty, netted against on_hand + on_order. null = no sales signal yet. */
  buy_signal: number | null;
  purchasable: boolean;
}

export interface BuyDeskRowsResponse {
  rows: BuyDeskRow[];
  total: number;
  store_id: string | null;
}

export const buyDeskApi = {
  getRows: async (params?: {
    store_id?: string;
    limit?: number;
    skip?: number;
  }): Promise<BuyDeskRowsResponse> => {
    const r = await api.get<BuyDeskRowsResponse>('/buy-desk/rows', { params });
    return r.data;
  },
};

export default buyDeskApi;
