// ============================================================================
// IMS 2.0 - API Service
// ============================================================================

import axios from 'axios';
import type { AxiosInstance, AxiosError, InternalAxiosRequestConfig } from 'axios';
import type { ApiResponse, LoginCredentials, LoginResponse, User } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

// Create axios instance
const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor - add auth token
api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('ims_token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor - handle errors
api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ message?: string; detail?: string }>) => {
    if (error.response?.status === 401) {
      // Clear auth state on unauthorized
      localStorage.removeItem('ims_token');
      localStorage.removeItem('ims_user');
      window.location.href = '/login';
    }

    const message =
      error.response?.data?.message ||
      error.response?.data?.detail ||
      error.message ||
      'An error occurred';

    return Promise.reject(new Error(message));
  }
);

// ============================================================================
// Auth API
// ============================================================================

export const authApi = {
  login: async (credentials: LoginCredentials): Promise<LoginResponse> => {
    const response = await api.post<LoginResponse>('/auth/login', credentials);
    return response.data;
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

export default api;
