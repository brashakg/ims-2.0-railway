// ============================================================================
// IMS 2.0 - Settings: User Management & Auth
// ============================================================================

import { useState, useEffect } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import {
  Plus, Edit2, Trash2, X,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { validatePhone } from '../../utils/validators';
import { adminUserApi, adminStoreApi } from '../../services/api';
import { MODULE_ACCESS_OPTIONS } from '../../context/ModuleContext';
import type { StoreData, UserData } from './settingsTypes';
import {
  ROLE_HIERARCHY,
  ASSIGNABLE_ROLES,
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

  // Get current user's role level
  const currentUserRoleLevel = ROLE_HIERARCHY[user?.activeRole || ''] || 0;

  // Filter users based on current user's role
  const filteredUsers = users.filter(u => {
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
            ? 'Create and manage store staff. You can assign: Optometrist, Sales Cashier, Sales Staff, Workshop Staff roles.'
            : 'Create users and assign roles. Users can have multiple roles and access to multiple stores.'}
        </p>

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
    </>
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

          {/* User-wise Module Access -- deny-only override on top of the role.
              Keys come from MODULE_ACCESS_OPTIONS (the single canonical source
              shared with the Rail nav + ProtectedRoute), so an unchecked box
              maps to exactly the module key the gate enforces. Unchecking only
              RESTRICTS -- it can never grant a module the role forbids. */}
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-2">Module Access</label>
            <p className="text-xs text-gray-500 mb-2">Uncheck to hide a module from this user (only restricts within their role; cannot grant access their role lacks)</p>
            <div className="grid grid-cols-2 tablet:grid-cols-3 gap-2">
              {MODULE_ACCESS_OPTIONS.map(({ key: moduleKey, label }) => {
                const userModules = formData.moduleAccess || {};
                const isEnabled = userModules[moduleKey] !== false;
                return (
                  <label key={moduleKey} className={clsx('flex items-center gap-2 p-2 rounded cursor-pointer', isEnabled ? 'bg-green-50 border border-green-200' : 'bg-gray-50 border border-gray-200')}>
                    <input
                      type="checkbox"
                      checked={isEnabled}
                      onChange={e => {
                        const current = formData.moduleAccess || {};
                        setFormData(prev => ({ ...prev, moduleAccess: { ...current, [moduleKey]: e.target.checked } }));
                      }}
                      className="rounded border-gray-300"
                    />
                    <span className="text-sm text-gray-600">{label}</span>
                  </label>
                );
              })}
            </div>
          </div>

          {/* User-wise Permissions -- PRESET-DRIVEN per-user override editor.
              Council ruling sec.2: the owner sees SENTENCES (from the role's
              delta toggles), never raw capability keys. Each toggle ON/OFF
              writes a grant/deny capability on the user; the discount field
              edits discount_cap. Hard-floor items are shown grayed-with-reason.
              "Reset to standard role" clears all overrides. PRESETS-ONLY-FIRST:
              this is a CLIENT-SIDE template that expands to capability keys
              written on the user -- no "preset" reference is ever persisted. */}
          <PermissionDeltaEditor formData={formData} setFormData={setFormData} />
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

// ============================================================================
// Preset-driven per-user permission delta editor (council ruling sec.2)
// ============================================================================
// Owner sees SENTENCES, never raw capability keys. The role's curated delta
// toggles + the discount field; hard-floor items grayed-with-reason; a
// "Reset to standard role" button. PRESETS-ONLY-FIRST: a preset is a CLIENT-SIDE
// template that expands to capability keys written on the user -- we NEVER
// persist a "preset" reference. The raw all-capabilities matrix is deliberately
// deferred (not built here).

interface DeltaRow {
  key: string;
  label: string;
  type: 'toggle' | 'number';
  default: boolean | number;
  hard_floor_note: string | null;
}
interface PermissionOptions {
  schema_version: number;
  discount_cap_field: string;
  role_deltas: Record<string, { defaults: string[]; commonOverrides: DeltaRow[] }>;
  grantable: string[];
}

function PermissionDeltaEditor({
  formData,
  setFormData,
}: {
  formData: Partial<UserData>;
  setFormData: Dispatch<SetStateAction<Partial<UserData>>>;
}) {
  const [options, setOptions] = useState<PermissionOptions | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let alive = true;
    adminUserApi
      .getPermissionOptions()
      .then(o => { if (alive) setOptions(o as PermissionOptions); })
      .catch(() => { if (alive) setLoadError(true); });
    return () => { alive = false; };
  }, []);

  // The delta rows for the user's HIGHEST role (the one that drives the preset).
  const roles = formData.roles || [];
  const highestRole = [...roles].sort(
    (a, b) => (ROLE_HIERARCHY[b] || 0) - (ROLE_HIERARCHY[a] || 0),
  )[0];
  const discountField = options?.discount_cap_field || '__discount_cap__';
  const rows: DeltaRow[] = (highestRole && options?.role_deltas[highestRole]?.commonOverrides) || [];

  const perms = formData.permissions || {};
  const grants = perms.grant || {};
  const denies = perms.deny || {};

  // The on/off state of a capability toggle, given its role-baseline default:
  // explicit deny -> off; explicit grant -> on; otherwise the role default.
  const toggleState = (row: DeltaRow): boolean => {
    if (denies[row.key]) return false;
    if (grants[row.key]) return true;
    return Boolean(row.default);
  };

  const setToggle = (row: DeltaRow, on: boolean) => {
    const nextGrant = { ...grants };
    const nextDeny = { ...denies };
    delete nextGrant[row.key];
    delete nextDeny[row.key];
    // Only record an OVERRIDE when it DIFFERS from the role default (so the
    // stored map stays minimal + the preset stays the source of truth).
    if (on && !row.default) nextGrant[row.key] = true;
    if (!on && row.default) nextDeny[row.key] = true;
    setFormData(prev => ({
      ...prev,
      permissions: { grant: nextGrant, deny: nextDeny },
    }));
  };

  const resetToStandard = () => {
    setFormData(prev => ({ ...prev, permissions: { grant: {}, deny: {} } }));
  };

  const isGrantable = (key: string) =>
    !options || options.grantable.includes(key);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="block text-sm font-medium text-gray-600">
          Permissions {highestRole ? `(beyond standard ${highestRole.replace(/_/g, ' ')})` : ''}
        </label>
        <button
          type="button"
          onClick={resetToStandard}
          className="text-xs text-bv-red-600 hover:underline"
        >
          Reset to standard role
        </button>
      </div>
      <p className="text-xs text-gray-500 mb-2">
        Turn extra abilities on or off for this person. Limits like discount caps,
        GST and prescription ranges are always enforced and can't be lifted here.
      </p>

      {loadError && (
        <p className="text-xs text-amber-600">
          Could not load permission options; this person will use their standard role.
        </p>
      )}

      {!loadError && rows.length === 0 && (
        <p className="text-xs text-gray-500">
          {highestRole
            ? 'No extra permission toggles for this role; they use the standard role.'
            : 'Choose a role first to customise permissions.'}
        </p>
      )}

      <div className="space-y-2">
        {rows.map(row => {
          if (row.type === 'number' || row.key === discountField) {
            return (
              <div key={row.key} className="flex items-center gap-2 p-2 bg-gray-50 border border-gray-200 rounded">
                <span className="text-sm text-gray-700 flex-1">{row.label}</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={formData.discountCap ?? (row.default as number) ?? 0}
                  onChange={e =>
                    setFormData(prev => ({ ...prev, discountCap: parseInt(e.target.value || '0', 10) }))
                  }
                  className="w-20 px-2 py-1 border border-gray-300 rounded text-sm"
                />
                <span className="text-sm text-gray-500">%</span>
              </div>
            );
          }
          const grantable = isGrantable(row.key);
          const checked = toggleState(row);
          const disabled = !grantable; // above the actor's level -> shown grayed
          return (
            <label
              key={row.key}
              className={clsx(
                'flex items-start gap-2 p-2 rounded border',
                disabled ? 'bg-gray-100 border-gray-200 opacity-70 cursor-not-allowed'
                  : checked ? 'bg-green-50 border-green-200 cursor-pointer'
                  : 'bg-gray-50 border-gray-200 cursor-pointer',
              )}
              title={disabled ? 'This permission is above your level and cannot be granted.' : undefined}
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={disabled}
                onChange={e => setToggle(row, e.target.checked)}
                className="mt-0.5 rounded border-gray-300"
              />
              <span className="text-sm text-gray-700">
                {row.label}
                {disabled && <span className="block text-xs text-gray-400">Above your level — cannot grant</span>}
                {row.hard_floor_note && (
                  <span className="block text-xs text-gray-400">{row.hard_floor_note}</span>
                )}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
