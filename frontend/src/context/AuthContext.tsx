// ============================================================================
// IMS 2.0 - Authentication Context
// ============================================================================

import { createContext, useContext, useReducer, useEffect } from 'react';
import type { ReactNode } from 'react';
import type { User, AuthState, LoginCredentials, LoginResponse, UserRole } from '../types';
import { authApi } from '../services/api';
import { usePOSStore } from '../stores/posStore';
import { permissionToCapability, isUngrantableCapability } from '../utils/capabilities';

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
  /** Re-fetch the signed-in user from the server and update state + cache.
   *  Used after a forced password change so `mustChangePassword` flips to
   *  false and the app gate lifts without a full re-login. */
  refreshUser: () => Promise<void>;
  setActiveRole: (role: UserRole) => void;
  setActiveStore: (storeId: string) => void;
  hasPermission: (permission: string) => boolean;
  hasRole: (role: UserRole | UserRole[]) => boolean;
  canAccessStore: (storeId: string) => boolean;
  /** Per-user module gate. Returns `false` ONLY when the admin has explicitly
   *  denied this module for the user (moduleAccess[moduleKey] === false); any
   *  other value (missing, true) returns `true` so role defaults apply. This is
   *  a DENY-ONLY override -- it never grants access a role lacks; callers must
   *  still AND it with the role check (see ModuleContext / ProtectedRoute). */
  hasModuleAccess: (moduleKey: string) => boolean;
  /** True iff the user holds INVESTOR and no other role with write
   *  power. Mirrors the server-side _is_investor_only check in
   *  backend/api/main.py — UI hides write actions when this is true.
   */
  isReadOnly: () => boolean;
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

    case 'LOGIN_SUCCESS': {
      // Auto-set activeRole from roles array if not already set
      const user = action.payload.user;
      if (!user.activeRole && user.roles && user.roles.length > 0) {
        user.activeRole = user.roles[0];
      }
      return {
        ...state,
        user,
        token: action.payload.token,
        isAuthenticated: true,
        isLoading: false,
      };
    }

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
            throw new Error('Invalid token format');
          }

          // Try to verify token is still valid with API
          try {
            const profile = await authApi.getProfile();

            // Restore client-side selections (activeStoreId + activeRole) from
            // the cached user. The server's /auth/me returns active_store_id
            // from the JWT — which defaults to storeIds[0] at LOGIN TIME and
            // is NOT updated when the user picks a different store via the
            // topbar dropdown (that's a pure client-side change persisted to
            // localStorage). So on every browser refresh, the server hands
            // back the original-login store, overwriting the user's pick.
            // We must prefer the cached value over the server's "default"
            // whenever the user still has access to it.
            //
            // First attempt (#278) only restored when !profile.activeStoreId,
            // which failed because the server's default is always populated.
            // Owner verified: F5 still reset to Bokaro (their first assigned
            // store). This fix always overrides with the cached pick when
            // it's still permitted.
            try {
              const cached = JSON.parse(userJson) as User;
              const cachedStoreId = cached?.activeStoreId;
              if (cachedStoreId) {
                const hasCrossStoreAccess = (profile.roles || []).some(r =>
                  r === 'SUPERADMIN' || r === 'ADMIN' || r === 'AREA_MANAGER',
                );
                const isAssigned = (profile.storeIds || []).includes(cachedStoreId);
                if (hasCrossStoreAccess || isAssigned) {
                  profile.activeStoreId = cachedStoreId;
                }
              }
              const cachedRole = cached?.activeRole;
              if (cachedRole && (profile.roles || []).includes(cachedRole)) {
                profile.activeRole = cachedRole;
              }
            } catch {
              // cached parsing failed — fall through to the fallback below.
            }

            // Auto-set activeRole if still not set (fresh user, no cache).
            if (!profile.activeRole && profile.roles && profile.roles.length > 0) {
              profile.activeRole = profile.roles[0];
            }

            // Re-persist so the cache stays in lockstep with what we just
            // dispatched (matters when the cached user was missing some field
            // we just filled in above).
            try {
              localStorage.setItem('ims_user', JSON.stringify(profile));
            } catch {
              // localStorage may be unavailable in some browser modes — non-fatal.
            }

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
              localStorage.removeItem('ims_token');
              localStorage.removeItem('ims_user');
              localStorage.removeItem('ims_login_time');
              localStorage.removeItem('ims_active_module');
              dispatch({ type: 'LOGOUT' });
            } else if (errorMsg.includes('Network error') || errorMsg.includes('ERR_NETWORK')) {
              // Network error - don't clear storage, but still show login
              dispatch({ type: 'SET_LOADING', payload: false });
            } else {
              // Other errors - clear storage as safety measure
              localStorage.removeItem('ims_token');
              localStorage.removeItem('ims_user');
              localStorage.removeItem('ims_login_time');
              localStorage.removeItem('ims_active_module');
              dispatch({ type: 'LOGOUT' });
            }
          }
        } catch (error) {
          // JSON parsing or token format error
          localStorage.removeItem('ims_token');
          localStorage.removeItem('ims_user');
          localStorage.removeItem('ims_login_time');
          localStorage.removeItem('ims_active_module');
          dispatch({ type: 'LOGOUT' });
        }
      } else {
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
        // Auto-set activeRole from roles array
        if (!response.user.activeRole && response.user.roles && response.user.roles.length > 0) {
          response.user.activeRole = response.user.roles[0];
        }

        // Store auth data
        localStorage.setItem('ims_token', response.token);
        localStorage.setItem('ims_user', JSON.stringify(response.user));
        localStorage.setItem('ims_login_time', Date.now().toString());
        // Reset the sidebar to grouped/expanded on every login (key owned by
        // components/shell/Rail.tsx COLLAPSED_GROUPS_KEY).
        localStorage.removeItem('ims_rail_collapsed_groups');

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
      localStorage.removeItem('ims_active_module');
      // Clear POS state to prevent data leaking between users
      try { usePOSStore.getState().clearAllOnLogout(); } catch { /* ignore if store not ready */ }
      dispatch({ type: 'LOGOUT' });
    }
  };

  // Re-fetch the signed-in user from the server (e.g. after a forced password
  // change) and merge into state + cache. Preserves the client-side active
  // store / role picks, which the server's /auth/me default would otherwise
  // overwrite (same reasoning as the mount path above).
  const refreshUser = async () => {
    const profile = await authApi.getProfile();
    const current = state.user;
    if (current?.activeStoreId) {
      const hasCrossStoreAccess = (profile.roles || []).some(r =>
        r === 'SUPERADMIN' || r === 'ADMIN' || r === 'AREA_MANAGER',
      );
      const isAssigned = (profile.storeIds || []).includes(current.activeStoreId);
      if (hasCrossStoreAccess || isAssigned) {
        profile.activeStoreId = current.activeStoreId;
      }
    }
    if (current?.activeRole && (profile.roles || []).includes(current.activeRole)) {
      profile.activeRole = current.activeRole;
    } else if (!profile.activeRole && profile.roles && profile.roles.length > 0) {
      profile.activeRole = profile.roles[0];
    }
    try {
      localStorage.setItem('ims_user', JSON.stringify(profile));
    } catch {
      // localStorage may be unavailable in some browser modes — non-fatal.
    }
    dispatch({ type: 'UPDATE_USER', payload: profile });
  };

  // Set active role
  const setActiveRole = (role: UserRole) => {
    if (state.user?.roles?.includes(role)) {
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
    if (
      !(
        state.user?.storeIds.includes(storeId) ||
        hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'])
      )
    ) {
      return;
    }

    // Optimistic local update for a snappy switch.
    dispatch({ type: 'SET_ACTIVE_STORE', payload: storeId });
    const userJson = localStorage.getItem('ims_user');
    if (userJson) {
      try {
        const user = JSON.parse(userJson);
        user.activeStoreId = storeId;
        localStorage.setItem('ims_user', JSON.stringify(user));
      } catch {
        /* ignore malformed cache */
      }
    }

    // QA F9: re-issue the JWT so the token's active_store_id matches the UI.
    // The backend bakes active_store_id INTO the token and most store-scoped
    // endpoints resolve the store from the token; flipping only client state
    // left the token on the OLD store -> reads/writes could hit the wrong
    // store in a multi-store chain. The request interceptor reads `ims_token`
    // per request, so persisting the new token takes effect on the next call.
    // Fire-and-forget (keeps the void signature); failures leave the optimistic
    // state and re-sync on the next refresh/login.
    authApi
      .switchStore(storeId)
      .then(({ access_token, active_store_id }) => {
        if (access_token) {
          localStorage.setItem('ims_token', access_token);
        }
        if (active_store_id && active_store_id !== storeId) {
          // Server normalized the store — reconcile UI + cache to match.
          dispatch({ type: 'SET_ACTIVE_STORE', payload: active_store_id });
          const uj = localStorage.getItem('ims_user');
          if (uj) {
            try {
              const u = JSON.parse(uj);
              u.activeStoreId = active_store_id;
              localStorage.setItem('ims_user', JSON.stringify(u));
            } catch {
              /* ignore */
            }
          }
        }
      })
      .catch(() => {
        /* non-fatal: optimistic state stands; backend 403s if no store access */
      });
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
      // DESIGN_MANAGER (BVI merge): owns the Online Store image/design queue.
      // Read-mostly on the catalog plus a design-scoped grant for later phases.
      DESIGN_MANAGER: ['products.view', 'online_store.view', 'design.*'],
      OPTOMETRIST: ['clinical.*', 'pos.create'],
      CASHIER: ['pos.*', 'till.*'],
      // SALES_CASHIER merged into SALES_STAFF (backlog #12). Kept as a recognized
      // alias (UserRole union member) so a legacy user object still type-checks;
      // mirrors the survivor's client permissions until the next login re-issues
      // the token as SALES_STAFF (the backend normalizes the role either way).
      SALES_CASHIER: ['pos.create', 'pos.discount'],
      SALES_STAFF: ['pos.create', 'pos.discount'],
      WORKSHOP_STAFF: ['inventory.view', 'workshop.*'],
      // INVESTOR is read-only — explicitly enumerate the .view permissions
      // they should see. Backend middleware ALSO blocks any non-GET request
      // so this list is defence-in-depth, not the only barrier.
      INVESTOR: [
        'reports.view',
        'inventory.view',
        'finance.view',
        'pos.view',
        'customers.view',
        'orders.view',
      ],
    };

    // Check permissions for ALL user roles, not just activeRole
    const userRoles = state.user.roles || [];

    // SUPERADMIN and ADMIN bypass all permission checks (they are the actors who
    // SET overrides; an override must never lock the top admins out -- mirrors
    // the server-side SUPERADMIN exemption in the middleware).
    if (userRoles.includes('SUPERADMIN') || userRoles.includes('ADMIN')) return true;

    // ----- ROLE BASELINE (unchanged) -----
    const allPerms: string[] = [];
    for (const r of userRoles) {
      const perms = rolePermissions[r] || [];
      allPerms.push(...perms);
    }
    let roleAllowed: boolean;
    if (allPerms.includes('*')) {
      roleAllowed = true;
    } else {
      roleAllowed = allPerms.some((perm) => {
        if (perm === permission) return true;
        if (perm.endsWith('.*')) {
          const category = perm.slice(0, -2);
          return permission.startsWith(category + '.');
        }
        return false;
      });
    }

    // ----- PER-USER CAPABILITY OVERRIDE (council ruling sec.2) -----
    // Merge the user's grant/deny over the role baseline, mirroring the backend
    // permission_resolver's frozen precedence (deny beats grant beats role).
    // DARK: with no `permissions` field, or a permission that maps to no
    // capability, this returns `roleAllowed` unchanged -- identical to today.
    const cap = permissionToCapability(permission);
    if (cap) {
      const denies = state.user.permissions?.deny || {};
      const grants = state.user.permissions?.grant || {};
      if (denies[cap] === true) return false; // deny always wins
      if (!roleAllowed && grants[cap] === true && !isUngrantableCapability(cap)) {
        return true; // grant adds a role-denied capability
      }
    }
    return roleAllowed;
  };

  // Role check - now checks user.roles array instead of activeRole
  const hasRole = (role: UserRole | UserRole[]): boolean => {
    if (!state.user) return false;
    const userRoles = state.user.roles || [];

    // SUPERADMIN and ADMIN can access everything
    if (userRoles.includes('SUPERADMIN') || userRoles.includes('ADMIN')) return true;

    // Check if user has any of the required roles
    const requiredRoles = Array.isArray(role) ? role : [role];
    return requiredRoles.some(r => userRoles.includes(r));
  };

  // Store access check
  const canAccessStore = (storeId: string): boolean => {
    if (!state.user) return false;
    // SUPERADMIN, ADMIN, AREA_MANAGER can access all stores
    if (hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'])) return true;
    return (state.user.storeIds ?? []).includes(storeId);
  };

  /** True iff the user is an INVESTOR-only seat (no other role on the
   *  same account). Backend middleware enforces this server-side via
   *  `_is_investor_only` in main.py — the frontend uses this helper to
   *  hide write actions and show a top-of-app "read-only" banner. */
  const isReadOnly = (): boolean => {
    const userRoles = state.user?.roles || [];
    return userRoles.length === 1 && userRoles[0] === 'INVESTOR';
  };

  /** Deny-only per-user module gate. `false` ONLY when the admin explicitly set
   *  moduleAccess[moduleKey] === false; everything else (no map, missing key,
   *  true) is allowed so the role default stands. This NEVER grants access a
   *  role lacks -- the role filter still runs alongside it (see ModuleContext /
   *  ProtectedRoute), so the role remains the ceiling and there is no path to
   *  privilege escalation through this map. */
  const hasModuleAccess = (moduleKey: string): boolean => {
    const map = state.user?.moduleAccess;
    if (!map) return true;
    return map[moduleKey] !== false;
  };

  const value: AuthContextType = {
    ...state,
    login,
    logout,
    refreshUser,
    setActiveRole,
    setActiveStore,
    hasPermission,
    hasRole,
    canAccessStore,
    isReadOnly,
    hasModuleAccess,
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
