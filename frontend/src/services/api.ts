// ============================================================================
// IMS 2.0 - API Service
// ============================================================================

import axios from 'axios';
import type { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';
import type { ApiResponse, LoginCredentials, LoginResponse, User } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

// Retry configuration
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

// Helper function for delay
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

// Check if error is retryable (network errors, timeouts, 5xx errors)
const isRetryableError = (error: AxiosError): boolean => {
  // Network errors (no response)
  if (!error.response) {
    return true;
  }
  // Server errors (5xx)
  if (error.response.status >= 500) {
    return true;
  }
  // Rate limiting
  if (error.response.status === 429) {
    return true;
  }
  return false;
};

// Create axios instance
const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor - add auth token and retry config
api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('ims_token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    // Initialize retry count
    if (config.headers) {
      config.headers['x-retry-count'] = config.headers['x-retry-count'] || '0';
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor - handle errors with retry logic
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<{ message?: string; detail?: string }>) => {
    const config = error.config;

    // Don't retry if no config or already exceeded retries
    if (!config || !config.headers) {
      return handleFinalError(error);
    }

    const retryCount = parseInt(config.headers['x-retry-count'] as string || '0', 10);

    // Check if we should retry
    if (isRetryableError(error) && retryCount < MAX_RETRIES) {
      config.headers['x-retry-count'] = String(retryCount + 1);

      // Exponential backoff: 1s, 2s, 4s
      const backoffDelay = RETRY_DELAY_MS * Math.pow(2, retryCount);
      console.log(`Network error, retrying in ${backoffDelay}ms (attempt ${retryCount + 1}/${MAX_RETRIES})...`);

      await delay(backoffDelay);
      return api.request(config);
    }

    return handleFinalError(error);
  }
);

// Handle final error after retries exhausted
const handleFinalError = (error: AxiosError<{ message?: string; detail?: string }>) => {
  if (error.response?.status === 401) {
    // Clear auth state on unauthorized
    localStorage.removeItem('ims_token');
    localStorage.removeItem('ims_user');
    window.location.href = '/login';
  }

  // Build user-friendly error message
  let message: string;

  if (!error.response) {
    // Network error
    message = 'Network error. Please check your internet connection and try again.';
  } else if (error.response.status >= 500) {
    message = 'Server error. Please try again in a moment.';
  } else {
    message =
      error.response?.data?.message ||
      error.response?.data?.detail ||
      error.message ||
      'An error occurred';
  }

  return Promise.reject(new Error(message));
};

// ============================================================================
// Auth API
// ============================================================================

export const authApi = {
  login: async (credentials: LoginCredentials): Promise<LoginResponse> => {
    // Backend response format differs from frontend LoginResponse
    interface BackendLoginResponse {
      access_token: string;
      token_type: string;
      expires_in: number;
      user: {
        user_id: string;
        username: string;
        full_name: string;
        roles: string[];
        store_ids: string[];
        active_store_id: string;
      };
    }

    const response = await api.post<BackendLoginResponse>('/auth/login', credentials);
    const data = response.data;

    // Transform backend response to frontend format
    return {
      success: true,
      token: data.access_token,
      user: {
        id: data.user.user_id,
        email: data.user.username, // Using username as email for compatibility
        name: data.user.full_name,
        phone: '',
        roles: data.user.roles as import('../types').UserRole[],
        activeRole: data.user.roles[0] as import('../types').UserRole,
        storeIds: data.user.store_ids,
        activeStoreId: data.user.active_store_id,
        discountCap: 0,
        isActive: true,
        geoRestricted: false,
        createdAt: new Date().toISOString(),
      },
    };
  },

  logout: async (): Promise<void> => {
    await api.post('/auth/logout');
    localStorage.removeItem('ims_token');
    localStorage.removeItem('ims_user');
  },

  refreshToken: async (): Promise<{ token: string }> => {
    const response = await api.post<{ token: string }>('/auth/refresh');
    return response.data;
  },

  getProfile: async (): Promise<User> => {
    const response = await api.get<User>('/auth/me');
    return response.data;
  },

  changePassword: async (currentPassword: string, newPassword: string): Promise<ApiResponse<void>> => {
    const response = await api.post<ApiResponse<void>>('/auth/change-password', {
      current_password: currentPassword,
      new_password: newPassword,
    });
    return response.data;
  },
};

// ============================================================================
// Store API
// ============================================================================

export const storeApi = {
  getStores: async () => {
    const response = await api.get('/stores');
    return response.data;
  },

  getStore: async (storeId: string) => {
    const response = await api.get(`/stores/${storeId}`);
    return response.data;
  },

  getStoreStats: async (storeId: string) => {
    const response = await api.get(`/stores/${storeId}/stats`);
    return response.data;
  },
};

// ============================================================================
// Product API
// ============================================================================

export const productApi = {
  getProducts: async (params?: { category?: string; brand?: string; search?: string }) => {
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
};

// ============================================================================
// Inventory API
// ============================================================================

export const inventoryApi = {
  getStock: async (storeId: string, productId?: string) => {
    const response = await api.get('/inventory/stock', { params: { store_id: storeId, product_id: productId } });
    return response.data;
  },

  getStockByBarcode: async (barcode: string) => {
    const response = await api.get(`/inventory/barcode/${barcode}`);
    return response.data;
  },

  searchByBarcode: async (barcode: string, storeId: string) => {
    // Search for product by barcode in specific store
    const response = await api.get(`/inventory/barcode/${barcode}`, { params: { store_id: storeId } });
    return response.data;
  },

  getLowStock: async (storeId: string) => {
    const response = await api.get('/inventory/low-stock', { params: { store_id: storeId } });
    return response.data;
  },

  getExpiringStock: async (storeId: string, days: number = 30) => {
    const response = await api.get('/inventory/expiring', { params: { store_id: storeId, days } });
    return response.data;
  },

  createTransfer: async (data: { fromStoreId: string; toStoreId: string; items: Array<{ stockId: string; quantity: number }> }) => {
    const response = await api.post('/inventory/transfers', data);
    return response.data;
  },

  getTransfers: async (storeId: string, direction: 'incoming' | 'outgoing') => {
    const response = await api.get('/inventory/transfers', { params: { store_id: storeId, direction } });
    return response.data;
  },
};

// ============================================================================
// Customer API
// ============================================================================

export const customerApi = {
  getCustomers: async (params?: { search?: string; page?: number; pageSize?: number; storeId?: string; limit?: number }) => {
    const response = await api.get('/customers', { params });
    return response.data;
  },

  getCustomer: async (customerId: string) => {
    const response = await api.get(`/customers/${customerId}`);
    return response.data;
  },

  createCustomer: async (data: Partial<import('../types').Customer>) => {
    const response = await api.post('/customers', data);
    return response.data;
  },

  updateCustomer: async (customerId: string, data: Partial<import('../types').Customer>) => {
    const response = await api.put(`/customers/${customerId}`, data);
    return response.data;
  },

  searchByPhone: async (phone: string) => {
    const response = await api.get('/customers/search/phone', { params: { phone } });
    return response.data;
  },

  addPatient: async (customerId: string, patient: Partial<import('../types').Patient>) => {
    const response = await api.post(`/customers/${customerId}/patients`, patient);
    return response.data;
  },
};

// ============================================================================
// Order API
// ============================================================================

export const orderApi = {
  getOrders: async (params?: { storeId?: string; status?: string; date?: string; customerId?: string; limit?: number }) => {
    const response = await api.get('/orders', { params });
    return response.data;
  },

  getOrder: async (orderId: string) => {
    const response = await api.get(`/orders/${orderId}`);
    return response.data;
  },

  createOrder: async (data: Partial<import('../types').Order>) => {
    const response = await api.post('/orders', data);
    return response.data;
  },

  addOrderItem: async (orderId: string, item: Partial<import('../types').OrderItem>) => {
    const response = await api.post(`/orders/${orderId}/items`, item);
    return response.data;
  },

  removeOrderItem: async (orderId: string, itemId: string) => {
    const response = await api.delete(`/orders/${orderId}/items/${itemId}`);
    return response.data;
  },

  addPayment: async (orderId: string, payment: Partial<import('../types').Payment>) => {
    const response = await api.post(`/orders/${orderId}/payments`, payment);
    return response.data;
  },

  confirmOrder: async (orderId: string) => {
    const response = await api.post(`/orders/${orderId}/confirm`);
    return response.data;
  },

  deliverOrder: async (orderId: string) => {
    const response = await api.post(`/orders/${orderId}/deliver`);
    return response.data;
  },

  cancelOrder: async (orderId: string, reason: string) => {
    const response = await api.post(`/orders/${orderId}/cancel`, { reason });
    return response.data;
  },
};

// ============================================================================
// Prescription API
// ============================================================================

export const prescriptionApi = {
  getPrescriptions: async (patientId: string) => {
    const response = await api.get('/prescriptions', { params: { patient_id: patientId } });
    return response.data;
  },

  getPrescription: async (prescriptionId: string) => {
    const response = await api.get(`/prescriptions/${prescriptionId}`);
    return response.data;
  },

  createPrescription: async (data: Partial<import('../types').Prescription>) => {
    const response = await api.post('/prescriptions', data);
    return response.data;
  },

  validatePrescription: async (prescriptionId: string) => {
    const response = await api.get(`/prescriptions/${prescriptionId}/validate`);
    return response.data;
  },
};

// ============================================================================
// Workshop API
// ============================================================================

export const workshopApi = {
  getJobs: async (storeId: string, status?: string) => {
    const response = await api.get('/workshop/jobs', { params: { store_id: storeId, status } });
    return response.data;
  },

  getJob: async (jobId: string) => {
    const response = await api.get(`/workshop/jobs/${jobId}`);
    return response.data;
  },

  updateJobStatus: async (jobId: string, status: string, notes?: string) => {
    const response = await api.patch(`/workshop/jobs/${jobId}/status`, { status, notes });
    return response.data;
  },

  assignJob: async (jobId: string, staffId: string) => {
    const response = await api.post(`/workshop/jobs/${jobId}/assign`, { staff_id: staffId });
    return response.data;
  },
};

// ============================================================================
// Reports API
// ============================================================================

export const reportsApi = {
  getSalesSummary: async (storeId: string, startDate: string, endDate: string) => {
    const response = await api.get('/reports/sales/summary', {
      params: { store_id: storeId, start_date: startDate, end_date: endDate },
    });
    return response.data;
  },

  getDashboardStats: async (storeId: string) => {
    const response = await api.get('/reports/dashboard', { params: { store_id: storeId } });
    return response.data;
  },

  getInventoryReport: async (storeId: string) => {
    const response = await api.get('/reports/inventory', { params: { store_id: storeId } });
    return response.data;
  },
};

// ============================================================================
// HR API
// ============================================================================

export const hrApi = {
  getAttendance: async (storeId: string, date?: string) => {
    const response = await api.get('/hr/attendance', { params: { store_id: storeId, date } });
    return response.data;
  },

  checkIn: async (storeId: string, latitude: number, longitude: number) => {
    const response = await api.post('/hr/attendance/check-in', {
      store_id: storeId,
      latitude,
      longitude,
    });
    return response.data;
  },

  checkOut: async (attendanceId: string) => {
    const response = await api.post(`/hr/attendance/${attendanceId}/check-out`);
    return response.data;
  },

  getLeaves: async (params?: { userId?: string; status?: string }) => {
    const response = await api.get('/hr/leaves', { params });
    return response.data;
  },

  applyLeave: async (data: Partial<import('../types').Leave>) => {
    const response = await api.post('/hr/leaves', data);
    return response.data;
  },

  approveLeave: async (leaveId: string, approved: boolean, remarks?: string) => {
    const response = await api.post(`/hr/leaves/${leaveId}/approve`, { approved, remarks });
    return response.data;
  },
};

// ============================================================================
// Admin API - Store Management
// ============================================================================

export const adminStoreApi = {
  getStores: async () => {
    const response = await api.get('/admin/stores');
    return response.data;
  },

  getStore: async (storeId: string) => {
    const response = await api.get(`/admin/stores/${storeId}`);
    return response.data;
  },

  createStore: async (data: {
    name: string;
    code: string;
    address: string;
    city: string;
    state: string;
    phone: string;
    email: string;
    gst: string;
    status?: string;
  }) => {
    const response = await api.post('/admin/stores', data);
    return response.data;
  },

  updateStore: async (storeId: string, data: Partial<{
    name: string;
    code: string;
    address: string;
    city: string;
    state: string;
    phone: string;
    email: string;
    gst: string;
    status: string;
  }>) => {
    const response = await api.put(`/admin/stores/${storeId}`, data);
    return response.data;
  },

  deleteStore: async (storeId: string) => {
    const response = await api.delete(`/admin/stores/${storeId}`);
    return response.data;
  },

  getStoreUsers: async (storeId: string) => {
    const response = await api.get(`/admin/stores/${storeId}/users`);
    return response.data;
  },
};

// ============================================================================
// Admin API - User Management
// ============================================================================

export const adminUserApi = {
  getUsers: async (params?: { storeId?: string; role?: string; status?: string }) => {
    const response = await api.get('/admin/users', { params });
    return response.data;
  },

  getUser: async (userId: string) => {
    const response = await api.get(`/admin/users/${userId}`);
    return response.data;
  },

  createUser: async (data: {
    name: string;
    email: string;
    phone: string;
    role: string;
    storeId: string;
    password?: string;
    status?: string;
  }) => {
    const response = await api.post('/admin/users', data);
    return response.data;
  },

  updateUser: async (userId: string, data: Partial<{
    name: string;
    email: string;
    phone: string;
    role: string;
    storeId: string;
    status: string;
  }>) => {
    const response = await api.put(`/admin/users/${userId}`, data);
    return response.data;
  },

  deleteUser: async (userId: string) => {
    const response = await api.delete(`/admin/users/${userId}`);
    return response.data;
  },

  resetPassword: async (userId: string, newPassword: string) => {
    const response = await api.post(`/admin/users/${userId}/reset-password`, { new_password: newPassword });
    return response.data;
  },

  assignStore: async (userId: string, storeId: string, role?: string) => {
    const response = await api.post(`/admin/users/${userId}/assign-store`, { store_id: storeId, role });
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

// ============================================================================
// Admin API - Discount Rules
// ============================================================================

export const adminDiscountApi = {
  getDiscountRules: async () => {
    const response = await api.get('/admin/discounts/rules');
    return response.data;
  },

  getRoleDiscountCaps: async () => {
    const response = await api.get('/admin/discounts/role-caps');
    return response.data;
  },

  setRoleDiscountCap: async (role: string, maxDiscount: number) => {
    const response = await api.post('/admin/discounts/role-caps', { role, max_discount: maxDiscount });
    return response.data;
  },

  getTierDiscounts: async () => {
    const response = await api.get('/admin/discounts/tier-discounts');
    return response.data;
  },

  setTierDiscount: async (tier: string, discount: number) => {
    const response = await api.post('/admin/discounts/tier-discounts', { tier, discount });
    return response.data;
  },

  createPromoCode: async (data: {
    code: string;
    discountType: 'PERCENTAGE' | 'FIXED';
    discountValue: number;
    minPurchase?: number;
    maxDiscount?: number;
    validFrom: string;
    validTo: string;
    usageLimit?: number;
    categories?: string[];
  }) => {
    const response = await api.post('/admin/discounts/promo-codes', data);
    return response.data;
  },

  getPromoCodes: async (params?: { active?: boolean }) => {
    const response = await api.get('/admin/discounts/promo-codes', { params });
    return response.data;
  },

  deletePromoCode: async (codeId: string) => {
    const response = await api.delete(`/admin/discounts/promo-codes/${codeId}`);
    return response.data;
  },
};

// ============================================================================
// Admin API - Integrations
// ============================================================================

export const adminIntegrationApi = {
  // Razorpay
  getRazorpayConfig: async () => {
    const response = await api.get('/admin/integrations/razorpay');
    return response.data;
  },

  setRazorpayConfig: async (data: { keyId: string; keySecret: string; webhookSecret?: string; enabled: boolean }) => {
    const response = await api.post('/admin/integrations/razorpay', data);
    return response.data;
  },

  testRazorpayConnection: async () => {
    const response = await api.post('/admin/integrations/razorpay/test');
    return response.data;
  },

  // WhatsApp
  getWhatsappConfig: async () => {
    const response = await api.get('/admin/integrations/whatsapp');
    return response.data;
  },

  setWhatsappConfig: async (data: { apiKey: string; phoneNumberId: string; businessId: string; enabled: boolean }) => {
    const response = await api.post('/admin/integrations/whatsapp', data);
    return response.data;
  },

  testWhatsappConnection: async () => {
    const response = await api.post('/admin/integrations/whatsapp/test');
    return response.data;
  },

  // Tally
  getTallyConfig: async () => {
    const response = await api.get('/admin/integrations/tally');
    return response.data;
  },

  setTallyConfig: async (data: { serverUrl: string; companyName: string; syncInterval: number; enabled: boolean }) => {
    const response = await api.post('/admin/integrations/tally', data);
    return response.data;
  },

  testTallyConnection: async () => {
    const response = await api.post('/admin/integrations/tally/test');
    return response.data;
  },

  // Shopify
  getShopifyConfig: async () => {
    const response = await api.get('/admin/integrations/shopify');
    return response.data;
  },

  setShopifyConfig: async (data: { shopUrl: string; apiKey: string; apiSecret: string; accessToken: string; enabled: boolean }) => {
    const response = await api.post('/admin/integrations/shopify', data);
    return response.data;
  },

  testShopifyConnection: async () => {
    const response = await api.post('/admin/integrations/shopify/test');
    return response.data;
  },

  // SMS Gateway
  getSmsConfig: async () => {
    const response = await api.get('/admin/integrations/sms');
    return response.data;
  },

  setSmsConfig: async (data: { provider: string; apiKey: string; senderId: string; enabled: boolean }) => {
    const response = await api.post('/admin/integrations/sms', data);
    return response.data;
  },
};

// ============================================================================
// Admin API - System
// ============================================================================

export const adminSystemApi = {
  getSystemStatus: async () => {
    const response = await api.get('/admin/system/status');
    return response.data;
  },

  getBackups: async () => {
    const response = await api.get('/admin/system/backups');
    return response.data;
  },

  createBackup: async () => {
    const response = await api.post('/admin/system/backups');
    return response.data;
  },

  restoreBackup: async (backupId: string) => {
    const response = await api.post(`/admin/system/backups/${backupId}/restore`);
    return response.data;
  },

  downloadBackup: async (backupId: string) => {
    const response = await api.get(`/admin/system/backups/${backupId}/download`, { responseType: 'blob' });
    return response.data;
  },

  exportData: async (type: 'products' | 'customers' | 'orders' | 'inventory' | 'all') => {
    const response = await api.get(`/admin/system/export/${type}`, { responseType: 'blob' });
    return response.data;
  },

  importData: async (type: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post(`/admin/system/import/${type}`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return response.data;
  },

  getAuditLogs: async (params?: { userId?: string; action?: string; startDate?: string; endDate?: string }) => {
    const response = await api.get('/admin/system/audit-logs', { params });
    return response.data;
  },

  getSettings: async () => {
    const response = await api.get('/admin/system/settings');
    return response.data;
  },

  updateSettings: async (settings: Record<string, unknown>) => {
    const response = await api.put('/admin/system/settings', settings);
    return response.data;
  },
};

// ============================================================================
// Clinical / Eye Test API
// ============================================================================

export const clinicalApi = {
  // Queue management
  getQueue: async (storeId: string) => {
    const response = await api.get('/clinical/queue', { params: { store_id: storeId } });
    return response.data;
  },

  addToQueue: async (data: {
    storeId: string;
    patientName: string;
    customerPhone: string;
    age?: number;
    reason?: string;
    customerId?: string;
  }) => {
    const response = await api.post('/clinical/queue', data);
    return response.data;
  },

  updateQueueStatus: async (queueId: string, status: string) => {
    const response = await api.patch(`/clinical/queue/${queueId}/status`, { status });
    return response.data;
  },

  removeFromQueue: async (queueId: string) => {
    const response = await api.delete(`/clinical/queue/${queueId}`);
    return response.data;
  },

  // Eye tests
  getTodayTests: async (storeId: string) => {
    const response = await api.get('/clinical/tests', { params: { store_id: storeId, date: 'today' } });
    return response.data;
  },

  getTest: async (testId: string) => {
    const response = await api.get(`/clinical/tests/${testId}`);
    return response.data;
  },

  startTest: async (queueId: string) => {
    const response = await api.post(`/clinical/queue/${queueId}/start-test`);
    return response.data;
  },

  completeTest: async (testId: string, data: {
    rightEye: { sphere: number | null; cylinder: number | null; axis: number | null; add?: number | null };
    leftEye: { sphere: number | null; cylinder: number | null; axis: number | null; add?: number | null };
    pd?: number;
    notes?: string;
  }) => {
    const response = await api.post(`/clinical/tests/${testId}/complete`, data);
    return response.data;
  },
};

// ============================================================================
// Product Master API
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
// Tasks API
// ============================================================================

export const tasksApi = {
  // Get all tasks with optional filters
  getTasks: async (params?: {
    status?: string;
    priority?: string;
    assigned_to?: string;
    store_id?: string;
    skip?: number;
    limit?: number;
  }) => {
    const response = await api.get('/tasks', { params });
    return response.data;
  },

  // Get tasks assigned to current user
  getMyTasks: async (includeCompleted: boolean = false) => {
    const response = await api.get('/tasks/my', { params: { include_completed: includeCompleted } });
    return response.data;
  },

  // Get overdue tasks
  getOverdueTasks: async (storeId?: string) => {
    const response = await api.get('/tasks/overdue', { params: { store_id: storeId } });
    return response.data;
  },

  // Get escalated tasks
  getEscalatedTasks: async () => {
    const response = await api.get('/tasks/escalated');
    return response.data;
  },

  // Get task summary/stats
  getTaskSummary: async (storeId?: string) => {
    const response = await api.get('/tasks/summary', { params: { store_id: storeId } });
    return response.data;
  },

  // Get single task by ID
  getTask: async (taskId: string) => {
    const response = await api.get(`/tasks/${taskId}`);
    return response.data;
  },

  // Create a new task
  createTask: async (task: {
    title: string;
    description?: string;
    category: string;
    priority?: string;
    assigned_to: string;
    due_at: string;
    linked_entity_type?: string;
    linked_entity_id?: string;
  }) => {
    const response = await api.post('/tasks', task);
    return response.data;
  },

  // Update a task
  updateTask: async (taskId: string, updates: {
    title?: string;
    description?: string;
    priority?: string;
    due_at?: string;
  }) => {
    const response = await api.put(`/tasks/${taskId}`, updates);
    return response.data;
  },

  // Start a task
  startTask: async (taskId: string) => {
    const response = await api.post(`/tasks/${taskId}/start`);
    return response.data;
  },

  // Complete a task
  completeTask: async (taskId: string, notes?: string) => {
    const response = await api.post(`/tasks/${taskId}/complete`, null, { params: { notes } });
    return response.data;
  },

  // Escalate a task
  escalateTask: async (taskId: string, escalateTo: string, level: number = 1) => {
    const response = await api.post(`/tasks/${taskId}/escalate`, null, {
      params: { escalate_to: escalateTo, level }
    });
    return response.data;
  },

  // Reassign a task
  reassignTask: async (taskId: string, newAssignee: string) => {
    const response = await api.post(`/tasks/${taskId}/reassign`, null, {
      params: { new_assignee: newAssignee }
    });
    return response.data;
  },
};

// ============================================================================
// Vendors API
// ============================================================================

export const vendorsApi = {
  // Vendors
  getVendors: async (params?: { search?: string; is_active?: boolean }) => {
    const response = await api.get('/vendors', { params });
    return response.data;
  },

  getVendor: async (vendorId: string) => {
    const response = await api.get(`/vendors/${vendorId}`);
    return response.data;
  },

  createVendor: async (vendor: {
    legal_name: string;
    trade_name: string;
    vendor_type?: string;
    gstin_status: string;
    gstin?: string;
    address: string;
    city: string;
    state: string;
    mobile: string;
    email?: string;
    credit_days?: number;
  }) => {
    const response = await api.post('/vendors', vendor);
    return response.data;
  },

  updateVendor: async (vendorId: string, updates: Partial<{
    legal_name: string;
    trade_name: string;
    address: string;
    city: string;
    state: string;
    mobile: string;
    email: string;
    credit_days: number;
    is_active: boolean;
  }>) => {
    const response = await api.put(`/vendors/${vendorId}`, updates);
    return response.data;
  },

  // Purchase Orders
  getPurchaseOrders: async (params?: { vendor_id?: string; status?: string; store_id?: string }) => {
    const response = await api.get('/vendors/purchase-orders', { params });
    return response.data;
  },

  getPurchaseOrder: async (poId: string) => {
    const response = await api.get(`/vendors/purchase-orders/${poId}`);
    return response.data;
  },

  createPurchaseOrder: async (po: {
    vendor_id: string;
    delivery_store_id: string;
    items: Array<{
      product_id: string;
      product_name: string;
      sku: string;
      quantity: number;
      unit_price: number;
    }>;
    expected_date?: string;
    notes?: string;
  }) => {
    const response = await api.post('/vendors/purchase-orders', po);
    return response.data;
  },

  sendPurchaseOrder: async (poId: string) => {
    const response = await api.post(`/vendors/purchase-orders/${poId}/send`);
    return response.data;
  },

  cancelPurchaseOrder: async (poId: string, reason: string) => {
    const response = await api.post(`/vendors/purchase-orders/${poId}/cancel`, null, { params: { reason } });
    return response.data;
  },

  // GRN (Goods Received Notes)
  getGRNs: async (params?: { store_id?: string; status?: string; po_id?: string }) => {
    const response = await api.get('/vendors/grn', { params });
    return response.data;
  },

  getGRN: async (grnId: string) => {
    const response = await api.get(`/vendors/grn/${grnId}`);
    return response.data;
  },

  createGRN: async (grn: {
    po_id: string;
    vendor_invoice_no: string;
    vendor_invoice_date: string;
    items: Array<{
      po_item_id: string;
      product_id: string;
      received_qty: number;
      accepted_qty: number;
      rejected_qty?: number;
      rejection_reason?: string;
    }>;
    notes?: string;
  }) => {
    const response = await api.post('/vendors/grn', grn);
    return response.data;
  },

  acceptGRN: async (grnId: string) => {
    const response = await api.post(`/vendors/grn/${grnId}/accept`);
    return response.data;
  },

  escalateGRN: async (grnId: string, note: string) => {
    const response = await api.post(`/vendors/grn/${grnId}/escalate`, null, { params: { note } });
    return response.data;
  },

  // Get pending GRN items for stock acceptance (combines PO items awaiting GRN)
  getPendingStock: async (storeId: string) => {
    const response = await api.get('/vendors/grn', { params: { store_id: storeId, status: 'PENDING' } });
    return response.data;
  },
};

// ============================================================================
// Settings API - Extended settings management
// ============================================================================

export const settingsApi = {
  // Profile
  getProfile: async () => {
    const response = await api.get('/settings/profile');
    return response.data;
  },

  updateProfile: async (data: { full_name?: string; phone?: string; email?: string }) => {
    const response = await api.put('/settings/profile', data);
    return response.data;
  },

  changePassword: async (data: { current_password: string; new_password: string }) => {
    const response = await api.post('/settings/profile/change-password', data);
    return response.data;
  },

  getPreferences: async () => {
    const response = await api.get('/settings/profile/preferences');
    return response.data;
  },

  updatePreferences: async (preferences: Record<string, unknown>) => {
    const response = await api.put('/settings/profile/preferences', preferences);
    return response.data;
  },

  // Business Settings
  getBusinessSettings: async () => {
    const response = await api.get('/settings/business');
    return response.data;
  },

  updateBusinessSettings: async (settings: {
    company_name?: string;
    company_short_name?: string;
    tagline?: string;
    logo_url?: string;
    primary_color?: string;
    secondary_color?: string;
    support_email?: string;
    support_phone?: string;
    website?: string;
    address?: string;
  }) => {
    const response = await api.put('/settings/business', settings);
    return response.data;
  },

  // Tax Settings
  getTaxSettings: async () => {
    const response = await api.get('/settings/tax');
    return response.data;
  },

  updateTaxSettings: async (settings: {
    gst_enabled?: boolean;
    company_gstin?: string;
    default_gst_rate?: number;
    hsn_validation?: boolean;
    e_invoice_enabled?: boolean;
    e_way_bill_enabled?: boolean;
    e_way_bill_threshold?: number;
  }) => {
    const response = await api.put('/settings/tax', settings);
    return response.data;
  },

  // Invoice Settings
  getInvoiceSettings: async () => {
    const response = await api.get('/settings/invoice');
    return response.data;
  },

  updateInvoiceSettings: async (settings: {
    invoice_prefix?: string;
    invoice_start_number?: number;
    financial_year?: string;
    show_logo_on_invoice?: boolean;
    show_terms_on_invoice?: boolean;
    default_terms?: string;
    default_warranty_days?: number;
    show_qr_code?: boolean;
  }) => {
    const response = await api.put('/settings/invoice', settings);
    return response.data;
  },

  // Notification Templates
  getNotificationTemplates: async () => {
    const response = await api.get('/settings/notifications/templates');
    return response.data;
  },

  getNotificationTemplate: async (templateId: string) => {
    const response = await api.get(`/settings/notifications/templates/${templateId}`);
    return response.data;
  },

  updateNotificationTemplate: async (templateId: string, template: {
    template_type?: string;
    trigger_event?: string;
    is_enabled?: boolean;
    subject?: string;
    content?: string;
    variables?: string[];
  }) => {
    const response = await api.put(`/settings/notifications/templates/${templateId}`, template);
    return response.data;
  },

  createNotificationTemplate: async (template: {
    template_id: string;
    template_type: string;
    trigger_event: string;
    is_enabled: boolean;
    subject?: string;
    content: string;
    variables: string[];
  }) => {
    const response = await api.post('/settings/notifications/templates', template);
    return response.data;
  },

  deleteNotificationTemplate: async (templateId: string) => {
    const response = await api.delete(`/settings/notifications/templates/${templateId}`);
    return response.data;
  },

  testNotification: async (templateId: string, testPhone?: string, testEmail?: string) => {
    const response = await api.post('/settings/notifications/test', { template_id: templateId, test_phone: testPhone, test_email: testEmail });
    return response.data;
  },

  // Notification Providers
  getNotificationProviders: async () => {
    const response = await api.get('/settings/notifications/providers');
    return response.data;
  },

  updateNotificationProvider: async (provider: {
    provider: string;
    api_key: string;
    api_secret?: string;
    sender_id?: string;
    webhook_url?: string;
    is_active: boolean;
  }) => {
    const response = await api.put('/settings/notifications/providers', provider);
    return response.data;
  },

  // Notification Logs
  getNotificationLogs: async (params?: {
    customer_id?: string;
    template_id?: string;
    channel?: string;
    status?: string;
    start_date?: string;
    end_date?: string;
    limit?: number;
    offset?: number;
  }) => {
    const response = await api.get('/settings/notifications/logs', { params });
    return response.data;
  },

  // Send Notification
  sendNotification: async (notification: {
    template_id: string;
    customer_id?: string;
    phone?: string;
    email?: string;
    variables: Record<string, string>;
    channel?: 'SMS' | 'WHATSAPP' | 'EMAIL';
  }) => {
    const response = await api.post('/notifications/send', notification);
    return response.data;
  },

  // Bulk Notifications
  sendBulkNotifications: async (notifications: {
    template_id: string;
    recipients: Array<{
      customer_id?: string;
      phone?: string;
      email?: string;
      variables: Record<string, string>;
    }>;
    channel?: 'SMS' | 'WHATSAPP' | 'EMAIL';
  }) => {
    const response = await api.post('/notifications/send-bulk', notifications);
    return response.data;
  },

  // Printer Settings
  getPrinterSettings: async () => {
    const response = await api.get('/settings/printers');
    return response.data;
  },

  updatePrinterSettings: async (settings: {
    receipt_printer_name?: string;
    receipt_printer_width?: number;
    label_printer_name?: string;
    label_size?: string;
    auto_print_receipt?: boolean;
    auto_print_job_card?: boolean;
    copies_per_print?: number;
  }) => {
    const response = await api.put('/settings/printers', settings);
    return response.data;
  },

  getAvailablePrinters: async () => {
    const response = await api.get('/settings/printers/available');
    return response.data;
  },

  // Discount Rules
  getDiscountRules: async () => {
    const response = await api.get('/settings/discount-rules');
    return response.data;
  },

  updateDiscountRules: async (rules: Record<string, Record<string, number>>) => {
    const response = await api.put('/settings/discount-rules', rules);
    return response.data;
  },

  // Integrations
  getIntegrations: async () => {
    const response = await api.get('/settings/integrations');
    return response.data;
  },

  getIntegration: async (integrationType: string) => {
    const response = await api.get(`/settings/integrations/${integrationType}`);
    return response.data;
  },

  updateIntegration: async (integrationType: string, config: {
    integration_type: string;
    enabled: boolean;
    config: Record<string, unknown>;
  }) => {
    const response = await api.put(`/settings/integrations/${integrationType}`, config);
    return response.data;
  },

  testIntegration: async (integrationType: string) => {
    const response = await api.post(`/settings/integrations/${integrationType}/test`);
    return response.data;
  },

  // System Settings
  getSystemSettings: async () => {
    const response = await api.get('/settings/system');
    return response.data;
  },

  updateSystemSettings: async (settings: Record<string, unknown>) => {
    const response = await api.put('/settings/system', settings);
    return response.data;
  },

  // Audit Logs
  getAuditLogs: async (params?: {
    entity_type?: string;
    entity_id?: string;
    user_id?: string;
    action?: string;
    limit?: number;
    offset?: number;
  }) => {
    const response = await api.get('/settings/audit-logs', { params });
    return response.data;
  },

  getAuditSummary: async () => {
    const response = await api.get('/settings/audit-logs/summary');
    return response.data;
  },
};

export default api;
