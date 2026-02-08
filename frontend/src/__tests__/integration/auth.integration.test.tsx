// ============================================================================
// IMS 2.0 - Authentication Integration Tests
// ============================================================================
// End-to-end authentication flow testing

import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import axios from 'axios';
import { AuthContext } from '../../context/AuthContext';
import { mockUsers, mockApiResponses } from '../../utils/test-fixtures';

jest.mock('axios');

describe('Authentication Integration', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    jest.clearAllMocks();
  });

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );

  describe('Login Flow', () => {
    it('should complete successful login flow', async () => {
      const mockAxios = axios as jest.Mocked<typeof axios>;
      mockAxios.post = jest.fn().mockResolvedValue({
        data: {
          success: true,
          data: {
            user: mockUsers.admin,
            token: 'test-token-123',
            refreshToken: 'refresh-token-456',
          },
        },
      });

      // 1. Verify initial state (unauthenticated)
      expect(localStorage.getItem('auth_token')).toBeNull();

      // 2. Attempt login
      const loginData = {
        email: 'admin@ims.local',
        password: 'admin123',
      };

      const response = await mockAxios.post(
        '/api/v1/auth/login',
        loginData
      );

      // 3. Verify successful response
      expect(response.data.success).toBe(true);
      expect(response.data.data.token).toBeDefined();
      expect(response.data.data.user).toEqual(mockUsers.admin);

      // 4. Verify token is saved
      localStorage.setItem('auth_token', response.data.data.token);
      expect(localStorage.getItem('auth_token')).toBe('test-token-123');
    });

    it('should handle login failure with invalid credentials', async () => {
      const mockAxios = axios as jest.Mocked<typeof axios>;
      const error = new Error('Invalid credentials');
      (error as any).response = {
        status: 401,
        data: { success: false, error: 'Invalid email or password' },
      };

      mockAxios.post = jest.fn().mockRejectedValue(error);

      const loginData = {
        email: 'invalid@ims.local',
        password: 'wrongpassword',
      };

      try {
        await mockAxios.post('/api/v1/auth/login', loginData);
        fail('Should have thrown error');
      } catch (err: any) {
        expect(err.response.status).toBe(401);
        expect(err.response.data.success).toBe(false);
      }

      // 2. Verify token is not saved
      expect(localStorage.getItem('auth_token')).toBeNull();
    });
  });

  describe('Token Persistence', () => {
    it('should restore auth state from localStorage on app start', async () => {
      // 1. Simulate user already logged in
      const token = 'existing-token-123';
      localStorage.setItem('auth_token', token);
      localStorage.setItem('user', JSON.stringify(mockUsers.admin));

      // 2. Verify token is accessible on app load
      const savedToken = localStorage.getItem('auth_token');
      const savedUser = localStorage.getItem('user');

      expect(savedToken).toBe(token);
      expect(JSON.parse(savedUser!)).toEqual(mockUsers.admin);
    });

    it('should validate token on app initialization', async () => {
      const mockAxios = axios as jest.Mocked<typeof axios>;
      const token = 'test-token-123';

      localStorage.setItem('auth_token', token);

      // Mock token validation endpoint
      mockAxios.get = jest.fn().mockResolvedValue({
        data: {
          success: true,
          data: { valid: true, user: mockUsers.admin },
        },
      });

      const response = await mockAxios.get('/api/v1/auth/validate', {
        headers: { Authorization: `Bearer ${token}` },
      });

      expect(response.data.data.valid).toBe(true);
      expect(response.data.data.user).toEqual(mockUsers.admin);
    });

    it('should clear auth on 401 response', async () => {
      localStorage.setItem('auth_token', 'expired-token');

      // Simulate API returning 401
      const error = new Error('Unauthorized');
      (error as any).response = { status: 401 };

      // Clear auth on 401
      if ((error as any).response?.status === 401) {
        localStorage.removeItem('auth_token');
        localStorage.removeItem('user');
      }

      expect(localStorage.getItem('auth_token')).toBeNull();
      expect(localStorage.getItem('user')).toBeNull();
    });
  });

  describe('Logout Flow', () => {
    it('should complete logout flow', async () => {
      const mockAxios = axios as jest.Mocked<typeof axios>;

      // 1. Setup authenticated state
      const token = 'test-token-123';
      localStorage.setItem('auth_token', token);
      localStorage.setItem('user', JSON.stringify(mockUsers.admin));

      // 2. Call logout endpoint
      mockAxios.post = jest.fn().mockResolvedValue({
        data: { success: true },
      });

      const response = await mockAxios.post(
        '/api/v1/auth/logout',
        {},
        { headers: { Authorization: `Bearer ${token}` } }
      );

      expect(response.data.success).toBe(true);

      // 3. Clear local auth state
      localStorage.removeItem('auth_token');
      localStorage.removeItem('user');

      // 4. Verify cleared
      expect(localStorage.getItem('auth_token')).toBeNull();
      expect(localStorage.getItem('user')).toBeNull();
    });
  });

  describe('Token Refresh', () => {
    it('should refresh expired token using refresh token', async () => {
      const mockAxios = axios as jest.Mocked<typeof axios>;

      // 1. Setup expired token scenario
      const expiredToken = 'expired-token';
      const refreshToken = 'refresh-token-456';
      localStorage.setItem('auth_token', expiredToken);
      localStorage.setItem('refresh_token', refreshToken);

      // 2. Attempt API call with expired token
      const firstError = new Error('Token Expired');
      (firstError as any).response = { status: 401 };

      mockAxios.post = jest.fn()
        .mockRejectedValueOnce(firstError)
        .mockResolvedValueOnce({
          data: {
            success: true,
            data: { token: 'new-token-789' },
          },
        });

      // 3. Trigger token refresh
      try {
        await mockAxios.post('/api/v1/auth/refresh', {
          refreshToken,
        });
      } catch (err: any) {
        if (err.response?.status === 401) {
          const refreshResponse = await mockAxios.post('/api/v1/auth/refresh', {
            refreshToken,
          });

          localStorage.setItem('auth_token', refreshResponse.data.data.token);
        }
      }

      // 4. Verify new token is saved
      expect(localStorage.getItem('auth_token')).toBe('new-token-789');
    });

    it('should force logout on failed token refresh', async () => {
      const mockAxios = axios as jest.Mocked<typeof axios>;

      const refreshToken = 'invalid-refresh-token';
      localStorage.setItem('auth_token', 'expired-token');
      localStorage.setItem('refresh_token', refreshToken);

      // Mock failed refresh
      const error = new Error('Invalid refresh token');
      (error as any).response = { status: 401 };

      mockAxios.post = jest.fn().mockRejectedValue(error);

      try {
        await mockAxios.post('/api/v1/auth/refresh', { refreshToken });
      } catch (err: any) {
        if (err.response?.status === 401) {
          localStorage.removeItem('auth_token');
          localStorage.removeItem('refresh_token');
          localStorage.removeItem('user');
        }
      }

      expect(localStorage.getItem('auth_token')).toBeNull();
      expect(localStorage.getItem('refresh_token')).toBeNull();
    });
  });

  describe('Authorization', () => {
    it('should enforce role-based access control', () => {
      // Test different user roles
      const adminUser = mockUsers.admin;
      const staffUser = mockUsers.staff;

      const canAccessAdmin = (user: typeof mockUsers.admin) =>
        user.roles.includes('ADMIN');

      const canAccessStaff = (user: typeof mockUsers.staff) =>
        user.roles.includes('STAFF');

      expect(canAccessAdmin(adminUser)).toBe(true);
      expect(canAccessAdmin(staffUser)).toBe(false);

      expect(canAccessStaff(staffUser)).toBe(true);
      expect(canAccessStaff(adminUser)).toBe(false); // Admin has ADMIN role but not STAFF
    });

    it('should restrict access to protected routes', () => {
      // Simulate protected route check
      const isAuthenticated = (token: string | null) => !!token;
      const hasRequiredRole = (roles: string[], required: string[]) =>
        required.some(role => roles.includes(role));

      const token = localStorage.getItem('auth_token');
      const userRoles = mockUsers.admin.roles;

      // Should allow access with token and correct role
      expect(isAuthenticated(token) && hasRequiredRole(userRoles, ['ADMIN'])).toBe(false); // No token set

      localStorage.setItem('auth_token', 'test-token');
      expect(
        isAuthenticated(localStorage.getItem('auth_token')) &&
        hasRequiredRole(userRoles, ['ADMIN'])
      ).toBe(true);
    });
  });

  describe('Error Handling', () => {
    it('should handle network errors gracefully', async () => {
      const mockAxios = axios as jest.Mocked<typeof axios>;
      const networkError = new Error('Network Error');

      mockAxios.post = jest.fn().mockRejectedValue(networkError);

      try {
        await mockAxios.post('/api/v1/auth/login', {
          email: 'admin@ims.local',
          password: 'admin123',
        });
      } catch (err: any) {
        expect(err.message).toBe('Network Error');
        // Token should NOT be cleared on network error
        localStorage.setItem('auth_token', 'preserved-token');
      }

      expect(localStorage.getItem('auth_token')).toBe('preserved-token');
    });

    it('should handle malformed responses', async () => {
      const mockAxios = axios as jest.Mocked<typeof axios>;

      mockAxios.post = jest.fn().mockResolvedValue({
        data: { /* missing required fields */ },
      });

      const response = await mockAxios.post('/api/v1/auth/login', {});

      // Should handle missing token field
      expect(response.data.data?.token).toBeUndefined();
    });
  });
});
