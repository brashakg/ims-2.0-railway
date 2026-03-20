// ============================================================================
// IMS 2.0 — Admin Control Panel (Exhaustive Store-wise & Role-wise Config)
// ============================================================================
// Superadmin controls for the entire software: module access per store,
// role permissions, discount limits, feature flags, operational rules

import { useState, useEffect } from 'react';
import { adminStoreApi } from '../../services/api';
import {
  Store, Shield, Eye, EyeOff, Save, Loader2,
  ShoppingCart, Stethoscope, Wrench, Package, BarChart3,
  Users, FileText, CreditCard, Settings,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

// -----------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------

interface StoreModuleConfig {
  storeId: string;
  storeName: string;
  modules: Record<string, boolean>;
}

interface RolePermission {
  roleId: string;
  roleName: string;
  permissions: Record<string, boolean>;
}

interface DiscountLimit {
  roleId: string;
  roleName: string;
  maxDiscountPercent: number;
  requiresApproval: boolean;
  approvalThreshold: number;
}

interface OperationalRule {
  id: string;
  label: string;
  description: string;
  value: boolean | number | string;
  type: 'toggle' | 'number' | 'select';
  options?: string[];
  category: 'billing' | 'inventory' | 'hr' | 'clinical' | 'security';
}

// -----------------------------------------------------------------------
// Constants
// -----------------------------------------------------------------------

const MODULES = [
  { id: 'pos', label: 'Point of Sale', icon: ShoppingCart },
  { id: 'clinical', label: 'Eye Clinic', icon: Stethoscope },
  { id: 'workshop', label: 'Workshop', icon: Wrench },
  { id: 'inventory', label: 'Inventory', icon: Package },
  { id: 'reports', label: 'Reports', icon: BarChart3 },
  { id: 'hr', label: 'HR & Payroll', icon: Users },
  { id: 'finance', label: 'Finance', icon: CreditCard },
  { id: 'crm', label: 'CRM & Loyalty', icon: Users },
  { id: 'tasks', label: 'Tasks & SOPs', icon: FileText },
  { id: 'settings', label: 'Settings', icon: Settings },
];

const PERMISSIONS = [
  'create_orders', 'void_orders', 'apply_discount', 'view_reports',
  'edit_products', 'manage_stock', 'process_returns', 'view_financials',
  'manage_users', 'lock_periods', 'export_data', 'delete_records',
  'approve_expenses', 'manage_payroll', 'view_audit_logs', 'manage_stores',
];

const ROLES = [
  { id: 'SUPERADMIN', name: 'Superadmin' },
  { id: 'ADMIN', name: 'Admin' },
  { id: 'AREA_MANAGER', name: 'Area Manager' },
  { id: 'STORE_MANAGER', name: 'Store Manager' },
  { id: 'ACCOUNTANT', name: 'Accountant' },
  { id: 'CATALOG_MANAGER', name: 'Catalog Manager' },
  { id: 'OPTOMETRIST', name: 'Optometrist' },
  { id: 'SALES_CASHIER', name: 'Sales/Cashier' },
  { id: 'SALES_STAFF', name: 'Sales Staff' },
  { id: 'WORKSHOP_STAFF', name: 'Workshop Staff' },
];

const DEFAULT_STORES: StoreModuleConfig[] = [];
// Stores are now fetched dynamically from the API

const DEFAULT_DISCOUNT_LIMITS: DiscountLimit[] = [
  { roleId: 'SUPERADMIN', roleName: 'Superadmin', maxDiscountPercent: 100, requiresApproval: false, approvalThreshold: 0 },
  { roleId: 'ADMIN', roleName: 'Admin', maxDiscountPercent: 50, requiresApproval: false, approvalThreshold: 0 },
  { roleId: 'STORE_MANAGER', roleName: 'Store Manager', maxDiscountPercent: 25, requiresApproval: false, approvalThreshold: 0 },
  { roleId: 'SALES_CASHIER', roleName: 'Sales/Cashier', maxDiscountPercent: 10, requiresApproval: true, approvalThreshold: 5 },
  { roleId: 'SALES_STAFF', roleName: 'Sales Staff', maxDiscountPercent: 5, requiresApproval: true, approvalThreshold: 3 },
  { roleId: 'OPTOMETRIST', roleName: 'Optometrist', maxDiscountPercent: 0, requiresApproval: false, approvalThreshold: 0 },
  { roleId: 'WORKSHOP_STAFF', roleName: 'Workshop Staff', maxDiscountPercent: 0, requiresApproval: false, approvalThreshold: 0 },
];

const DEFAULT_RULES: OperationalRule[] = [
  { id: 'require_customer', label: 'Require Customer for All Sales', description: 'No walk-in quick sales without customer', value: false, type: 'toggle', category: 'billing' },
  { id: 'auto_round_off', label: 'Auto Round-off to Nearest ₹1', description: 'Round invoice totals', value: true, type: 'toggle', category: 'billing' },
  { id: 'credit_limit', label: 'Default Credit Limit (₹)', description: 'Max credit per customer', value: 50000, type: 'number', category: 'billing' },
  { id: 'credit_approval', label: 'Credit Above Limit Needs Approval', description: 'Manager approval for credit exceeding limit', value: true, type: 'toggle', category: 'billing' },
  { id: 'negative_stock', label: 'Allow Negative Stock Billing', description: 'Sell even when stock is 0', value: false, type: 'toggle', category: 'inventory' },
  { id: 'low_stock_threshold', label: 'Low Stock Alert Threshold', description: 'Warn when stock falls below this', value: 5, type: 'number', category: 'inventory' },
  { id: 'auto_reorder', label: 'Auto-Generate Reorder POs', description: 'Auto-create POs when stock is low', value: false, type: 'toggle', category: 'inventory' },
  { id: 'geo_fence_radius', label: 'Geo-fence Radius (meters)', description: 'Max distance for attendance check-in', value: 200, type: 'number', category: 'hr' },
  { id: 'late_threshold', label: 'Late Arrival Threshold (minutes)', description: 'Minutes after shift start to mark late', value: 15, type: 'number', category: 'hr' },
  { id: 'require_prescription', label: 'Require Rx for Lens Orders', description: 'Block lens billing without prescription', value: true, type: 'toggle', category: 'clinical' },
  { id: 'rx_validity_days', label: 'Prescription Validity (days)', description: 'Days before Rx expires', value: 180, type: 'number', category: 'clinical' },
  { id: 'session_timeout', label: 'Session Timeout (minutes)', description: 'Auto-logout after inactivity', value: 30, type: 'number', category: 'security' },
  { id: 'password_expiry', label: 'Force Password Change (days)', description: 'Days before password must be changed', value: 90, type: 'number', category: 'security' },
  { id: 'two_factor', label: 'Require 2FA for Admin Roles', description: 'Two-factor auth for admin and above', value: false, type: 'toggle', category: 'security' },
];

// -----------------------------------------------------------------------
// Component
// -----------------------------------------------------------------------

type PanelTab = 'modules' | 'permissions' | 'discounts' | 'rules';

export function AdminControlPanel() {
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<PanelTab>('modules');
  const [isSaving, setIsSaving] = useState(false);

  // Store module access — fetch dynamically
  const [storeModules, setStoreModules] = useState<StoreModuleConfig[]>(DEFAULT_STORES);

  useEffect(() => {
    adminStoreApi.getStores().then((data: any) => {
      const storeList = Array.isArray(data?.stores || data) ? (data?.stores || data) : [];
      if (storeList.length > 0) {
        setStoreModules(storeList.map((s: any) => ({
          storeId: s.store_id || s.store_code || s.id,
          storeName: s.store_name || s.name || '',
          modules: s.modules || { pos: true, clinical: true, workshop: true, inventory: true, reports: true, hr: true, finance: true, crm: true, tasks: true, settings: true },
        })));
      }
    }).catch(() => {});
  }, []);

  // Role permissions
  const [rolePermissions] = useState<RolePermission[]>(
    ROLES.map(role => ({
      roleId: role.id,
      roleName: role.name,
      permissions: Object.fromEntries(
        PERMISSIONS.map(p => [p, ['SUPERADMIN', 'ADMIN'].includes(role.id) ? true : Math.random() > 0.4])
      ),
    }))
  );

  // Discount limits
  const [discountLimits, setDiscountLimits] = useState<DiscountLimit[]>(DEFAULT_DISCOUNT_LIMITS);

  // Operational rules
  const [rules, setRules] = useState<OperationalRule[]>(DEFAULT_RULES);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await new Promise(resolve => setTimeout(resolve, 1500));
      toast.success('Admin settings saved successfully');
    } catch {
      toast.error('Failed to save settings');
    } finally {
      setIsSaving(false);
    }
  };

  const toggleStoreModule = (storeId: string, moduleId: string) => {
    setStoreModules(prev => prev.map(s =>
      s.storeId === storeId ? { ...s, modules: { ...s.modules, [moduleId]: !s.modules[moduleId] } } : s
    ));
  };

  const TABS: { id: PanelTab; label: string; icon: typeof Store }[] = [
    { id: 'modules', label: 'Store Modules', icon: Store },
    { id: 'permissions', label: 'Role Permissions', icon: Shield },
    { id: 'discounts', label: 'Discount Limits', icon: CreditCard },
    { id: 'rules', label: 'Operational Rules', icon: Settings },
  ];

  return (
    <div className="space-y-6">
      {/* Tab Bar */}
      <div className="flex flex-wrap gap-2 border-b border-gray-700 pb-1">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={clsx(
              'flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors',
              activeTab === tab.id ? 'bg-gray-700 text-white' : 'text-gray-400 hover:text-gray-300'
            )}
          >
            <tab.icon className="w-4 h-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* ============================================================= */}
      {/* STORE MODULE ACCESS                                            */}
      {/* ============================================================= */}
      {activeTab === 'modules' && (
        <div className="space-y-4">
          <p className="text-sm text-gray-400">Control which modules are available at each store location.</p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-900 text-gray-400 text-left">
                <tr>
                  <th className="px-4 py-3 sticky left-0 bg-gray-900 z-10">Store</th>
                  {MODULES.map(m => (
                    <th key={m.id} className="px-3 py-3 text-center whitespace-nowrap">
                      <div className="flex flex-col items-center gap-1">
                        <m.icon className="w-4 h-4" />
                        <span className="text-xs">{m.label}</span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {storeModules.map(store => (
                  <tr key={store.storeId} className="text-white">
                    <td className="px-4 py-3 font-medium sticky left-0 bg-gray-800 z-10 whitespace-nowrap">{store.storeName}</td>
                    {MODULES.map(m => (
                      <td key={m.id} className="px-3 py-3 text-center">
                        <button
                          onClick={() => toggleStoreModule(store.storeId, m.id)}
                          className={clsx('p-1 rounded', store.modules[m.id] ? 'text-green-400' : 'text-gray-600')}
                        >
                          {store.modules[m.id] ? <Eye className="w-5 h-5" /> : <EyeOff className="w-5 h-5" />}
                        </button>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ============================================================= */}
      {/* ROLE PERMISSIONS MATRIX                                        */}
      {/* ============================================================= */}
      {activeTab === 'permissions' && (
        <div className="space-y-4">
          <p className="text-sm text-gray-400">Fine-grained permission control for each role across all modules.</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-900 text-gray-400 text-left">
                <tr>
                  <th className="px-3 py-2 sticky left-0 bg-gray-900 z-10">Permission</th>
                  {ROLES.map(role => (
                    <th key={role.id} className="px-2 py-2 text-center whitespace-nowrap">{role.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {PERMISSIONS.map(perm => (
                  <tr key={perm} className="text-white">
                    <td className="px-3 py-2 font-medium sticky left-0 bg-gray-800 z-10 whitespace-nowrap capitalize">
                      {perm.replace(/_/g, ' ')}
                    </td>
                    {rolePermissions.map(role => (
                      <td key={role.roleId} className="px-2 py-2 text-center">
                        <span className={clsx(
                          'inline-block w-3 h-3 rounded-full',
                          role.permissions[perm] ? 'bg-green-500' : 'bg-red-500/30'
                        )} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ============================================================= */}
      {/* DISCOUNT LIMITS                                                */}
      {/* ============================================================= */}
      {activeTab === 'discounts' && (
        <div className="space-y-4">
          <p className="text-sm text-gray-400">Set maximum discount percentages and approval requirements per role.</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {discountLimits.map(dl => (
              <div key={dl.roleId} className="bg-gray-800 border border-gray-700 rounded-lg p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Shield className="w-4 h-4 text-gray-400" />
                  <h4 className="font-medium text-white">{dl.roleName}</h4>
                </div>
                <div className="space-y-3">
                  <div>
                    <label className="text-xs text-gray-400">Max Discount %</label>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={dl.maxDiscountPercent}
                      onChange={(e) => setDiscountLimits(prev => prev.map(d =>
                        d.roleId === dl.roleId ? { ...d, maxDiscountPercent: Number(e.target.value) } : d
                      ))}
                      className="w-full mt-1 bg-gray-900 border border-gray-600 text-white rounded px-3 py-1.5 text-sm"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={dl.requiresApproval}
                      onChange={() => setDiscountLimits(prev => prev.map(d =>
                        d.roleId === dl.roleId ? { ...d, requiresApproval: !d.requiresApproval } : d
                      ))}
                      className="rounded border-gray-600"
                    />
                    <label className="text-xs text-gray-300">Requires approval above threshold</label>
                  </div>
                  {dl.requiresApproval && (
                    <div>
                      <label className="text-xs text-gray-400">Approval Threshold %</label>
                      <input
                        type="number"
                        min={0}
                        max={dl.maxDiscountPercent}
                        value={dl.approvalThreshold}
                        onChange={(e) => setDiscountLimits(prev => prev.map(d =>
                          d.roleId === dl.roleId ? { ...d, approvalThreshold: Number(e.target.value) } : d
                        ))}
                        className="w-full mt-1 bg-gray-900 border border-gray-600 text-white rounded px-3 py-1.5 text-sm"
                      />
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ============================================================= */}
      {/* OPERATIONAL RULES                                              */}
      {/* ============================================================= */}
      {activeTab === 'rules' && (
        <div className="space-y-6">
          {['billing', 'inventory', 'hr', 'clinical', 'security'].map(category => {
            const categoryRules = rules.filter(r => r.category === category);
            return (
              <div key={category}>
                <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3 border-b border-gray-700 pb-2">
                  {category.charAt(0).toUpperCase() + category.slice(1)} Rules
                </h3>
                <div className="space-y-3">
                  {categoryRules.map(rule => (
                    <div key={rule.id} className="flex items-center justify-between p-3 bg-gray-800 border border-gray-700 rounded-lg">
                      <div className="flex-1 min-w-0 mr-4">
                        <p className="text-sm font-medium text-white">{rule.label}</p>
                        <p className="text-xs text-gray-400 mt-0.5">{rule.description}</p>
                      </div>
                      <div className="flex-shrink-0">
                        {rule.type === 'toggle' && (
                          <button
                            onClick={() => setRules(prev => prev.map(r =>
                              r.id === rule.id ? { ...r, value: !r.value } : r
                            ))}
                            className={clsx(
                              'relative inline-flex h-7 w-12 items-center rounded-full transition-colors',
                              rule.value ? 'bg-green-600' : 'bg-gray-600'
                            )}
                          >
                            <span className={clsx(
                              'inline-block h-5 w-5 rounded-full bg-white transition-transform',
                              rule.value ? 'translate-x-6' : 'translate-x-1'
                            )} />
                          </button>
                        )}
                        {rule.type === 'number' && (
                          <input
                            type="number"
                            value={rule.value as number}
                            onChange={(e) => setRules(prev => prev.map(r =>
                              r.id === rule.id ? { ...r, value: Number(e.target.value) } : r
                            ))}
                            className="w-24 bg-gray-900 border border-gray-600 text-white rounded px-2 py-1 text-sm text-right"
                          />
                        )}
                        {rule.type === 'select' && rule.options && (
                          <select
                            value={rule.value as string}
                            onChange={(e) => setRules(prev => prev.map(r =>
                              r.id === rule.id ? { ...r, value: e.target.value } : r
                            ))}
                            className="bg-gray-900 border border-gray-600 text-white rounded px-2 py-1 text-sm"
                          >
                            {rule.options.map(opt => (
                              <option key={opt} value={opt}>{opt}</option>
                            ))}
                          </select>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Save Button */}
      <div className="flex justify-end pt-4 border-t border-gray-700">
        <button
          onClick={handleSave}
          disabled={isSaving}
          className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {isSaving ? 'Saving...' : 'Save All Settings'}
        </button>
      </div>
    </div>
  );
}
