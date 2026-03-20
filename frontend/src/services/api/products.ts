// ============================================================================
// IMS 2.0 - Product / Catalog API
// ============================================================================

import api from './client';

export interface CreateProductPayload {
  category: string;
  attributes: Record<string, string | number>;
  description?: string;
  hsn_code?: string;
  gst_rate?: number;
  weight?: number;
  pricing: {
    mrp: number;
    offer_price?: number;
    cost_price?: number;
    discount_category?: string;
  };
  inventory?: {
    initial_quantity?: number;
    barcode?: string;
    reorder_level?: number;
  };
  images?: string[];
  shopify?: {
    sync_to_shopify: boolean;
    shopify_tags?: string[];
    publish_to_online_store?: boolean;
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
    const response = await api.get('/products/search', { params: { q: query, category } });
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
};
