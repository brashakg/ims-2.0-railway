// ============================================================================
// IMS 2.0 - Product / Catalog API
// ============================================================================

import api from './client';

export interface CreateProductPayload {
  category: string;
  // Required by the backend ProductCreate (top-level, NOT inside attributes).
  sku: string;
  brand: string;
  model: string;
  attributes: Record<string, string | number>;
  description?: string;
  hsn_code?: string;
  // Pricing is FLAT to match the backend `ProductCreate` schema. It used
  // to be nested under `pricing` / `inventory`, which pydantic silently
  // dropped — so MRP, offer_price and gst_rate never persisted. ProductCreate
  // has no stock field, so initial quantity is not sent here (stock is
  // created later via GRN, not at product-create time).
  mrp: number;
  offer_price?: number;
  gst_rate?: number;
  weight?: number;
  // ---- Contact-lens (CL) identity fields. Top-level on ProductCreate,
  // optional + additive. Only sent for CONTACT_LENS / COLORED_CONTACT_LENS. ----
  cl_series?: string;
  modality?: string;
  base_curve?: number;
  diameter?: number;
  cl_power?: number;
  cl_cyl?: number;
  cl_axis?: number;
  cl_add?: number;
  pack_size?: number;
  // ---- Spectacle-lens stock-power identity (drives the SPH x CYL Power Grid).
  // Top-level on ProductCreate, optional. Only sent for the lens category. ----
  sph?: number;
  cyl?: number;
  axis?: number;
  add?: number;
  // Extra fields the form collects; the backend ignores keys it doesn't
  // model, so these are harmless passthrough until ProductCreate grows them.
  cost_price?: number;
  discount_category?: string;
  images?: string[];
  shopify?: {
    // Kept for future NEXUS agent → Shopify sync only. We don't run our
    // own storefront, so `publish_to_online_store` was removed in 6.12.
    sync_to_shopify: boolean;
    shopify_tags?: string[];
    publish_to_pos?: boolean;
  };
}

// Partial update payload for `PUT /products/{id}`. Mirrors the backend
// `ProductUpdate` schema (snake_case). Every field is optional; the backend
// merges only what is sent and re-runs the category + MRP>=offer validators.
export interface UpdateProductPayload {
  category?: string;
  brand?: string;
  model?: string;
  color?: string;
  mrp?: number;
  offer_price?: number;
  hsn_code?: string;
  gst_rate?: number;
  is_active?: boolean;
  // Scan-to-sell barcode persisted on the product master.
  barcode?: string;
  // Per-product reorder configuration (Reorder dashboard).
  reorder_point?: number;
  reorder_quantity?: number;
  max_stock?: number;
  lead_time_days?: number;
  // CL identity
  cl_series?: string;
  modality?: string;
  base_curve?: number;
  diameter?: number;
  cl_power?: number;
  cl_cyl?: number;
  cl_axis?: number;
  cl_add?: number;
  pack_size?: number;
  // Spectacle-lens power identity
  sph?: number;
  cyl?: number;
  axis?: number;
  add?: number;
}

// ============================================================================
// Catalog API - Online (Shopify/e-commerce) status bridge
// ----------------------------------------------------------------------------
// Reads the e-commerce (BVI) catalog to tell, per SKU/barcode, whether a
// product is online (in Shopify) and how much online stock exists. Fully
// fail-soft on the backend: an unconfigured/unreachable bridge returns {}.
// ============================================================================

export interface OnlineStatus {
  online: boolean;
  online_stock: number;
  status?: string | null;
}

export const catalogApi = {
  /** Per-identifier online status. Pass any mix of SKUs / barcodes; the
   *  backend matches each against sku, storeBarcode or barcode and keys the
   *  result by the identifier you sent. Empty {} when the bridge is off. */
  getOnlineStatus: async (skus: string[]): Promise<Record<string, OnlineStatus>> => {
    const clean = Array.from(new Set((skus || []).map(s => String(s || '').trim()).filter(Boolean)));
    if (clean.length === 0) return {};
    const response = await api.get('/catalog/online-status', { params: { skus: clean.join(',') } });
    return (response.data?.statuses || {}) as Record<string, OnlineStatus>;
  },

  /** Diagnostic: is the e-commerce catalog configured/reachable + counts. */
  getOnlineSummary: async () => {
    const response = await api.get('/catalog/online-summary');
    return response.data as {
      configured: boolean;
      reachable: boolean;
      online_products?: number;
      online_variants?: number;
      sample?: Array<{ sku: string | null; store_barcode: string | null; barcode: string | null }>;
    };
  },
};

export const productApi = {
  getProducts: async (params?: { category?: string; brand?: string; search?: string; store_id?: string }) => {
    const response = await api.get('/products', { params });
    return response.data;
  },

  getProduct: async (productId: string) => {
    const response = await api.get(`/products/${productId}`);
    return response.data;
  },

  getCategories: async () => {
    const response = await api.get('/products/categories/list');
    return response.data;
  },

  getBrands: async (category?: string) => {
    const response = await api.get('/products/brands/list', { params: { category } });
    return response.data;
  },

  searchProducts: async (query: string, category?: string) => {
    // Backend exposes search via the list endpoint's `search` param, not a
    // dedicated /products/search route (which 404'd).
    const response = await api.get('/products', { params: { search: query, category } });
    return response.data;
  },

  createProduct: async (data: CreateProductPayload) => {
    const response = await api.post('/products', data);
    return response.data;
  },

  // Update a product through the SINGLE validated path (`PUT /products/{id}`).
  // The backend ProductUpdate schema enforces category (422) + MRP >= offer
  // and persists only modelled fields (snake_case). Use this instead of the
  // retired, unvalidated `adminProductApi.updateProduct`. `productId` is the
  // canonical product_id. All fields optional; send only what changes.
  updateProduct: async (productId: string, data: UpdateProductPayload) => {
    const response = await api.put(`/products/${productId}`, data);
    return response.data;
  },

  // Rapid Grid (Phase B): create many products in one call. Each row is the
  // SAME CreateProductPayload shape as createProduct; the backend validates +
  // creates valid rows and reports per-row results. Import this DIRECTLY from
  // this module (not the api barrel) — the barrel re-export fails to resolve
  // (TS2614).
  bulkCreateProducts: async (products: CreateProductPayload[]): Promise<BulkCreateResponse> => {
    const response = await api.post('/products/bulk-create', { products });
    return response.data as BulkCreateResponse;
  },
};

// Per-row + summary result from POST /products/bulk-create.
export interface BulkCreateRowResult {
  index: number;
  ok: boolean;
  errors: string[];
  sku: string;
  product_id?: string;
}

export interface BulkCreateResponse {
  summary: { total: number; created: number; failed: number };
  results: BulkCreateRowResult[];
}

// ============================================================================
// Bulk pricing / offers API  (v2 Pricing & Offers screen)
// ----------------------------------------------------------------------------
// Dry-run-first + cap-enforced. The backend returns per-row before/after plus
// a per-row cap classification; the UI previews on dry-run then commits the
// valid rows. Import this DIRECTLY from this module (not the api barrel).

export interface BulkScope {
  category?: string;
  brand?: string;
  store_id?: string;
  limit?: number;
}

export interface BulkRowResult {
  product_id: string;
  sku?: string;
  brand?: string;
  model?: string;
  category?: string;
  discount_category?: string;
  // bulk-price returns old_mrp/new_mrp; bulk-offer returns mrp.
  old_mrp?: number;
  new_mrp?: number;
  mrp?: number;
  old_offer_price: number;
  new_offer_price: number;
  effective_cap_pct: number;
  implied_discount_pct: number;
  ok: boolean;
  reason: string | null;
  message: string | null;
  changed: boolean;
  applied?: boolean;
}

export interface BulkSummary {
  operation: string;
  store_id?: string | null;
  category?: string | null;
  brand?: string | null;
  reason?: string | null;
  applied: boolean;
  counts: { total: number; valid: number; violations: number; unchanged: number };
  committed: number;
  [k: string]: unknown;
}

export interface BulkResponse {
  dry_run: boolean;
  summary: BulkSummary;
  rows: BulkRowResult[];
}

export interface BulkPricePayload extends BulkScope {
  mode: 'PERCENT' | 'FLAT';
  target: 'OFFER' | 'MRP' | 'BOTH';
  amount: number;
  apply?: boolean;
  reason?: string;
}

export interface BulkOfferPayload extends BulkScope {
  action: 'SET' | 'CLEAR';
  discount_percent?: number;
  offer_price?: number;
  apply?: boolean;
  reason?: string;
}

export const pricingApi = {
  bulkPrice: async (payload: BulkPricePayload): Promise<BulkResponse> => {
    const response = await api.post('/products/bulk-price', payload);
    return response.data;
  },

  bulkOffer: async (payload: BulkOfferPayload): Promise<BulkResponse> => {
    const response = await api.post('/products/bulk-offer', payload);
    return response.data;
  },
};

// ============================================================================
// Admin API - Product Master
// ----------------------------------------------------------------------------
// WRITE methods (create / update / delete) were REMOVED. They posted raw
// camelCase to the unvalidated `/admin/products` endpoints, which wrote the
// `products` collection with no category / MRP / GST validation and stored
// camelCase keys (offerPrice vs offer_price) -> split-brain. All product
// writes now go through the single validated `productApi` (`/products`,
// `/products/bulk-create`, `PUT /products/{id}`). The reads below + the
// CSV file-stash + generate-sku helper are kept (they are not product writers).
// ============================================================================

export const adminProductApi = {
  getProducts: async (params?: { category?: string; brand?: string; status?: string; page?: number; pageSize?: number }) => {
    const response = await api.get('/admin/products', { params });
    return response.data;
  },

  getProduct: async (productId: string) => {
    const response = await api.get(`/admin/products/${productId}`);
    return response.data;
  },

  bulkImportProducts: async (file: File, category: string) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('category', category);
    const response = await api.post('/admin/products/bulk-import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  generateSku: async (category: string, brand: string, modelNo: string) => {
    const response = await api.post('/admin/products/generate-sku', { category, brand, model_no: modelNo });
    return response.data;
  },
};

// ============================================================================
// Admin API - Category Master
// ============================================================================

export const adminCategoryApi = {
  getCategories: async () => {
    const response = await api.get('/admin/categories');
    return response.data;
  },

  getCategory: async (categoryId: string) => {
    const response = await api.get(`/admin/categories/${categoryId}`);
    return response.data;
  },

  createCategory: async (data: {
    code: string;
    name: string;
    hsnCode: string;
    gstRate: number;
    description?: string;
    attributes?: string[];
    status?: string;
  }) => {
    const response = await api.post('/admin/categories', data);
    return response.data;
  },

  updateCategory: async (categoryId: string, data: Partial<{
    name: string;
    hsnCode: string;
    gstRate: number;
    description: string;
    attributes: string[];
    status: string;
  }>) => {
    const response = await api.put(`/admin/categories/${categoryId}`, data);
    return response.data;
  },

  deleteCategory: async (categoryId: string) => {
    const response = await api.delete(`/admin/categories/${categoryId}`);
    return response.data;
  },
};

// ============================================================================
// Admin API - Brand Master
// ============================================================================

export const adminBrandApi = {
  getBrands: async (params?: { category?: string; tier?: string }) => {
    const response = await api.get('/admin/brands', { params });
    return response.data;
  },

  getBrand: async (brandId: string) => {
    const response = await api.get(`/admin/brands/${brandId}`);
    return response.data;
  },

  createBrand: async (data: {
    name: string;
    code: string;
    categories: string[];
    tier: 'MASS' | 'PREMIUM' | 'LUXURY';
    warranty?: number;
    description?: string;
    status?: string;
  }) => {
    const response = await api.post('/admin/brands', data);
    return response.data;
  },

  updateBrand: async (brandId: string, data: Partial<{
    name: string;
    code: string;
    categories: string[];
    tier: string;
    warranty: number;
    description: string;
    status: string;
  }>) => {
    const response = await api.put(`/admin/brands/${brandId}`, data);
    return response.data;
  },

  deleteBrand: async (brandId: string) => {
    const response = await api.delete(`/admin/brands/${brandId}`);
    return response.data;
  },

  // Sub-brands
  getSubbrands: async (brandId: string) => {
    const response = await api.get(`/admin/brands/${brandId}/subbrands`);
    return response.data;
  },

  createSubbrand: async (brandId: string, data: {
    name: string;
    code: string;
    description?: string;
  }) => {
    const response = await api.post(`/admin/brands/${brandId}/subbrands`, data);
    return response.data;
  },

  deleteSubbrand: async (brandId: string, subbrandId: string) => {
    const response = await api.delete(`/admin/brands/${brandId}/subbrands/${subbrandId}`);
    return response.data;
  },
};

// ============================================================================
// Admin API - Lens Master
// ============================================================================

export const adminLensApi = {
  // Lens Brands
  getLensBrands: async () => {
    const response = await api.get('/admin/lens/brands');
    return response.data;
  },

  createLensBrand: async (data: { name: string; code: string; tier?: string }) => {
    const response = await api.post('/admin/lens/brands', data);
    return response.data;
  },

  updateLensBrand: async (brandId: string, data: Partial<{ name: string; code: string; tier: string }>) => {
    const response = await api.put(`/admin/lens/brands/${brandId}`, data);
    return response.data;
  },

  deleteLensBrand: async (brandId: string) => {
    const response = await api.delete(`/admin/lens/brands/${brandId}`);
    return response.data;
  },

  // Lens Indices
  getLensIndices: async () => {
    const response = await api.get('/admin/lens/indices');
    return response.data;
  },

  createLensIndex: async (data: { value: string; multiplier: number; description?: string }) => {
    const response = await api.post('/admin/lens/indices', data);
    return response.data;
  },

  updateLensIndex: async (indexId: string, data: Partial<{ value: string; multiplier: number; description: string }>) => {
    const response = await api.put(`/admin/lens/indices/${indexId}`, data);
    return response.data;
  },

  deleteLensIndex: async (indexId: string) => {
    const response = await api.delete(`/admin/lens/indices/${indexId}`);
    return response.data;
  },

  // Lens Coatings
  getLensCoatings: async () => {
    const response = await api.get('/admin/lens/coatings');
    return response.data;
  },

  createLensCoating: async (data: { name: string; code: string; price: number; description?: string }) => {
    const response = await api.post('/admin/lens/coatings', data);
    return response.data;
  },

  updateLensCoating: async (coatingId: string, data: Partial<{ name: string; code: string; price: number; description: string }>) => {
    const response = await api.put(`/admin/lens/coatings/${coatingId}`, data);
    return response.data;
  },

  deleteLensCoating: async (coatingId: string) => {
    const response = await api.delete(`/admin/lens/coatings/${coatingId}`);
    return response.data;
  },

  // Lens Add-ons
  getLensAddons: async () => {
    const response = await api.get('/admin/lens/addons');
    return response.data;
  },

  createLensAddon: async (data: { name: string; code: string; price: number; type: string; description?: string }) => {
    const response = await api.post('/admin/lens/addons', data);
    return response.data;
  },

  updateLensAddon: async (addonId: string, data: Partial<{ name: string; code: string; price: number; type: string; description: string }>) => {
    const response = await api.put(`/admin/lens/addons/${addonId}`, data);
    return response.data;
  },

  deleteLensAddon: async (addonId: string) => {
    const response = await api.delete(`/admin/lens/addons/${addonId}`);
    return response.data;
  },

  // Lens Pricing Matrix
  getLensPricing: async (brandId?: string) => {
    const response = await api.get('/admin/lens/pricing', { params: { brand_id: brandId } });
    return response.data;
  },

  setLensPricing: async (data: {
    brandId: string;
    indexId: string;
    category: string;
    basePrice: number;
  }) => {
    const response = await api.post('/admin/lens/pricing', data);
    return response.data;
  },

  // ----- Range pricing (May 2026) ------------------------------------
  // Bracket-based tier pricing (e.g. SPH 0-2.00 = ₹1,200 · 2.25-4.00 =
  // ₹1,500). Avoids the per-SKU explosion when there are 80+ Rx combos.
  // POS calls /admin/lens/pricing-ranges/quote at the prescription step.
  listLensPricingRanges: async (params?: {
    brand_id?: string;
    index_id?: string;
    category?: string;
  }) => {
    const response = await api.get('/admin/lens/pricing-ranges', { params });
    return response.data as {
      ranges: Array<{
        range_id: string;
        brand_id: string;
        index_id: string;
        category: string;
        parameter: 'sphere' | 'cylinder' | 'addition';
        min_value: number;
        max_value: number;
        base_price: number;
        is_active: boolean;
      }>;
      total: number;
    };
  },

  createLensPricingRange: async (data: {
    brand_id: string;
    index_id: string;
    category: string;
    parameter: 'sphere' | 'cylinder' | 'addition';
    min_value: number;
    max_value: number;
    base_price: number;
  }) => {
    const response = await api.post('/admin/lens/pricing-ranges', data);
    return response.data;
  },

  updateLensPricingRange: async (
    rangeId: string,
    patch: Partial<{
      min_value: number;
      max_value: number;
      base_price: number;
      is_active: boolean;
    }>,
  ) => {
    const response = await api.put(`/admin/lens/pricing-ranges/${rangeId}`, patch);
    return response.data;
  },

  deleteLensPricingRange: async (rangeId: string) => {
    const response = await api.delete(`/admin/lens/pricing-ranges/${rangeId}`);
    return response.data;
  },

  quoteLensPrice: async (input: {
    brand_id: string;
    index_id: string;
    category: string;
    sphere?: number;
    cylinder?: number;
    addition?: number;
    coatings?: string[];
  }) => {
    const response = await api.post('/admin/lens/pricing-ranges/quote', input);
    return response.data as {
      ok: boolean;
      source?: 'exact_match' | 'range_match' | 'no_pricing';
      base_price?: number;
      total?: number;
      breakdown?: {
        base_price: number;
        brand_multiplier?: number;
        index_multiplier?: number;
        coatings_subtotal?: number;
      };
      hint?: string;
    };
  },
};
