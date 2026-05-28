// ============================================================================
// IMS 2.0 - Auth API
// ============================================================================

import api, { getSecureApiUrl } from './client';
import type { ApiResponse, LoginCredentials, LoginResponse, User } from '../../types';

// Mirrors backend/api/services/role_caps.py. Only used as a fallback when
// the server response is missing `discount_cap` (pre-fix backends). The
// authoritative cap is always server-side; this prevents a missing field
// from silently locking SUPERADMIN at 0%.
const FRONTEND_ROLE_DISCOUNT_CAPS: Record<string, number> = {
  SUPERADMIN: 100,
  ADMIN: 100,
  AREA_MANAGER: 25,
  STORE_MANAGER: 20,
  INVENTORY_MANAGER: 20,
  SALES_CASHIER: 10,
  SALES_STAFF: 10,
};
function roleDiscountCapFallback(roles: string[]): number {
  if (!roles?.length) return 0;
  return Math.max(...roles.map((r) => FRONTEND_ROLE_DISCOUNT_CAPS[r] ?? 0));
}

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
        // Role-aware effective cap from the server (added May 2026).
        // SUPERADMIN/ADMIN = 100; managers see their role baseline; sales = 10.
        // Optional because pre-fix backends won't include it — we fall
        // back to a local role lookup so old servers still work.
        discount_cap?: number;
        // True when the user must change an admin-set temporary password
        // before using the app. Optional for pre-fix backends.
        must_change_password?: boolean;
      };
    }

    try {
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
          roles: data.user.roles as import('../../types').UserRole[],
          activeRole: data.user.roles[0] as import('../../types').UserRole,
          storeIds: data.user.store_ids,
          activeStoreId: data.user.active_store_id,
          // Prefer server's role-aware cap. Fallback to role lookup so a
          // missing field never silently locks a SUPERADMIN at 0%.
          discountCap: data.user.discount_cap ?? roleDiscountCapFallback(data.user.roles),
          isActive: true,
          geoRestricted: false,
          createdAt: new Date().toISOString(),
          mustChangePassword: data.user.must_change_password ?? false,
        },
      };
    } catch (error) {
      // Return error response instead of throwing
      let errorMessage = 'Invalid username or password';

      if (error instanceof Error) {
        errorMessage = error.message;

        // Check if it's a network error
        if (error.message.includes('Network error') || error.message.includes('ERR_NETWORK')) {
          errorMessage = `Network error connecting to API. Please check your internet connection. API URL: ${getSecureApiUrl()}`;
        }
      }

      return {
        success: false,
        message: errorMessage,
        token: undefined,
        user: undefined,
      };
    }
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

  // Re-issue the JWT with a new active store. The backend bakes
  // active_store_id INTO the token, and store-scoped endpoints resolve the
  // store from the token; if we only flip client state the token keeps the
  // OLD store and writes can land on the wrong store (QA F9). Callers must
  // persist the returned access_token so the next request carries it.
  switchStore: async (
    storeId: string
  ): Promise<{ access_token: string; active_store_id: string }> => {
    const response = await api.post<{ access_token: string; active_store_id: string }>(
      `/auth/switch-store/${encodeURIComponent(storeId)}`
    );
    return response.data;
  },

  getProfile: async (): Promise<User> => {
    // Backend /auth/me returns the JWT payload verbatim (snake_case:
    // user_id, store_ids, active_store_id, roles, exp). Frontend User type
    // is camelCase. Without this transform, AuthContext overwrote the
    // freshly-logged-in user with one missing activeStoreId on every mount,
    // which made the topbar store pill flip to "No store" and broke POS.
    const response = await api.get<{
      user_id: string;
      username: string;
      full_name?: string;
      roles: string[];
      store_ids: string[];
      active_store_id: string;
      discount_cap?: number;
      must_change_password?: boolean;
      exp?: number;
    }>('/auth/me');
    const raw = response.data;
    return {
      id: raw.user_id,
      email: raw.username,
      name: raw.full_name ?? raw.username,
      phone: '',
      roles: raw.roles as import('../../types').UserRole[],
      activeRole: (raw.roles[0] ?? 'SALES_STAFF') as import('../../types').UserRole,
      storeIds: raw.store_ids ?? [],
      activeStoreId: raw.active_store_id,
      discountCap: raw.discount_cap ?? roleDiscountCapFallback(raw.roles),
      isActive: true,
      geoRestricted: false,
      createdAt: new Date().toISOString(),
      mustChangePassword: raw.must_change_password ?? false,
    };
  },

  changePassword: async (currentPassword: string, newPassword: string): Promise<ApiResponse<void>> => {
    const response = await api.post<ApiResponse<void>>('/auth/change-password', {
      current_password: currentPassword,
      new_password: newPassword,
    });
    return response.data;
  },
};
