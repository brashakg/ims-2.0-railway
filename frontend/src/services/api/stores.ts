// ============================================================================
// IMS 2.0 - Store Management API
// ============================================================================

import api from './client';

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
// Admin API - Store Management
// ============================================================================

export const adminStoreApi = {
  getStores: async () => {
    const response = await api.get('/stores');
    return response.data;
  },

  getStore: async (storeId: string) => {
    const response = await api.get(`/stores/${storeId}`);
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
    const response = await api.post('/stores', data);
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
    const response = await api.put(`/stores/${storeId}`, data);
    return response.data;
  },

  deleteStore: async (storeId: string) => {
    const response = await api.delete(`/stores/${storeId}`);
    return response.data;
  },

  getStoreUsers: async (storeId: string) => {
    const response = await api.get(`/stores/${storeId}/users`);
    return response.data;
  },
};

// ============================================================================
// Admin API - User Management
// ============================================================================

export const adminUserApi = {
  getUsers: async (params?: { storeId?: string; role?: string; status?: string }) => {
    const response = await api.get('/users', { params });
    return response.data;
  },

  getUser: async (userId: string) => {
    const response = await api.get(`/users/${userId}`);
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
    const response = await api.post('/users', data);
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
    const response = await api.put(`/users/${userId}`, data);
    return response.data;
  },

  deleteUser: async (userId: string) => {
    const response = await api.delete(`/users/${userId}`);
    return response.data;
  },

  resetPassword: async (userId: string, newPassword: string) => {
    const response = await api.post(`/users/${userId}/reset-password`, { new_password: newPassword });
    return response.data;
  },

  assignStore: async (userId: string, storeId: string, role?: string) => {
    const response = await api.post(`/users/${userId}/assign-store`, { store_id: storeId, role });
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
