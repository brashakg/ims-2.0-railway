// ============================================================================
// IMS 2.0 - Settings: User Management & Auth
// ============================================================================

import { useState, useEffect } from 'react';
import {
  Plus, Edit2, Trash2, X,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { adminUserApi, adminStoreApi } from '../../services/api';
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
  roles: u.roles || u.role ? [u.role] : [],
  accessibleStores: u.accessible_stores || u.accessibleStores || u.store_ids || [],
  discountCap: u.discount_cap || u.discountCap || 10,
  isActive: u.is_active !== false,
  createdAt: u.created_at || u.createdAt || '',
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
    try {
      setIsLoading(true);
      const apiData = {
        name: userData.fullName || '',
        email: userData.email || '',
        phone: userData.phone || '',
        role: userData.roles?.[0] || 'SALES_STAFF',
        storeId: userData.accessibleStores?.[0] || '',
        password: password,
        status: userData.isActive ? 'ACTIVE' : 'INACTIVE',
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
    return <div className="flex items-center justify-center h-48"><div className="text-gray-400">Loading...</div></div>;
  }

  return (
    <>
      <div className="card">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-semibold text-white">User Management</h2>
            {user?.activeRole === 'STORE_MANAGER' && (
              <p className="text-xs text-gray-400 mt-1">Showing users from your managed stores</p>
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

        <p className="text-sm text-gray-400 mb-4">
          {user?.activeRole === 'STORE_MANAGER'
            ? 'Create and manage store staff. You can assign: Optometrist, Sales Cashier, Sales Staff, Workshop Staff roles.'
            : 'Create users and assign roles. Users can have multiple roles and access to multiple stores.'}
        </p>

        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-900 border-b border-gray-700">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">User</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Roles</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase">Stores</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Discount Cap</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Status</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {filteredUsers.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-gray-400">
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
                    <tr key={u.id} className="hover:bg-gray-900">
                      <td className="px-4 py-3">
                        <p className="font-medium text-white">{u.fullName}</p>
                        <p className="text-xs text-gray-400">{u.email}</p>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          {u.roles.map(role => (
                            <span key={role} className={clsx(
                              'text-xs px-2 py-0.5 rounded',
                              role === 'SUPERADMIN' ? 'bg-purple-100 text-purple-700' :
                              role === 'ADMIN' ? 'bg-red-100 text-red-700' :
                              role === 'AREA_MANAGER' ? 'bg-orange-100 text-orange-700' :
                              role === 'STORE_MANAGER' ? 'bg-blue-100 text-blue-700' :
                              'bg-gray-700 text-gray-300'
                            )}>
                              {role.replace('_', ' ')}
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
                                <span key={sId} className="text-xs bg-blue-900/30 text-blue-400 px-2 py-0.5 rounded" title={storeObj?.storeName || sId}>
                                  {storeObj?.storeCode || sId}
                                </span>
                              );
                            })}
                            {u.accessibleStores.length > 3 && (
                              <span className="text-xs text-gray-400">+{u.accessibleStores.length - 3} more</span>
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
                              className="text-gray-400 hover:text-bv-red-600"
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
                              className="text-gray-400 hover:text-red-600"
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
        <div className="mt-6 pt-6 border-t border-gray-700">
          <h3 className="text-sm font-medium text-gray-300 mb-3">
            {user?.activeRole === 'STORE_MANAGER' ? 'Assignable Roles' : 'Available Roles'}
          </h3>
          <div className="flex flex-wrap gap-2">
            {(ASSIGNABLE_ROLES[user?.activeRole || ''] || []).map(role => (
              <span key={role} className="text-xs bg-gray-700 px-3 py-1 rounded">
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
      <div className="bg-gray-800 rounded-xl w-full max-w-xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <h2 className="text-lg font-semibold text-white">
            {user ? 'Edit User' : 'Add New User'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-gray-700 rounded-lg">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Username *</label>
              <input
                type="text"
                value={formData.username || ''}
                onChange={e => setFormData(prev => ({ ...prev, username: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-700 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Full Name *</label>
              <input
                type="text"
                value={formData.fullName || ''}
                onChange={e => setFormData(prev => ({ ...prev, fullName: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-700 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Email *</label>
              <input
                type="email"
                value={formData.email || ''}
                onChange={e => setFormData(prev => ({ ...prev, email: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-700 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Phone</label>
              <input
                type="tel"
                value={formData.phone || ''}
                onChange={e => setFormData(prev => ({ ...prev, phone: e.target.value }))}
                className="w-full px-3 py-2 border border-gray-700 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          </div>

          {!user && (
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Password *</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full px-3 py-2 border border-gray-700 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Roles *</label>
            {allowedRoles.length === 0 ? (
              <p className="text-sm text-gray-400">You don't have permission to assign roles.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {allowedRoles.map(role => (
                  <label key={role} className="flex items-center gap-2 p-2 bg-gray-900 rounded cursor-pointer hover:bg-gray-700">
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
              <p className="text-xs text-gray-400 mt-2">As Store Manager, you can only assign store-level roles.</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Accessible Stores</label>
            {allowedStores.length === 0 ? (
              <p className="text-sm text-gray-400">No stores available.</p>
            ) : (
              <div className="grid grid-cols-2 gap-2">
                {allowedStores.map(store => (
                  <label key={store.id} className="flex items-center gap-2 p-2 bg-gray-900 rounded cursor-pointer hover:bg-gray-700">
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
              <p className="text-xs text-gray-400 mt-2">You can only assign users to your managed stores.</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Discount Cap (%)</label>
              <input
                type="number"
                value={formData.discountCap || 10}
                onChange={e => setFormData(prev => ({ ...prev, discountCap: parseInt(e.target.value) }))}
                min="0"
                max="100"
                className="w-full px-3 py-2 border border-gray-700 rounded-lg focus:border-bv-red-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Status</label>
              <select
                value={formData.isActive ? 'active' : 'inactive'}
                onChange={e => setFormData(prev => ({ ...prev, isActive: e.target.value === 'active' }))}
                className="w-full px-3 py-2 bg-gray-900 border border-gray-700 text-white rounded-lg focus:border-bv-red-500 focus:outline-none"
              >
                <option value="active">Active</option>
                <option value="inactive">Inactive (Suspended)</option>
              </select>
            </div>
          </div>

          {/* User-wise Module Access */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Module Access</label>
            <p className="text-xs text-gray-500 mb-2">Control which modules this user can access (overrides role defaults)</p>
            <div className="grid grid-cols-3 gap-2">
              {['POS', 'Clinical', 'Workshop', 'Inventory', 'Reports', 'HR', 'Finance', 'CRM', 'Tasks'].map(mod => {
                const moduleKey = mod.toLowerCase();
                const userModules = (formData as any).moduleAccess || {};
                const isEnabled = userModules[moduleKey] !== false;
                return (
                  <label key={mod} className={clsx('flex items-center gap-2 p-2 rounded cursor-pointer', isEnabled ? 'bg-green-900/30' : 'bg-gray-900')}>
                    <input
                      type="checkbox"
                      checked={isEnabled}
                      onChange={e => {
                        const current = (formData as any).moduleAccess || {};
                        setFormData(prev => ({ ...prev, moduleAccess: { ...current, [moduleKey]: e.target.checked } } as any));
                      }}
                      className="rounded border-gray-600"
                    />
                    <span className="text-sm text-gray-300">{mod}</span>
                  </label>
                );
              })}
            </div>
          </div>

          {/* User-wise Permissions */}
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Individual Permissions</label>
            <div className="grid grid-cols-2 gap-2">
              {[
                { key: 'can_void_orders', label: 'Void/Cancel Orders' },
                { key: 'can_process_returns', label: 'Process Returns' },
                { key: 'can_export_data', label: 'Export Data' },
                { key: 'can_view_financials', label: 'View Financial Reports' },
                { key: 'can_approve_expenses', label: 'Approve Expenses' },
                { key: 'can_manage_stock', label: 'Manage Stock Levels' },
              ].map(perm => {
                const userPerms = (formData as any).permissions || {};
                const isGranted = userPerms[perm.key] !== false;
                return (
                  <label key={perm.key} className="flex items-center gap-2 p-2 bg-gray-900 rounded cursor-pointer hover:bg-gray-700">
                    <input
                      type="checkbox"
                      checked={isGranted}
                      onChange={e => {
                        const current = (formData as any).permissions || {};
                        setFormData(prev => ({ ...prev, permissions: { ...current, [perm.key]: e.target.checked } } as any));
                      }}
                      className="rounded border-gray-600"
                    />
                    <span className="text-sm text-gray-300">{perm.label}</span>
                  </label>
                );
              })}
            </div>
          </div>
        </div>

        <div className="p-4 border-t border-gray-700 flex justify-end gap-3">
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
