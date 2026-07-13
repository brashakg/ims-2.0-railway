// ============================================================================
// IMS 2.0 - Catalog products API (the `catalog_products` PIM collection)
// ============================================================================
// Serves the Catalog Manager screen: list/filter the PIM docs (including the
// BVI-imported review queue), edit them in place, and PROMOTE an imported doc
// into a POS-sellable `products` spine row (the only thing that clears
// needs_review). Import methods DIRECTLY from this module, never through the
// services/api barrel — the barrel re-export fails for new modules (TS2614).

import api from './client';

// A catalog_products doc as the list/detail endpoints return it. Everything is
// optional so a partial/legacy doc never breaks rendering.
export interface CatalogProductDoc {
  id: string;
  sku?: string | null;
  title?: string | null;
  name?: string | null;
  brand?: string | null;
  category?: string | null;
  category_name?: string | null;
  description?: string | null;
  hsn_code?: string | null;
  gst_rate?: number | null;
  weight?: number | null;
  mrp?: number | null;
  offer_price?: number | null;
  pricing?: {
    mrp?: number | null;
    offer_price?: number | null;
    cost_price?: number | null;
    discount_category?: string | null;
  } | null;
  images?: string[] | null;
  image_url?: string | null;
  attributes?: Record<string, unknown> | null;
  tags?: string[] | null;
  is_active?: boolean | null;
  needs_review?: boolean | null;
  pos_ready?: boolean | null;
  promoted_at?: string | null;
  promoted_by?: string | null;
  source?: string | null;
  category_unmapped?: boolean | null;
  ecom?: {
    status?: string | null;
    handle?: string | null;
    page_url?: string | null;
    shopify_product_id?: string | null;
  } | null;
  [k: string]: unknown;
}

export interface CatalogProductListResponse {
  products: CatalogProductDoc[];
  /** Post-filter, pre-slice count — feeds the Pagination component. */
  total: number;
  page: number;
  total_pages: number;
}

export interface CatalogProductListParams {
  category?: string;
  brand?: string;
  search?: string;
  /** 'true' (default, active only) | 'false' | 'all' (review queue needs
   *  'all' — BVI DRAFT/ARCHIVED imports carry is_active=false). */
  is_active?: 'true' | 'false' | 'all';
  needs_review?: boolean;
  source?: string;
  page?: number;
  limit?: number;
}

// Partial update for PUT /catalog/products/{id} (the review mini-form's save
// and the full-page ?review editor's diff-only save).
export interface UpdateCatalogProductPayload {
  /** Canonicalised server-side; a change re-derives HSN/GST when neither is
   *  explicitly sent alongside. */
  category?: string;
  attributes?: Record<string, unknown>;
  description?: string;
  hsn_code?: string;
  gst_rate?: number;
  weight?: number;
  /** All-optional PATCH model: send only the pricing keys that changed. */
  pricing?: {
    mrp?: number;
    offer_price?: number;
    cost_price?: number;
    discount_category?: string;
  };
  images?: string[];
  is_active?: boolean;
  /** Display name — the server sets both `name` and `title` from it. */
  name?: string;
  /** Governed tags; an explicit empty list clears all tags. */
  tags?: string[];
  /** Optimistic concurrency: the doc's `updated_at` as it was loaded. The
   *  server 409s ("changed by someone else") when the doc moved since. */
  expected_updated_at?: string;
}

// ---------------------------------------------------------------------------
// Status-preserving error for the review editor. The shared axios interceptor
// flattens every failure into a plain string Error (losing the HTTP status),
// but the full-page review flow must branch on it: 409 = concurrency /
// already-approved, 422 = dictionary/validation. update() and promote() below
// let 4xx client errors through validateStatus and re-throw them as this typed
// error; the message is the same flattened `detail` string the interceptor
// would have produced, so existing generic `e instanceof Error` handlers
// (drawer, bulk approve) behave byte-identically.
// ---------------------------------------------------------------------------
export class CatalogRequestError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'CatalogRequestError';
    this.status = status;
  }
}

// Mirror of the interceptor's detail flattening for the validateStatus path.
function flattenErrorDetail(data: unknown, fallback: string): string {
  const detail = (data as { detail?: unknown } | null | undefined)?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join('. ');
  }
  const message = (data as { message?: unknown } | null | undefined)?.message;
  return typeof message === 'string' && message ? message : fallback;
}

// One review-checklist row from the promote dry-run (a door validation gap).
export interface PromoteGap {
  field: string | null;
  label?: string | null;
  message: string;
}

export interface PromoteDuplicateWarning {
  product_id?: string | null;
  sku?: string | null;
  brand?: string | null;
  model?: string | null;
  name?: string | null;
  reason: string;
}

export interface PromoteDryRunResult {
  ok: boolean;
  gaps: PromoteGap[];
  duplicate_warnings: PromoteDuplicateWarning[];
}

export interface PromoteResult {
  message: string;
  product_id: string;
  sku?: string | null;
  needs_review: boolean;
  pos_ready: boolean;
}

export const catalogProductsApi = {
  list: async (
    params?: CatalogProductListParams
  ): Promise<CatalogProductListResponse> => {
    const response = await api.get('/catalog/products', { params });
    return response.data as CatalogProductListResponse;
  },

  get: async (productId: string): Promise<CatalogProductDoc> => {
    const response = await api.get(`/catalog/products/${productId}`);
    return (response.data as { product: CatalogProductDoc }).product;
  },

  update: async (productId: string, data: UpdateCatalogProductPayload) => {
    // 400/409/422 pass validateStatus so the HTTP status survives as a typed
    // CatalogRequestError (409 = optimistic-concurrency conflict, 422 =
    // dictionary/validation). Other statuses keep the normal interceptor path
    // (retries, 401 refresh, flattened messages).
    const response = await api.put(`/catalog/products/${productId}`, data, {
      validateStatus: (s) => (s >= 200 && s < 300) || s === 400 || s === 409 || s === 422,
    });
    if (response.status >= 400) {
      throw new CatalogRequestError(
        flattenErrorDetail(response.data, 'Could not save the product.'),
        response.status
      );
    }
    return response.data as { product: CatalogProductDoc; message: string };
  },

  /** Validate an imported doc through the canonical door WITHOUT writing.
   *  Returns the gap checklist + soft duplicate warnings. */
  promoteDryRun: async (productId: string): Promise<PromoteDryRunResult> => {
    const response = await api.post(
      `/catalog/products/${productId}/promote`,
      undefined,
      { params: { dry_run: true } }
    );
    return response.data as PromoteDryRunResult;
  },

  /** Approve for POS: inserts the spine row (same id/sku) and clears
   *  needs_review. Throws a status-carrying CatalogRequestError on 400/409/422
   *  (same plain-English message as before — the review editor branches on
   *  the status: "already a billing product" 409 = treat as approved) and the
   *  interceptor's plain Error on everything else (5xx retries intact). */
  promote: async (productId: string): Promise<PromoteResult> => {
    const response = await api.post(`/catalog/products/${productId}/promote`, undefined, {
      validateStatus: (s) => (s >= 200 && s < 300) || s === 400 || s === 409 || s === 422,
    });
    if (response.status >= 400) {
      throw new CatalogRequestError(
        flattenErrorDetail(response.data, 'Approval failed.'),
        response.status
      );
    }
    return response.data as PromoteResult;
  },
};
