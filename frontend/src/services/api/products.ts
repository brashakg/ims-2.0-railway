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
};

// ============================================================================
// Admin API - Product Master
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

  createProduct: async (data: {
    category: string;
    brand: string;
    subbrand?: string;
    modelNo: string;
    name: string;
    sku: string;
    mrp: number;
    offerPrice?: number;
    costPrice?: number;
    hsnCode?: string;
    attributes: Record<string, string | number>;
    images?: string[];
    status?: string;
  }) => {
    const response = await api.post('/admin/products', data);
    return response.data;
  },

  updateProduct: async (productId: string, data: Partial<{
    name: string;
    mrp: number;
    offerPrice: number;
    costPrice: number;
    barcode: string;
    attributes: Record<string, string | number>;
    images: string[];
    status: string;
  }>) => {
    const response = await api.put(`/admin/products/${productId}`, data);
    return response.data;
  },

  deleteProduct: async (productId: string) => {
    const response = await api.delete(`/admin/products/${productId}`);
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
