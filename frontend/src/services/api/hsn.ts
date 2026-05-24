// ============================================================================
// IMS 2.0 - HSN -> GST rate master API (SUPERADMIN/ADMIN)
// ============================================================================
// CRUD for the editable HSN->GST table that POS billing resolves against
// (overrides the static canonical table in backend api/services/gst_rates.py).
// Backend: backend/api/routers/admin_catalog.py (/api/v1/admin/hsn).
// NOTE: import this module DIRECTLY (not via services/api barrel) — the barrel
// re-export does not resolve for newly-added services (TS2614 gotcha).

import api from './client';

export interface HsnRate {
  hsn_id: string;
  hsn_code: string;
  description?: string;
  gst_rate: number;
  category_hint?: string;
  is_active?: boolean;
  updated_at?: string;
}

export interface HsnRateInput {
  hsn_code: string;
  gst_rate: number;
  description?: string;
  category_hint?: string;
  is_active?: boolean;
}

export const hsnApi = {
  list: async (): Promise<{ hsn_rates: HsnRate[]; total: number }> => {
    const res = await api.get('/admin/hsn');
    return res.data;
  },
  create: async (payload: HsnRateInput): Promise<HsnRate> => {
    const res = await api.post('/admin/hsn', payload);
    return res.data;
  },
  update: async (hsnId: string, payload: Partial<HsnRateInput>): Promise<HsnRate> => {
    const res = await api.put(`/admin/hsn/${hsnId}`, payload);
    return res.data;
  },
  remove: async (hsnId: string): Promise<{ deleted: boolean }> => {
    const res = await api.delete(`/admin/hsn/${hsnId}`);
    return res.data;
  },
};
