// ============================================================================
// IMS 2.0 - Authentication Context
// ============================================================================

import { createContext, useContext, useReducer, useEffect } from 'react';
import type { ReactNode } from 'react';
import type { User, AuthState, LoginCredentials, LoginResponse, UserRole } from '../types';
import { authApi } from '../services/api';

// ============================================================================
// Types
// ============================================================================

type AuthAction =
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'LOGIN_SUCCESS'; payload: { user: User; token: string } }
  | { type: 'LOGOUT' }
  | { type: 'SET_ACTIVE_ROLE'; payload: UserRole }
  | { type: 'SET_ACTIVE_STORE'; payload: string }
  | { type: 'UPDATE_USER'; payload: Partial<User> };

interface AuthContextType extends AuthState {
  login: (credentials: LoginCredentials) => Promise<LoginResponse>;
  logout: () => Promise<void>;
  setActiveRole: (role: UserRole) => void;
  setActiveStore: (storeId: string) => void;
  hasPermission: (permission: string) => boolean;
  hasRole: (role: UserRole | UserRole[]) => boolean;
  canAccessStore: (storeId: string) => boolean;
}

// ============================================================================
// Initial State
// ============================================================================

const initialState: AuthState = {
  user: null,
  token: null,
  isAuthenticated: false,
  isLoading: true,
};

// ============================================================================
// Reducer
// ============================================================================

function authReducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, isLoading: action.payload };

    case 'LOGIN_SUCCESS':
      return {
        ...state,
        user: action.payload.user,
        token: action.payload.token,
        isAuthenticated: true,
        isLoading: false,
      };

    case 'LOGOUT':
      return {
        ...initialState,
        isLoading: false,
      };

    case 'SET_ACTIVE_ROLE':
      if (!state.user) return state;
      return {
        ...state,
        user: { ...state.user, activeRole: action.payload },
      };

    case 'SET_ACTIVE_STORE':
      if (!state.user) return state;
      return {
        ...state,
        user: { ...state.user, activeStoreId: action.payload },
      };

    case 'UPDATE_USER':
      if (!state.user) return state;
      return {
        ...state,
        user: { ...state.user, ...action.payload },
      };

    default:
      return state;
  }
}

// ============================================================================
// Context
// ============================================================================

const AuthContext = createContext<AuthContextType | undefined>(undefined);

// ============================================================================
// Provider Component
// ============================================================================

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [state, dispatch] = useReducer(authReducer, initialState);

  // Check for existing token on mount
  useEffect(() => {
    const initializeAuth = async () => {
      const token = localStorage.getItem('ims_token');
      const userJson = localStorage.getItem('ims_user');

      if (token && userJson) {
        try {
          // Verify stored user JSON is valid (parsing test)
          JSON.parse(userJson) as User;

          // Check if token looks valid (basic JWT format check)
          const tokenParts = token.split('.');
          if (tokenParts.length !== 3) {
            console.warn('[Auth] Invalid token format detected, clearing storage');
            throw new Error('Invalid token format');
          }

          console.log('[Auth] Attempting to rehydrate session from localStorage');

          // Try to verify token is still valid with API
          try {
            const profile = await authApi.getProfile();
            console.log('[Auth] Token validation successful, restoring session');
            dispatch({
              type: 'LOGIN_SUCCESS',
              payload: { user: profile, token },
            });
            return;
          } catch (apiError) {
            // Check if it's a 401 (token expired/invalid) or network error
            const errorMsg = apiError instanceof Error ? apiError.message : '';

            // Only clear storage on auth errors (401), not network errors
            if (errorMsg.includes('401') || errorMsg.includes('Unauthorized') || errorMsg.includes('Invalid or expired token')) {
              console.warn('[Auth] Token validation failed with 401, clearing storage');
              localStorage.removeItem('ims_token');
              localStorage.removeItem('ims_user');
              localStorage.removeItem('ims_login_time');
              localStorage.removeItem('ims_active_module');
              dispatch({ type: 'LOGOUT' });
            } else if (errorMsg.includes('Network error') || errorMsg.includes('ERR_NETWORK')) {
              // Network error - don't clear storage, but still show login
              // User might have lost connectivity temporarily
              console.warn('[Auth] Network error during token validation, forcing login but preserving token');
              dispatch({ type: 'SET_LOADING', payload: false });
            } else {
              // Other errors - clear storage as safety measure
              console.error('[Auth] Unexpected error during token validation:', apiError);
              localStorage.removeItem('ims_token');
              localStorage.removeItem('ims_user');
              localStorage.removeItem('ims_login_time');
              localStorage.removeItem('ims_active_module');
              dispatch({ type: 'LOGOUT' });
            }
          }
        } catch (error) {
          // JSON parsing or token format error
          console.error('[Auth] Failed to parse stored auth data:', error);
          localStorage.removeItem('ims_token');
          localStorage.removeItem('ims_user');
          localStorage.removeItem('ims_login_time');
          localStorage.removeItem('ims_active_module');
          dispatch({ type: 'LOGOUT' });
        }
      } else {
        console.log('[Auth] No stored token found, user needs to login');
        dispatch({ type: 'SET_LOADING', payload: false });
      }
    };

    initializeAuth();
  }, []);

  // Login function
  const login = async (credentials: LoginCredentials): Promise<LoginResponse> => {
    dispatch({ type: 'SET_LOADING', payload: true });

    try {
      const response = await authApi.login(credentials);

      if (response.success && response.token && response.user) {
        // Store auth data
        localStorage.setItem('ims_token', response.token);
        localStorage.setItem('ims_user', JSON.stringify(response.user));
        localStorage.setItem('ims_login_time', Date.now().toString());

        dispatch({
          type: 'LOGIN_SUCCESS',
          payload: { user: response.user, token: response.token },
        });
      } else {
        // Login failed - ensure loading state is cleared and user is not set
        dispatch({ type: 'SET_LOADING', payload: false });
      }

      return response;
    } catch (error) {
      dispatch({ type: 'SET_LOADING', payload: false });
      throw error;
    }
  };

  // Logout function
  const logout = async () => {
    try {
      await authApi.logout();
    } catch {
      // Ignore logout errors
    } finally {
      localStorage.removeItem('ims_token');
      localStorage.removeItem('ims_user');
      localStorage.removeItem('ims_login_time');
      dispatch({ type: 'LOGOUT' });
    }
  };

  // Set active role
  const setActiveRole = (role: UserRole) => {
    if (state.user?.roles.includes(role)) {
      dispatch({ type: 'SET_ACTIVE_ROLE', payload: role });
      // Persist to localStorage
      const userJson = localStorage.getItem('ims_user');
      if (userJson) {
        const user = JSON.parse(userJson);
        user.activeRole = role;
        localStorage.setItem('ims_user', JSON.stringify(user));
      }
    }
  };

  // Set active store
  const setActiveStore = (storeId: string) => {
    if (state.user?.storeIds.includes(storeId) || hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'])) {
      dispatch({ type: 'SET_ACTIVE_STORE', payload: storeId });
      // Persist to localStorage
      const userJson = localStorage.getItem('ims_user');
      if (userJson) {
        const user = JSON.parse(userJson);
        user.activeStoreId = storeId;
        localStorage.setItem('ims_user', JSON.stringify(user));
      }
    }
  };

  // Permission check based on role hierarchy
  const hasPermission = (permission: string): boolean => {
    if (!state.user) return false;

    const rolePermissions: Record<UserRole, string[]> = {
      SUPERADMIN: ['*'], // All permissions
      ADMIN: ['admin.*', 'reports.*', 'settings.*', 'users.*', 'stores.*'],
      AREA_MANAGER: ['reports.view', 'inventory.view', 'inventory.transfer', 'hr.approve'],
      STORE_MANAGER: ['pos.*', 'inventory.accept', 'inventory.count', 'till.*', 'hr.view'],
      ACCOUNTANT: ['reports.*', 'expenses.approve', 'finance.*'],
      CATALOG_MANAGER: ['inventory.*', 'products.*'],
      OPTOMETRIST: ['clinical.*', 'pos.create'],
      CASHIER: ['pos.*', 'till.*'],
      SALES_CASHIER: ['pos.*', 'till.*'],
      SALES_STAFF: ['pos.create', 'pos.discount'],
      WORKSHOP_STAFF: ['inventory.view', 'workshop.*'],
    };

    const userPerms = rolePermissions[state.user.activeRole] || [];

    // Check for wildcard permission
    if (userPerms.includes('*')) return true;

    // Check for exact match or category wildcard
    return userPerms.some((perm) => {
      if (perm === permission) return true;
      if (perm.endsWith('.*')) {
        const category = perm.slice(0, -2);
        return permission.startsWith(category + '.');
      }
      return false;
    });
  };

  // Role check
  const hasRole = (role: UserRole | UserRole[]): boolean => {
    if (!state.user) return false;
    const roles = Array.isArray(role) ? role : [role];
    return roles.includes(state.user.activeRole);
  };

  // Store access check
  const canAccessStore = (storeId: string): boolean => {
    if (!state.user) return false;
    // SUPERADMIN, ADMIN, AREA_MANAGER can access all stores
    if (hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'])) return true;
    return (state.user.storeIds ?? []).includes(storeId);
  };

  const value: AuthContextType = {
    ...state,
    login,
    logout,
    setActiveRole,
    setActiveStore,
    hasPermission,
    hasRole,
    canAccessStore,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ============================================================================
// Hook
// ============================================================================

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

export default AuthContext;
