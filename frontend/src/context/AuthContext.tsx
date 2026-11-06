// ============================================================================
// IMS 2.0 - Authentication Context
// ============================================================================

import { createContext, useContext, useReducer, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { User, AuthState, LoginCredentials, LoginResponse, UserRole } from '../types';
import { authApi } from '../services/api';
import { usePOSStore } from '../stores/posStore';
import { permissionToCapability, isUngrantableCapability } from '../utils/capabilities';
import { userSeesAllStores } from '../utils/storeAccess';
import { USER_SHOPS_SYNCED_EVENT } from '../services/api/client';
import { dropPaletteRecents } from '../services/api/signOutEvent';
import { getSessionPolicy } from '../constants/sessionPolicy';
import { LAST_ACTIVITY_KEY, storedSessionIdleExpired } from '../hooks/useIdleLogout';
import { IDLE_LOGIN_PATH, autoParkForIdleLogout } from '../hooks/idleSignOut';
// Own line: keeps clear of #1379's import added beside the one above.
import { decodeJwtClaims, syncCachedUserShops } from '../services/api/client';

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
  /** Switch shop. Resolves true once the server confirmed it (screen, cache
   *  and token now all on the new shop); false when refused or failed (all
   *  three still on the old shop) or overtaken by a newer switch. */
  setActiveStore: (storeId: string) => Promise<boolean>;
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

/** The id of the person in the shared cache (ims_user), if readable. */
function storedUserId(): string | undefined {
  try {
    return (JSON.parse(localStorage.getItem('ims_user') || 'null') as User | null)?.id;
  } catch {
    return undefined;
  }
}

// ============================================================================
// Provider Component
// ============================================================================

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [state, dispatch] = useReducer(authReducer, initialState);
  const queryClient = useQueryClient();
  // Latest store switch; an answer to an older one is stale (R41).
  const switchSeq = useRef(0);
  // Sign-in session: login and logout bump it, so a switch answer asked for
  // in an older session is never applied to the next one (#1372 review).
  const sessionGen = useRef(0);
  // The person and shop on THIS tab's screen, read by async/event handlers.
  const userRef = useRef<User | null>(null);
  useEffect(() => { userRef.current = state.user; }, [state.user]);

  // A token refresh re-reads the login's shops from the new token
  // (services/api/client syncCachedUserShops): follow it on screen at once.
  useEffect(() => {
    const follow = () => {
      try {
        const raw = localStorage.getItem('ims_user');
        if (raw) dispatch({ type: 'UPDATE_USER', payload: JSON.parse(raw) as User });
      } catch {
        /* a malformed cache changes nothing */
      }
    };
    window.addEventListener(USER_SHOPS_SYNCED_EVENT, follow);
    return () => window.removeEventListener(USER_SHOPS_SYNCED_EVENT, follow);
  }, []);

  // Another tab signed out, idled out, or signed a different person in (they
  // share localStorage; the `storage` event fires only in the OTHER tabs).
  // This tab must not keep showing the old person's screens (Codex R21): empty
  // it and reload, so every in-memory view and store starts from the new
  // storage. A refresh rotating ims_token / ims_refresh_token for the SAME
  // person is not a sign-out and changes nothing.
  const signedInId = state.user?.id;
  useEffect(() => {
    if (!signedInId) return;
    const follow = (e: StorageEvent) => {
      if (e.key !== null && e.key !== 'ims_token' && e.key !== 'ims_user') return;
      let storedId: string | undefined;
      try {
        storedId = (JSON.parse(localStorage.getItem('ims_user') || 'null') as User | null)?.id;
      } catch { /* a malformed cache is no one */ }
      // Mid-login in the other tab the new token can land before the new
      // user: the ims_user event that follows decides.
      if (localStorage.getItem('ims_token') && (!storedId || storedId === signedInId)) return;
      dispatch({ type: 'LOGOUT' });
      queryClient.clear();
      window.location.reload();
    };
    window.addEventListener('storage', follow);
    return () => window.removeEventListener('storage', follow);
  }, [signedInId, queryClient]);

  // Check for existing token on mount
  useEffect(() => {
    const initializeAuth = async () => {
      const token = localStorage.getItem('ims_token');
      const userJson = localStorage.getItem('ims_user');

      if (token && userJson) {
        try {
          // Verify stored user JSON is valid (parsing test)
          const cachedUser = JSON.parse(userJson) as User;

          // Idle past the limit while the browser/tab was closed (owner
          // 11-01): sign out BEFORE any server call, so the stored token is
          // never resumed or silently refreshed. Same steps as the idle
          // watcher (autoParkForIdleLogout parks then clears synchronously, so
          // a second run under React StrictMode finds no cart to re-park).
          if (storedSessionIdleExpired(getSessionPolicy())) {
            autoParkForIdleLogout(cachedUser?.id);
            await logout();
            // AuthProvider sits outside the router, so a plain replace.
            window.location.replace(IDLE_LOGIN_PATH);
            return;
          }

          // Check if token looks valid (basic JWT format check)
          const tokenParts = token.split('.');
          if (tokenParts.length !== 3) {
            throw new Error('Invalid token format');
          }

          // Try to verify token is still valid with API
          try {
            const profile = await authApi.getProfile();

            // The shop is the server's: /auth/me answers with the token's
            // active_store_id, which a store switch re-issues (R41), so an
            // F5 keeps the pick. An older cache naming another shop (a switch
            // that failed before R41) is set back to it, never trusted.
            // Only the role pick is client-side and restored from the cache.
            try {
              const cached = JSON.parse(userJson) as User;
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
              localStorage.removeItem('ims_refresh_token');
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
              localStorage.removeItem('ims_refresh_token');
              localStorage.removeItem('ims_user');
              localStorage.removeItem('ims_login_time');
              localStorage.removeItem('ims_active_module');
              dispatch({ type: 'LOGOUT' });
            }
          }
        } catch (error) {
          // JSON parsing or token format error
          localStorage.removeItem('ims_token');
          localStorage.removeItem('ims_refresh_token');
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

  // Another tab of the SAME person switched shop: the shared token now acts
  // for that shop, so this tab shows it too and drops its shop data (#1372
  // review). A different person or a sign-out is the sign-out listener's.
  useEffect(() => {
    const follow = (e: StorageEvent) => {
      if (e.key !== null && e.key !== 'ims_token' && e.key !== 'ims_user') return;
      const me = userRef.current;
      const token = localStorage.getItem('ims_token');
      const claims = decodeJwtClaims(token);
      if (!me || !token || !claims || claims.user_id !== me.id || storedUserId() !== me.id) return;
      const shop = claims.active_store_id || '';
      syncCachedUserShops(token); // the cache follows the token too
      if (shop === me.activeStoreId) return;
      dispatch({ type: 'UPDATE_USER', payload: { activeStoreId: shop, storeIds: Array.isArray(claims.store_ids) ? claims.store_ids : me.storeIds } });
      void queryClient.resetQueries();
    };
    window.addEventListener('storage', follow);
    return () => window.removeEventListener('storage', follow);
  }, [queryClient]);

  // Login function
  const login = async (credentials: LoginCredentials): Promise<LoginResponse> => {
    sessionGen.current++;
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
        // Rotating refresh token (2026-07 token hardening): stored beside the
        // access token; the api client exchanges it for a fresh pair while the
        // user is active. Absent (pre-upgrade backend) -> clear any stale one.
        if (response.refreshToken) {
          localStorage.setItem('ims_refresh_token', response.refreshToken);
        } else {
          localStorage.removeItem('ims_refresh_token');
        }
        localStorage.setItem('ims_user', JSON.stringify(response.user));
        localStorage.setItem('ims_login_time', Date.now().toString());
        // A fresh login is fresh activity: an old idle stamp must never sign
        // this session straight back out.
        localStorage.setItem(LAST_ACTIVITY_KEY, Date.now().toString());
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
    sessionGen.current++;
    try {
      await authApi.logout();
    } catch {
      // Ignore logout errors
    } finally {
      localStorage.removeItem('ims_token');
      localStorage.removeItem('ims_refresh_token');
      localStorage.removeItem('ims_user');
      localStorage.removeItem('ims_login_time');
      localStorage.removeItem('ims_active_module');
      dropPaletteRecents();
      // Clear POS state to prevent data leaking between users
      try { usePOSStore.getState().clearAllOnLogout(state.user?.id, state.user?.activeStoreId); } catch { /* ignore if store not ready */ }
      dispatch({ type: 'LOGOUT' });
      // Every screen's cached server data goes too, so the next person on a
      // shared tablet is never shown the last person's (tasks or anything
      // else). Every sign-out path - the menu, idle, store picker, forced
      // password change - comes through here.
      queryClient.clear();
    }
  };

  // Re-fetch the signed-in user from the server (e.g. after a forced password
  // change) and merge into state + cache. The shop is the server's (the
  // token's, as on mount); only the client-side role pick is preserved.
  const refreshUser = async () => {
    const profile = await authApi.getProfile();
    const current = state.user;
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
  const setActiveStore = async (storeId: string): Promise<boolean> => {
    // The server's switch-store rule: an assigned shop, or any shop for
    // ADMIN / SUPERADMIN.
    if (!(state.user?.storeIds?.includes(storeId) || userSeesAllStores(state.user?.roles))) {
      return false;
    }

    // A bill whose press is pending is parked under ITS store before the
    // switch (its key replays only there), then the till is cleared
    // (money review 24).
    try {
      const pos = usePOSStore.getState();
      const reason = pos.parkIfPending(state.user?.id);
      if (!reason) {
        // It could not be put aside: the switch is refused, the bill stays.
        window.alert('Could not put this bill aside - it may already be paid. It stays on the till; check Orders.');
        return false;
      }
      if (reason === 'parked') pos.resetTransaction('parked');
    } catch { /* never block a store switch */ }

    // QA F9 / R41: the token's active_store_id is what store-scoped endpoints
    // act on, so the screen moves ONLY when the server has re-issued the token
    // for the new shop. Until then (and if the switch fails) screen, cache and
    // token all stay on the old shop. A newer switch makes an older answer
    // stale: it is ignored, never applied over the newer choice.
    const seq = ++switchSeq.current;
    const gen = sessionGen.current;
    try {
      const { access_token, active_store_id } = await authApi.switchStore(storeId);
      if (seq !== switchSeq.current || !access_token) return false;
      // Still the session and the person that asked? A sign-out / sign-in
      // here, or another tab signing someone else in, makes the answer
      // someone else's: it is dropped, the current token is left alone.
      const issuedTo = decodeJwtClaims(access_token)?.user_id;
      if (gen !== sessionGen.current || !issuedTo || issuedTo !== userRef.current?.id
          || issuedTo !== storedUserId()) return false;
      const confirmed = active_store_id || storeId;
      localStorage.setItem('ims_token', access_token);
      dispatch({ type: 'SET_ACTIVE_STORE', payload: confirmed });
      const userJson = localStorage.getItem('ims_user');
      if (userJson) {
        try {
          const user = JSON.parse(userJson);
          user.activeStoreId = confirmed;
          localStorage.setItem('ims_user', JSON.stringify(user));
        } catch {
          /* ignore malformed cache */
        }
      }
      return true;
    } catch {
      return false;
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
      // DESIGN_MANAGER (BVI merge): owned the Online Store image/design queue
      // (retired 2026-10-29; the role keeps the Online Store pages).
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
    let roleAllowed = false;
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
