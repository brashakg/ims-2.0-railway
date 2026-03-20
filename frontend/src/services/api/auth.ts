// ============================================================================
// IMS 2.0 - Auth API
// ============================================================================

import api, { getSecureApiUrl } from './client';
import type { ApiResponse, LoginCredentials, LoginResponse, User } from '../../types';

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
          discountCap: 0,
          isActive: true,
          geoRestricted: false,
          createdAt: new Date().toISOString(),
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
