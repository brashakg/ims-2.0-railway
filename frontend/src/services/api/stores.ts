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
// Org setup - store CRUD with the real backend StoreCreate/StoreUpdate shape
// (entity_id required; GSTIN is derived server-side from the entity by state).
// ============================================================================

export interface StorePayload {
  store_code?: string;
  store_name?: string;
  brand?: string;
  entity_id?: string;
  address?: string;
  city?: string;
  state?: string;
  state_code?: string;
  pincode?: string;
  phone?: string;
  email?: string;
  whatsapp?: string;
  enabled_categories?: string[];
  latitude?: number | null;
  longitude?: number | null;
  geofence_radius_m?: number | null;
  locality?: string;
  landmark?: string;
  store_type?: string;
  region?: string;
  opening_date?: string;
  manager_user_id?: string;
  working_hours?: string;
  weekly_off?: string;
  upi_vpa?: string;
  cost_center?: string;
  invoice_prefix?: string;
  invoice_header?: string;
  invoice_footer?: string;
  invoice_terms?: string;
  is_active?: boolean;
}

export interface Store extends StorePayload {
  store_id: string;
  gstin?: string;
  is_hq?: boolean;
}

export const orgStoreApi = {
  list: async () => {
    const response = await api.get('/stores', { params: { active_only: false } });
    return response.data as { stores: Store[]; total: number };
  },
  create: async (payload: StorePayload) => {
    const response = await api.post('/stores', payload);
    return response.data as { store_id: string; gstin?: string; message: string };
  },
  update: async (storeId: string, payload: StorePayload) => {
    const response = await api.put(`/stores/${storeId}`, payload);
    return response.data;
  },
  remove: async (storeId: string) => {
    const response = await api.delete(`/stores/${storeId}`);
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

  getStoreUsers: async (
    storeId: string,
    opts?: { roles?: string[]; activeOnly?: boolean },
  ) => {
    const params: Record<string, string> = {};
    if (opts?.roles?.length) {
      params.roles = opts.roles.join(',');
    }
    if (opts?.activeOnly === false) {
      params.active_only = 'false';
    }
    const response = await api.get(`/stores/${storeId}/users`, { params });
    return response.data;
  },
};

// ============================================================================
// Admin API - User Management
// ============================================================================

export const adminUserApi = {
  getUsers: async (params?: { storeId?: string; role?: string; status?: string }) => {
    // Convert camelCase storeId -> snake_case store_id; the backend Query param
    // is store_id, so passing storeId raw silently dropped the store filter.
    const { storeId, ...rest } = params ?? {};
    const apiParams = { ...rest, ...(storeId ? { store_id: storeId } : {}) };
    const response = await api.get('/users', { params: apiParams });
    return response.data;
  },

  getUser: async (userId: string) => {
    const response = await api.get(`/users/${userId}`);
    return response.data;
  },

  createUser: async (data: {
    name: string;
    email: string;
    phone?: string;
    role?: string;
    storeId?: string;
    password?: string;
    username?: string;
    status?: string;
  }) => {
    // Map the UI's shape (name/role/storeId) onto the backend UserCreate
    // contract (full_name/roles[]/store_ids[]). Sending the raw UI shape was
    // a 422: `username` + `full_name` are required and were never sent, and
    // `role`/`storeId` (wrong names) were silently dropped so the new user got
    // no role/store. Derive a username from the email local-part when the form
    // doesn't supply one.
    const fullName = (data.name || '').trim();
    const emailLocal = (data.email || '').split('@')[0] || '';
    const derived = (data.username || emailLocal || fullName)
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '')
      .slice(0, 40);
    const username = derived.length >= 3 ? derived : `user${Date.now().toString().slice(-6)}`;
    const hasRealPwd = !!(data.password && data.password.length >= 8);
    const payload: Record<string, unknown> = {
      username,
      email: data.email,
      full_name: fullName.length >= 2 ? fullName : username,
      password: hasRealPwd ? data.password : 'Welcome@123',
      // A temp/auto password must be changed on first login (not become the
      // permanent password).
      must_change_password: !hasRealPwd,
    };
    if (data.phone) payload.phone = data.phone;
    if (data.role) payload.roles = [data.role];
    if (data.storeId) {
      payload.store_ids = [data.storeId];
      payload.primary_store_id = data.storeId;
    }
    const response = await api.post('/users', payload);
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
    // Same name->full_name / role->roles[] / storeId->store_ids[] mapping the
    // backend UserUpdate expects (raw UI shape silently dropped role + store).
    const payload: Record<string, unknown> = {};
    if (data.name !== undefined) payload.full_name = data.name;
    if (data.phone !== undefined) payload.phone = data.phone;
    if (data.role) payload.roles = [data.role];
    if (data.storeId) {
      payload.store_ids = [data.storeId];
      payload.primary_store_id = data.storeId;
    }
    if (data.status !== undefined) payload.is_active = data.status === 'ACTIVE';
    const response = await api.put(`/users/${userId}`, payload);
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

  // restoreBackup / downloadBackup / exportData / importData removed —
  // the backend routes are either missing (404) or hardcoded 501 stubs, and
  // nothing renders these. The Settings Import/Export buttons were removed
  // too. Re-add with real implementations when bulk import/export is built.

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
