// ============================================================================
// IMS 2.0 — Dashboard / owner-digest API client
// ----------------------------------------------------------------------------
// Backs the SUPERADMIN/ADMIN "owner digest" card on the Hub. Backend:
// GET /api/v1/admin/owner-digest (backend/api/routers/dashboard_widgets.py).
// Import this service DIRECTLY from this module (not the api barrel — the barrel
// re-export has a TS2614 quirk, per past sessions).
// ============================================================================

import api from './client';

export interface OwnerDigestToday {
  sales: number;
  collections: number;
  expenses: number;
  cash_net: number;
  orders: number;
  new_customers: number;
  pending_tasks: number;
  low_stock: number;
  out_of_stock: number;
}

export interface OwnerDigestMtd {
  sales: number;
  expenses: number;
  orders: number;
}

export interface OwnerDigestExpanded {
  by_store: Array<{ store_id: string; store_name?: string; sales: number; orders: number }>;
  payment_modes: Record<string, number>;
  low_stock_items: Array<{
    name?: string | null;
    sku?: string | null;
    qty: number;
    reorder_point: number;
    store_id?: string | null;
  }>;
  pending_task_list: Array<{
    title: string;
    priority: string;
    status?: string | null;
    due_at?: string | null;
    store_id?: string | null;
  }>;
  staff: { present_today: number; total_staff: number };
}

export interface OwnerDigest {
  date: string;
  store_id: string | null;
  today: OwnerDigestToday;
  mtd: OwnerDigestMtd;
  expanded: OwnerDigestExpanded;
}

export const dashboardApi = {
  /** Day-close owner digest (SUPERADMIN/ADMIN). storeId omitted = all stores. */
  getOwnerDigest: async (storeId?: string): Promise<OwnerDigest> => {
    const res = await api.get('/admin/owner-digest', {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return res.data as OwnerDigest;
  },
};

export default dashboardApi;
