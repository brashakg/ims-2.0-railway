// ============================================================================
// IMS 2.0 - Settings: User Management & Auth
// ============================================================================

import { useState, useEffect, useMemo } from 'react';
import {
  Plus, Edit2, Trash2, X, Search, ShieldCheck, Building2, Mail, Phone, BadgeCheck,
  KeyRound, Copy, Check,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { validatePhone } from '../../utils/validators';
import { adminUserApi, adminStoreApi } from '../../services/api';
import { PermissionDeltaEditor } from '../../components/permissions/PermissionDeltaEditor';
import { UserPermissionsPanel } from '../../components/permissions/UserPermissionsPanel';
import type { StoreData, UserData } from './settingsTypes';
import {
  ROLE_HIERARCHY,
  ASSIGNABLE_ROLES,
  AVAILABLE_ROLES,
  getHighestRoleLevel,
  CATEGORY_DEFINITIONS,
} from './settingsTypes';

// ============================================================================
// Transform helpers
// ============================================================================

const transformStore = (s: any): StoreData => ({
  id: s.id || s.store_id || s._id,
  storeCode: s.store_code || s.storeCode || '',
  storeName: s.store_name || s.storeName || s.name || '',
  brand: s.brand || 'BETTER_VISION',
  gstin: s.gstin || s.GSTIN || '',
  address: s.address || '',
  city: s.city || '',
  state: s.state || '',
  pincode: s.pincode || s.postal_code || '',
  phone: s.phone || s.contact_phone || '',
  email: s.email || s.contact_email || '',
  openingTime: s.opening_time || s.openingTime || '10:00',
  closingTime: s.closing_time || s.closingTime || '20:00',
  geoLat: s.geo_lat || s.latitude,
  geoLng: s.geo_lng || s.longitude,
  geoFenceRadius: s.geo_fence_radius || s.geoFenceRadius || 100,
  enabledCategories: s.enabled_categories || s.enabledCategories || CATEGORY_DEFINITIONS.map(c => c.code),
  isActive: s.is_active !== false,
});

const transformUser = (u: any): UserData => ({
  id: u.id || u.user_id || u._id,
  username: u.username || u.user_name || '',
  email: u.email || '',
  fullName: u.full_name || u.fullName || u.name || '',
  phone: u.phone || u.contact_phone || '',
  // Precedence fix: was `u.roles || u.role ? [u.role] : []`, which parses as
  // `(u.roles || u.role) ? [u.role] : []` -> for the array the API actually
  // returns (`roles`, no singular `role`), this yielded `[undefined]`, and the
  // badge render below then crashed on `undefined.replace(...)`, blanking the
  // whole app. Use the real roles array when present.
  roles: u.roles ?? (u.role ? [u.role] : []),
  accessibleStores: u.accessible_stores || u.accessibleStores || u.store_ids || [],
  discountCap: u.discount_cap || u.discountCap || 10,
  isActive: u.is_active !== false,
  createdAt: u.created_at || u.createdAt || '',
  // Load existing deny-only module override so editing a user preserves it
  // (snake_case from the API; camelCase tolerated). Default {} = role defaults.
  moduleAccess: u.module_access || u.moduleAccess || {},
  // Two-sided capability override (council ruling sec.2). Default {} -> DARK.
  permissions: u.permissions || {},
});

// ============================================================================
// User Management Section
// ============================================================================

export function UserManagementSection() {
  const { user } = useAuth();
  const toast = useToast();

  const [isLoading, setIsLoading] = useState(false);
  const [users, setUsers] = useState<UserData[]>([]);
  const [stores, setStores] = useState<StoreData[]>([]);
  const [showAddUserModal, setShowAddUserModal] = useState(false);
  const [editingUser, setEditingUser] = useState<UserData | null>(null);
  // Per-user permissions panel (slide-over) -- backlog #14 discoverability.
  const [permUser, setPermUser] = useState<UserData | null>(null);
  // One-time temp-password display after a reset. Holds the user + the temp the
  // server returned exactly ONCE; cleared (and forgotten) when the modal closes.
  const [resetResult, setResetResult] = useState<{ user: UserData; temp: string } | null>(null);
  const [resettingId, setResettingId] = useState<string | null>(null);
  // Search + filters (backlog #14).
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [storeFilter, setStoreFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [usersResponse, storesResponse] = await Promise.all([
        adminUserApi.getUsers().catch(() => ({ users: [] })),
        adminStoreApi.getStores().catch(() => ({ stores: [] })),
      ]);

      if (usersResponse?.users) {
        setUsers(usersResponse.users.map(transformUser));
      } else if (Array.isArray(usersResponse)) {
        setUsers(usersResponse.map(transformUser));
      }

      if (storesResponse?.stores) {
        setStores(storesResponse.stores.map(transformStore));
      } else if (Array.isArray(storesResponse)) {
        setStores(storesResponse.map(transformStore));
      }
    } catch {
      setUsers([]);
      setStores([]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSaveUser = async (userData: Partial<UserData>, password?: string) => {
    const phoneErr = validatePhone(userData.phone);
    if (phoneErr) { toast.error(phoneErr); return; }
    try {
      setIsLoading(true);
      const apiData = {
        name: userData.fullName || '',
        email: userData.email || '',
        phone: userData.phone || '',
        // Pass the FULL multi-selects (all roles + all accessible stores) +
        // discount cap the modal collects -- not just the first of each -- so
        // nothing is dropped on save.
        roles: userData.roles && userData.roles.length ? userData.roles : ['SALES_STAFF'],
        storeIds: userData.accessibleStores || [],
        primaryStoreId: userData.accessibleStores?.[0],
        discountCap: userData.discountCap,
        username: (userData as { username?: string }).username,
        password: password,
        status: userData.isActive ? 'ACTIVE' : 'INACTIVE',
        // Deny-only per-user module override. Previously collected by the modal
        // checkboxes but DROPPED here -- now forwarded so it actually persists
        // (adminUserApi maps it to the backend `module_access` field).
        moduleAccess: userData.moduleAccess,
        // Two-sided capability override (council ruling sec.2). Only sent when
        // the editor produced one; the backend escalation-guards + audits it.
        permissions: userData.permissions,
      };

      if (editingUser?.id) {
        await adminUserApi.updateUser(editingUser.id, apiData);
      } else {
        await adminUserApi.createUser(apiData);
      }
      toast.success(editingUser ? 'User updated successfully' : 'User created successfully');
      setShowAddUserModal(false);
      setEditingUser(null);
      loadData();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save user');
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteUser = async (userId: string) => {
    if (!window.confirm('Are you sure you want to delete this user?')) return;
    try {
      await adminUserApi.deleteUser(userId);
      toast.success('User deleted successfully');
      loadData();
    } catch {
      toast.error('Failed to delete user');
    }
  };

  // Reset a user to a server-generated TEMPORARY password. The server returns
  // the temp exactly ONCE; we surface it in a copyable modal and never refetch
  // it (real passwords are one-way bcrypt hashes -- there is no "view password").
  const handleResetPassword = async (targetUser: UserData) => {
    const name = targetUser.fullName || targetUser.username;
    if (!window.confirm(
      `Reset ${name}'s password? They'll get a temporary password and must change it at next login.`
    )) return;
    try {
      setResettingId(targetUser.id);
      const res = await adminUserApi.resetPassword(targetUser.id);
      if (res?.temporary_password) {
        setResetResult({ user: targetUser, temp: res.temporary_password });
      } else {
        // No temp in the response (e.g. a legacy supplied-password path) -- still
        // a success; just confirm the force-change is set.
        toast.success(`Password reset for ${name}. They must change it at next login.`);
      }
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 403) {
        toast.error("You can't reset the password of a user with a higher role than yours.");
      } else {
        toast.error(err instanceof Error ? err.message : 'Failed to reset password');
      }
    } finally {
      setResettingId(null);
    }
  };

  // Get current user's role level
  const currentUserRoleLevel = ROLE_HIERARCHY[user?.activeRole || ''] || 0;

  // Filter users based on current user's role (RBAC visibility ceiling)
  const visibleUsers = users.filter(u => {
    if (user?.activeRole === 'SUPERADMIN') return true;
    if (user?.activeRole === 'ADMIN') {
      return !u.roles.includes('SUPERADMIN');
    }
    if (user?.activeRole === 'STORE_MANAGER') {
      const userStores = user?.storeIds || [];
      const hasCommonStore = u.accessibleStores?.some(s => userStores.includes(s));
      const userRoleLevel = getHighestRoleLevel(u.roles);
      return hasCommonStore && userRoleLevel < currentUserRoleLevel;
    }
    return false;
  });

  // Apply the search box + role/store/status filters (backlog #14) on top of the
  // RBAC-visible set, so the on-screen filters never widen who an actor can see.
  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return visibleUsers.filter(u => {
      if (q) {
        const hay = `${u.fullName} ${u.username} ${u.email} ${u.phone}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (roleFilter && !u.roles.includes(roleFilter)) return false;
      if (storeFilter && !(u.accessibleStores || []).includes(storeFilter)) return false;
      if (statusFilter === 'active' && !u.isActive) return false;
      if (statusFilter === 'inactive' && u.isActive) return false;
      return true;
    });
  }, [visibleUsers, search, roleFilter, storeFilter, statusFilter]);

  const canManageUser = (targetUser: UserData) => {
    const targetRoleLevel = getHighestRoleLevel(targetUser.roles);
    return currentUserRoleLevel > targetRoleLevel;
  };

  if (isLoading) {
    return <div className="flex items-center justify-center h-48"><div className="text-gray-500">Loading...</div></div>;
  }

  return (
    <>
      <div className="card">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">User Management</h2>
            {user?.activeRole === 'STORE_MANAGER' && (
              <p className="text-xs text-gray-500 mt-1">Showing users from your managed stores</p>
            )}
          </div>
          <button
            onClick={() => setShowAddUserModal(true)}
            className="btn-primary flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Add User
          </button>
        </div>

        <p className="text-sm text-gray-500 mb-4">
          {user?.activeRole === 'STORE_MANAGER'
            ? 'Create and manage store staff. You can assign: Optometrist, Sales Staff, Workshop Staff roles.'
            : 'Create users and assign roles. Users can have multiple roles and access to multiple stores.'}
        </p>

        {/* ---- Search + filters (backlog #14) ----------------------------- */}
        <div className="flex flex-col tablet:flex-row tablet:items-center gap-3 mb-4">
          <div className="relative flex-1 min-w-[180px]">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search name, username, email or phone"
              className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:border-bv-red-500 focus:outline-none"
            />
          </div>
          <select
            value={roleFilter}
            onChange={e => setRoleFilter(e.target.value)}
            className="px-3 py-2 bg-white border border-gray-300 text-gray-900 rounded-lg text-sm focus:border-bv-red-500 focus:outline-none"
          >
            <option value="">All roles</option>
            {AVAILABLE_ROLES.map(r => (
              <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>
            ))}
          </select>
          <select
            value={storeFilter}
            onChange={e => setStoreFilter(e.target.value)}
            className="px-3 py-2 bg-white border border-gray-300 text-gray-900 rounded-lg text-sm focus:border-bv-red-500 focus:outline-none"
          >
            <option value="">All stores</option>
            {stores.map(s => (
              <option key={s.id} value={s.id}>{s.storeCode || s.storeName}</option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value as 'all' | 'active' | 'inactive')}
            className="px-3 py-2 bg-white border border-gray-300 text-gray-900 rounded-lg text-sm focus:border-bv-red-500 focus:outline-none"
          >
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </div>

        <div className="text-xs text-gray-500 mb-2">
          Showing {filteredUsers.length} of {visibleUsers.length} users
        </div>

        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Roles</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Stores</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Discount Cap</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {filteredUsers.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-gray-500">
                    {user?.activeRole === 'STORE_MANAGER'
                      ? 'No staff members found. Click "Add User" to create store staff.'
                      : 'No users found. Click "Add User" to create one.'}
                  </td>
                </tr>
              ) : (
                filteredUsers.map(u => {
                  const canEdit = canManageUser(u);
                  const canDelete = canManageUser(u) && u.id !== user?.id;

                  return (
                    <tr key={u.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <p className="font-medium text-gray-900">{u.fullName}</p>
                        <p className="text-xs text-gray-500">{u.email}</p>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          {(u.roles || []).filter(Boolean).map(role => (
                            <span key={role} className={clsx(
                              'text-xs px-2 py-0.5 rounded',
                              role === 'SUPERADMIN' ? 'bg-purple-100 text-purple-700' :
                              role === 'ADMIN' ? 'bg-red-100 text-red-700' :
                              role === 'AREA_MANAGER' ? 'bg-orange-100 text-orange-700' :
                              role === 'STORE_MANAGER' ? 'bg-blue-100 text-blue-700' :
                              'bg-gray-100 text-gray-600'
                            )}>
                              {String(role).replace(/_/g, ' ')}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        {u.accessibleStores && u.accessibleStores.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {u.accessibleStores.slice(0, 3).map(sId => {
                              const storeObj = stores.find(s => s.id === sId);
                              return (
                                <span key={sId} className="text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded" title={storeObj?.storeName || sId}>
                                  {storeObj?.storeCode || sId}
                                </span>
                              );
                            })}
                            {u.accessibleStores.length > 3 && (
                              <span className="text-xs text-gray-500">+{u.accessibleStores.length - 3} more</span>
                            )}
                          </div>
                        ) : (
                          <span className="text-gray-500 text-xs">No stores</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className="font-medium">{u.discountCap}%</span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        {u.isActive ? (
                          <span className="badge-success">Active</span>
                        ) : (
                          <span className="badge-error">Inactive</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <div className="flex items-center justify-center gap-2">
                          {canEdit ? (
                            <button
                              onClick={() => {
                                setEditingUser(u);
                                setShowAddUserModal(true);
                              }}
                              className="text-gray-500 hover:text-bv-red-600"
                              title="Edit user"
                            >
                              <Edit2 className="w-4 h-4" />
                            </button>
                          ) : (
                            <span className="text-gray-200" title="Cannot edit higher-level users">
                              <Edit2 className="w-4 h-4" />
                            </span>
                          )}
                          {canEdit ? (
                            <button
                              onClick={() => setPermUser(u)}
                              className="text-gray-500 hover:text-bv-red-600"
                              title="Customize permissions"
                            >
                              <ShieldCheck className="w-4 h-4" />
                            </button>
                          ) : (
                            <span className="text-gray-200" title="Cannot manage permissions for higher-level users">
                              <ShieldCheck className="w-4 h-4" />
                            </span>
                          )}
                          {canEdit ? (
                            <button
                              onClick={() => handleResetPassword(u)}
                              disabled={resettingId === u.id}
                              className="text-gray-500 hover:text-bv-red-600 disabled:opacity-40"
                              title="Reset password (issues a temporary password)"
                            >
                              <KeyRound className="w-4 h-4" />
                            </button>
                          ) : (
                            <span className="text-gray-200" title="Cannot reset password for higher-level users">
                              <KeyRound className="w-4 h-4" />
                            </span>
                          )}
                          {canDelete ? (
                            <button
                              onClick={() => handleDeleteUser(u.id)}
                              className="text-gray-500 hover:text-red-600"
                              title="Delete user"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          ) : (
                            <span className="text-gray-200" title="Cannot delete this user">
                              <Trash2 className="w-4 h-4" />
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Available Roles Reference */}
        <div className="mt-6 pt-6 border-t border-gray-200">
          <h3 className="text-sm font-medium text-gray-600 mb-3">
            {user?.activeRole === 'STORE_MANAGER' ? 'Assignable Roles' : 'Available Roles'}
          </h3>
          <div className="flex flex-wrap gap-2">
            {(ASSIGNABLE_ROLES[user?.activeRole || ''] || []).map(role => (
              <span key={role} className="text-xs bg-gray-100 px-3 py-1 rounded">
                {role.replace('_', ' ')}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* User Modal */}
      {showAddUserModal && (
        <UserModal
          user={editingUser}
          stores={stores}
          onClose={() => {
            setShowAddUserModal(false);
            setEditingUser(null);
          }}
          onSave={handleSaveUser}
          currentUserRole={user?.activeRole || ''}
          currentUserStores={user?.storeIds || []}
        />
      )}

      {/* Per-user Permissions slide-over (backlog #14) */}
      {permUser && (
        <UserPermissionsSlideOver
          user={permUser}
          stores={stores}
          onClose={() => setPermUser(null)}
        />
      )}

      {/* One-time temporary-password display after a reset */}
      {resetResult && (
        <TempPasswordModal
          userName={resetResult.user.fullName || resetResult.user.username}
          tempPassword={resetResult.temp}
          onClose={() => setResetResult(null)}
        />
      )}
    </>
  );
}

// ============================================================================
// One-time temporary-password modal (shown once after a reset)
// ============================================================================
// Displays the server-generated temp in a monospace box with a copy button and
// an explicit "won't be shown again" warning. Closing forgets the value -- the
// only way to get another temp is to reset again (real passwords are one-way
// bcrypt hashes; there is no "view password").

function TempPasswordModal({
  userName,
  tempPassword,
  onClose,
}: {
  userName: string;
  tempPassword: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(tempPassword);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Could not copy automatically -- select the text and copy manually.');
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white border border-gray-200 rounded-xl w-full max-w-md overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <KeyRound className="w-5 h-5 text-bv-red-600" />
            <h2 className="text-lg font-semibold text-gray-900">Temporary password</h2>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-sm text-gray-600">
            A temporary password for <span className="font-medium text-gray-900">{userName}</span> has
            been set. They will be required to change it at their next login.
          </p>

          <div className="flex items-stretch gap-2">
            <div
              className="flex-1 font-mono text-base tracking-wide bg-gray-50 border border-gray-300 rounded-lg px-3 py-2.5 text-gray-900 break-all select-all"
              data-testid="temp-password-value"
            >
              {tempPassword}
            </div>
            <button
              onClick={handleCopy}
              className="btn-primary flex items-center gap-1.5 px-3"
              title="Copy to clipboard"
            >
              {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>

          <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5">
            <p className="text-sm text-amber-800 font-medium">
              Copy and share this now -- it won't be shown again.
            </p>
          </div>
        </div>

        <div className="p-4 border-t border-gray-200 flex justify-end">
          <button onClick={onClose} className="btn-primary">Done</button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Per-user Permissions slide-over (detail view + editor + history + revert)
// ============================================================================
// Surfaces the existing /users/{id}/permissions editor for an individual user,
// with a header summary card (roles, stores, status). Uses the shared
// UserPermissionsPanel so all 4 locked dimensions + the audit timeline + revert
// are in one discoverable place.

function UserPermissionsSlideOver({
  user,
  stores,
  onClose,
}: {
  user: UserData;
  stores: StoreData[];
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-white w-full max-w-2xl h-full shadow-xl flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-bv-red-600" />
            <h2 className="text-lg font-semibold text-gray-900">Permissions</h2>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {/* User summary card */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
            <p className="font-medium text-gray-900">{user.fullName}</p>
            <div className="mt-2 grid grid-cols-1 tablet:grid-cols-2 gap-y-1 gap-x-4 text-sm text-gray-600">
              {user.email && (
                <span className="flex items-center gap-1.5"><Mail className="w-3.5 h-3.5 text-gray-400" />{user.email}</span>
              )}
              {user.phone && (
                <span className="flex items-center gap-1.5"><Phone className="w-3.5 h-3.5 text-gray-400" />{user.phone}</span>
              )}
              <span className="flex items-center gap-1.5">
                <BadgeCheck className="w-3.5 h-3.5 text-gray-400" />
                {(user.roles || []).map(r => r.replace(/_/g, ' ')).join(', ') || 'No role'}
              </span>
              <span className="flex items-center gap-1.5">
                <Building2 className="w-3.5 h-3.5 text-gray-400" />
                {(user.accessibleStores || [])
                  .map(sId => stores.find(s => s.id === sId)?.storeCode || sId)
                  .join(', ') || 'No stores'}
              </span>
            </div>
            <div className="mt-2">
              {user.isActive
                ? <span className="badge-success">Active</span>
                : <span className="badge-error">Inactive</span>}
            </div>
          </div>

          <UserPermissionsPanel userId={user.id} roles={user.roles} userName={user.fullName} />
        </div>

        <div className="p-4 border-t border-gray-200 flex justify-end">
          <button onClick={onClose} className="btn-outline">Close</button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// User Modal
// ============================================================================

function UserModal({
  user,
  stores,
  onClose,
  onSave,
  currentUserRole,
  currentUserStores,
}: {
  user: UserData | null;
  stores: StoreData[];
  onClose: () => void;
  onSave: (data: Partial<UserData>, password?: string) => void;
  currentUserRole: string;
  currentUserStores: string[];
}) {
  const [formData, setFormData] = useState<Partial<UserData>>(
    user || {
      username: '',
      email: '',
      fullName: '',
      phone: '',
      roles: [],
      accessibleStores: [],
      discountCap: 10,
      isActive: true,
    }
  );
  const [password, setPassword] = useState('');

  const allowedRoles = ASSIGNABLE_ROLES[currentUserRole] || [];
  const allowedStores = currentUserRole === 'STORE_MANAGER'
    ? stores.filter(s => currentUserStores.includes(s.id))
    : stores;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white border border-gray-200 rounded-xl w-full max-w-xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">
            {user ? 'Edit User' : 'Add New User'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Username *</label>
              <input
                type="text"
                value={formData.username || ''}
                onChange={e => setFormData(prev => ({ ...prev, username: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Full Name *</label>
              <input
                type="text"
                value={formData.fullName || ''}
                onChange={e => setFormData(prev => ({ ...prev, fullName: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Email *</label>
              <input
                type="email"
                value={formData.email || ''}
                onChange={e => setFormData(prev => ({ ...prev, email: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Phone</label>
              <input
                type="tel"
                value={formData.phone || ''}
                onChange={e => setFormData(prev => ({ ...prev, phone: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          {!user && (
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Password *</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-600 mb-2">Roles *</label>
            {allowedRoles.length === 0 ? (
              <p className="text-sm text-gray-500">You don't have permission to assign roles.</p>
            ) : (
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-2">
                {allowedRoles.map(role => (
                  <label key={role} className="flex items-center gap-2 p-2 bg-gray-50 border border-gray-200 rounded cursor-pointer hover:bg-gray-100">
                    <input
                      type="checkbox"
                      checked={formData.roles?.includes(role) || false}
                      onChange={e => {
                        const current = formData.roles || [];
                        if (e.target.checked) {
                          setFormData(prev => ({ ...prev, roles: [...current, role] }));
                        } else {
                          setFormData(prev => ({ ...prev, roles: current.filter(r => r !== role) }));
                        }
                      }}
                      className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-500"
                    />
                    <span className="text-sm">{role.replace('_', ' ')}</span>
                  </label>
                ))}
              </div>
            )}
            {currentUserRole === 'STORE_MANAGER' && (
              <p className="text-xs text-gray-500 mt-2">As Store Manager, you can only assign store-level roles.</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-600 mb-2">Accessible Stores</label>
            {allowedStores.length === 0 ? (
              <p className="text-sm text-gray-500">No stores available.</p>
            ) : (
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-2">
                {allowedStores.map(store => (
                  <label key={store.id} className="flex items-center gap-2 p-2 bg-gray-50 border border-gray-200 rounded cursor-pointer hover:bg-gray-100">
                    <input
                      type="checkbox"
                      checked={formData.accessibleStores?.includes(store.id) || false}
                      onChange={e => {
                        const current = formData.accessibleStores || [];
                        if (e.target.checked) {
                          setFormData(prev => ({ ...prev, accessibleStores: [...current, store.id] }));
                        } else {
                          setFormData(prev => ({ ...prev, accessibleStores: current.filter(s => s !== store.id) }));
                        }
                      }}
                      className="rounded border-gray-300 text-bv-red-600 focus:ring-bv-red-500"
                    />
                    <span className="text-sm">{store.storeName}</span>
                  </label>
                ))}
              </div>
            )}
            {currentUserRole === 'STORE_MANAGER' && (
              <p className="text-xs text-gray-500 mt-2">You can only assign users to your managed stores.</p>
            )}
          </div>

          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Discount Cap (%)</label>
              <input
                type="number"
                value={formData.discountCap || 10}
                onChange={e => setFormData(prev => ({ ...prev, discountCap: parseInt(e.target.value) }))}
                min="0"
                max="100"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Status</label>
              <select
                value={formData.isActive ? 'active' : 'inactive'}
                onChange={e => setFormData(prev => ({ ...prev, isActive: e.target.value === 'active' }))}
                className="w-full px-3 py-2 bg-white border border-gray-300 text-gray-900 rounded-lg focus:border-bv-red-500 focus:outline-none"
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive (Suspended)</option>
              </select>
            </div>
          </div>

          {/* User-wise Permissions + Module Access -- the SHARED preset-driven
              per-user override editor (council ruling sec.2). Covers all 4 locked
              dimensions: discount-cap override, module/screen access (deny-only),
              the returns/refund-approval capability toggle, and per-role extra
              abilities. Owner sees SENTENCES, never raw capability keys; items
              above the actor's level are shown grayed-with-reason. The SAME
              component is reused on the onboarding wizard step 4. */}
          <PermissionDeltaEditor
            roles={formData.roles || []}
            permissions={formData.permissions || {}}
            onPermissionsChange={(next) => setFormData(prev => ({ ...prev, permissions: next }))}
            discountCap={formData.discountCap}
            onDiscountCapChange={(next) => setFormData(prev => ({ ...prev, discountCap: next }))}
            moduleAccess={formData.moduleAccess || {}}
            onModuleAccessChange={(next) => setFormData(prev => ({ ...prev, moduleAccess: next }))}
          />
        </div>

        <div className="p-4 border-t border-gray-200 flex justify-end gap-3">
          <button onClick={onClose} className="btn-outline">
            Cancel
          </button>
          <button onClick={() => onSave(formData, password)} className="btn-primary">
            {user ? 'Update User' : 'Create User'}
          </button>
        </div>
      </div>
    </div>
  );
}
