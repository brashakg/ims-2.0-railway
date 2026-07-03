// ============================================================================
// IMS 2.0 - Product / Catalog API
// ============================================================================

import api, { resolveApiAssetUrl } from './client';

// ---------------------------------------------------------------------------
// Canonical category field registry (GET /products/categories).
// The single source of truth for which attribute fields are required vs
// optional per product category, sourced from the backend product_master
// CATEGORY_SPECS the create/update gate enforces. The three product-entry doors
// derive their required-ness from this so the FE can never drift from the
// server enforcement.
// ---------------------------------------------------------------------------
export interface CategoryRegistryField {
  name: string;
  label: string;
  required: boolean;
  // Catalog Dictionary: owner-configured allowed values for this field
  // (Settings -> Catalog Dictionary; brand_name comes from the Brand Master).
  // When present the form renders a select restricted to these values — the
  // backend enforces the same list at create/update.
  options?: string[];
}

export interface CategoryRegistryEntry {
  code: string; // canonical long-form (e.g. "FRAME")
  sku_prefix: string; // short SKU-prefix code the FE picker keys on (e.g. "FR")
  name: string;
  required_fields: string[];
  optional_fields: string[];
  fields: CategoryRegistryField[];
  forced_discount_category: string | null;
}

export interface CategoryRegistryResponse {
  categories: CategoryRegistryEntry[];
}

// Result of POST /products/image. `url` points at the backend serve endpoint
// (GET /products/image/{file_id}) and is what gets stored in the product
// `images` array.
export interface UploadedProductImage {
  file_id: string;
  url: string;
  filename: string;
  content_type: string;
  size: number;
}

export interface CreateProductPayload {
  category: string;
  // SKU is AUTO-MINTED by the backend (product_master mints the clean semantic
  // SKU when none is supplied). Only send one when the operator explicitly
  // provided it (e.g. a legacy/imported SKU); otherwise OMIT it so the backend
  // mints the canonical value — the FE no longer fabricates a Date.now() SKU.
  sku?: string;
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
    // POST (SKU list in the body), not GET with a comma-joined ?skus= query.
    // The inventory page can pass thousands of SKUs; a query string that long
    // tripped server/proxy URL limits -> net::ERR_CONNECTION_CLOSED and a blank
    // "Online" column (QA F12). The body has no practical length limit.
    const response = await api.post('/catalog/online-status', { skus: clean });
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

  // THE canonical per-category field registry (single source of truth). Drives
  // the required/optional fields all three product-entry doors render + validate,
  // sourced from the backend product_master CATEGORY_SPECS the create gate
  // enforces. See productAddShared.ts loadCategoryRegistry (cached once).
  getCategoryRegistry: async (): Promise<CategoryRegistryResponse> => {
    const response = await api.get('/products/categories');
    return response.data;
  },

  // Brand Master read-projection for the Add-Product form: active brands
  // applicable to the category (short code like 'FR' or canonical), each with
  // its sub-brand names — drives the Brand Name select and restricts the
  // Sub Brand select per selected brand. Authenticated (not admin-gated).
  getBrandOptions: async (
    category?: string
  ): Promise<{ brands: Array<{ name: string; subbrands: string[] }> }> => {
    const response = await api.get('/products/brand-options', { params: { category } });
    return response.data as { brands: Array<{ name: string; subbrands: string[] }> };
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

  // Upload a single product image (multipart). Persists the bytes durably on the
  // backend (GridFS) and returns a stable, self-hosted URL to embed in the
  // product `images` array. Import this DIRECTLY from this module (not the api
  // barrel) — the barrel re-export fails to resolve for new methods (TS2614).
  uploadProductImage: async (file: File): Promise<UploadedProductImage> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post('/products/image', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      // Phone photos over store Wi-Fi can exceed the global 10s timeout.
      timeout: 60000,
    });
    const data = response.data as UploadedProductImage;
    // Absolutize: the deployed FE is on a different origin than the API, so a
    // relative url would render (and be stored) as a broken image.
    return { ...data, url: resolveApiAssetUrl(data.url) };
  },

  // RE-HOST an external image URL (an Autopilot brand-site photo): the backend
  // copies the bytes into OUR file store via an SSRF-hardened server-side
  // fetch, so the product never depends on the brand site keeping the file.
  // Same response shape as uploadProductImage, with a stable internal url.
  rehostProductImage: async (url: string): Promise<UploadedProductImage> => {
    const response = await api.post('/products/image/from-url', { url }, { timeout: 60000 });
    const data = response.data as UploadedProductImage;
    return { ...data, url: resolveApiAssetUrl(data.url) };
  },

  // Remove the background of a previously-uploaded product image and resize to
  // the catalog standard (Photoroom cut-out pipeline on the backend). The
  // ORIGINAL image is untouched — a NEW {file_id, url} is returned so the caller
  // swaps the entry it just edited. Import this DIRECTLY from this module (not
  // the api barrel) — the barrel re-export fails to resolve for new methods
  // (TS2614).
  editProductImage: async (fileId: string): Promise<{ file_id: string; url: string }> => {
    // Photoroom round-trip (fetch -> provider -> store) can exceed 10s.
    const response = await api.post(`/products/image/${fileId}/edit`, undefined, {
      timeout: 60000,
    });
    const data = response.data as { file_id: string; url: string };
    return { ...data, url: resolveApiAssetUrl(data.url) };
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
// Categories are a FIXED reference (CATEGORY_DEFINITIONS) that drives GST via
// the HSN/GST master -- there is no category CRUD. The old adminCategoryApi
// (/admin/categories writer) was removed: it had zero consumers and was a
// confusing duplicate source for category + GST data.
// ============================================================================

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
