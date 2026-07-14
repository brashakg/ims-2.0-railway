// ============================================================================
// IMS 2.0 - Cataloguing QC sampling API (attribution phase 2)
// ----------------------------------------------------------------------------
// Random-sample QC review workflow over /products/qc-samples. Manager-ladder
// gated on the backend (SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER/
// CATALOG_MANAGER). Import DIRECTLY from this module, not the api barrel
// (the barrel re-export fails to resolve for new methods -- TS2614).
// ============================================================================

import api from './client';

/** The 8 QC error-field checkboxes; mirrors the backend vocabulary exactly. */
export const QC_ERROR_FIELDS = [
  'category',
  'brand_model',
  'attributes',
  'pricing',
  'hsn_gst',
  'images',
  'description',
  'name',
] as const;

export type QcErrorField = (typeof QC_ERROR_FIELDS)[number];

export const QC_ERROR_FIELD_LABELS: Record<QcErrorField, string> = {
  category: 'Category',
  brand_model: 'Brand / model',
  attributes: 'Attributes',
  pricing: 'Pricing',
  hsn_gst: 'HSN / GST',
  images: 'Images',
  description: 'Description',
  name: 'Name',
};

export interface QcSampleItem {
  item_id: string;
  batch_id: string;
  product_id: string;
  product_name: string;
  sku?: string | null;
  category?: string | null;
  image_url?: string | null;
  cataloguer_id: string;
  cataloguer_name: string;
  product_created_at?: string | null;
  status: 'PENDING' | 'REVIEWED';
  sampled_at: string;
  sampled_by?: string;
  sampled_by_name?: string;
  verdict?: 'OK' | 'ERROR';
  error_fields?: string[];
  note?: string | null;
  reviewed_by?: string;
  reviewed_by_name?: string;
  reviewed_at?: string;
  overwritten_by?: string;
  overwritten_by_name?: string;
}

export interface QcBatchSummary {
  batch_id: string;
  total: number;
  reviewed: number;
  sampled_at: string | null;
}

export interface QcListResponse {
  items: QcSampleItem[];
  total: number;
  batches: QcBatchSummary[];
}

export interface QcGenerateResponse {
  batch_id: string;
  days: number;
  per_user: number;
  total_items: number;
  cataloguers: Array<{ user_id: string; name: string; sampled: number }>;
}

export const cataloguingQcApi = {
  /** Draw a new random QC batch: up to per_user products per cataloguer from
   *  the last `days` days (server excludes items already pending review). */
  generate: async (body?: { days?: number; per_user?: number }): Promise<QcGenerateResponse> => {
    const response = await api.post('/products/qc-samples/generate', body || {});
    return response.data as QcGenerateResponse;
  },

  /** List sample items (newest batch first) + per-batch progress. */
  list: async (params?: {
    status?: 'PENDING' | 'REVIEWED';
    batch_id?: string;
    cataloguer?: string;
  }): Promise<QcListResponse> => {
    const response = await api.get('/products/qc-samples', { params });
    return response.data as QcListResponse;
  },

  /** Record a verdict. Backend enforces no-self-QC (403) and immutability
   *  (409 on re-verdict; ADMIN/SUPERADMIN overwrite is stamped). */
  verdict: async (
    itemId: string,
    body: { verdict: 'OK' | 'ERROR'; error_fields?: string[]; note?: string },
  ): Promise<QcSampleItem> => {
    const response = await api.post(`/products/qc-samples/${itemId}/verdict`, body);
    return response.data as QcSampleItem;
  },
};
