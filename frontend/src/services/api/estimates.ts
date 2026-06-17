// ============================================================================
// IMS 2.0 - Estimates / Quotations API
// ============================================================================
// A non-binding estimate/quotation: priced line items + estimated GST + a
// validity date. No stock claim, no invoice serial. The render endpoint
// returns a self-contained printable HTML page.
//
// NOTE: import this module DIRECTLY (`from '../../services/api/estimates'`).
// The barrel re-export (services/api/index.ts) can fail to resolve for newly
// added modules (TS2614) -- a known gotcha in this repo.

import api, { getSecureApiUrl } from './client';

export interface EstimateItemInput {
  description: string;
  product_id?: string;
  category?: string;
  item_type?: string;
  hsn_code?: string;
  quantity: number;
  mrp?: number;
  offer_price: number;
  discount_percent?: number;
}

export interface EstimateCreateInput {
  customer_name?: string;
  customer_phone?: string;
  customer_address?: string;
  customer_id?: string;
  customer_gstin?: string;
  store_id?: string;
  items: EstimateItemInput[];
  cart_discount_percent?: number;
  validity_days?: number;
  valid_until?: string;
  terms?: string;
  interstate?: boolean;
}

export interface EstimateTotals {
  subtotal: number;
  taxable: number;
  tax: number;
  cart_discount_amount?: number;
  total_discount?: number;
  dominant_rate?: number;
  pricing_model?: string;
  grand_total: number;
}

export interface EstimateDocument {
  estimate_id: string;
  estimate_number: string;
  store_id?: string;
  customer_name?: string;
  customer_phone?: string;
  customer_address?: string;
  interstate?: boolean;
  items: any[];
  totals: EstimateTotals;
  cart_discount_percent?: number;
  terms?: string;
  valid_until?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
}

export interface EstimateListResponse {
  estimates: EstimateDocument[];
  total: number;
}

export const estimatesApi = {
  async create(payload: EstimateCreateInput): Promise<EstimateDocument> {
    const { data } = await api.post('/estimates', payload);
    return data;
  },

  async list(storeId?: string): Promise<EstimateListResponse> {
    const { data } = await api.get('/estimates', {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return data;
  },

  async get(estimateId: string): Promise<EstimateDocument> {
    const { data } = await api.get(`/estimates/${estimateId}`);
    return data;
  },

  // The render endpoint is JWT-protected and returns text/html. Fetch it with
  // the auth header attached, then open the result in a new tab for printing.
  async openPrint(estimateId: string): Promise<void> {
    const token = localStorage.getItem('ims_token');
    const url = `${getSecureApiUrl()}/estimates/${estimateId}/render`;
    const res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!res.ok) {
      throw new Error(`Failed to render estimate (${res.status})`);
    }
    const html = await res.text();
    const win = window.open('', '_blank');
    if (win) {
      win.document.open();
      win.document.write(html);
      win.document.close();
    }
  },
};

export default estimatesApi;
