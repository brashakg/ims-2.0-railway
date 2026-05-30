// ============================================================================
// IMS 2.0 - Module Context
// Manages active module state for dynamic sidebar navigation
// ============================================================================

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
import type { UserRole } from '../types';
import { useAuth } from './AuthContext';
import {
  ShoppingCart, Eye, Package, Users, Truck, DollarSign,
  Users2, BarChart3, Settings, Wrench,
  type LucideIcon,
} from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

export type ModuleId =
  | 'dashboard'
  | 'pos'
  | 'clinic'
  | 'inventory'
  | 'customers'
  | 'vendors'
  | 'workshop'
  | 'hr'
  | 'reports'
  | 'finance'
  | 'settings';

export interface SidebarItem {
  id: string;
  label: string;
  path: string;
  icon?: LucideIcon;
  roles?: UserRole[]; // If set, only these roles see this item. If omitted, all roles with module access see it.
}

export interface ModuleConfig {
  id: ModuleId;
  title: string;
  subtitle: string;
  icon: LucideIcon;
  color: string;
  bgColor: string;
  allowedRoles: UserRole[];
  sidebarItems: SidebarItem[];
}

interface ModuleContextType {
  activeModule: ModuleId | null;
  setActiveModule: (module: ModuleId | null) => void;
  getModuleConfig: (moduleId: ModuleId) => ModuleConfig | undefined;
  getModulesForRole: (role: UserRole) => ModuleConfig[];
  /** Map a route path to the canonical module key that owns it (or null when
   *  the path belongs to no gateable module -- dashboard, settings, print,
   *  jarvis, etc., which must never be deny-able). Used by ProtectedRoute and
   *  the Rail to apply the per-user deny-only module override at the route +
   *  nav level. Matches by longest path prefix. */
  moduleForPath: (path: string) => ModuleKey | null;
  isModuleActive: boolean;
  goToDashboard: () => void;
}

// ============================================================================
// Canonical module keys -- the SINGLE source of truth shared by SettingsAuth
// (the admin checkboxes), the Rail nav, and ProtectedRoute. A per-user
// `module_access` map (deny-only override on top of the role) is keyed on
// EXACTLY these strings. They mirror the gateable MODULE_CONFIGS group ids.
//
// `settings` is deliberately NOT gateable: an admin must never be able to lock
// a user (or themselves) out of User Management, which is the only place to
// undo a bad module grant. Dashboard / print / jarvis / org are likewise
// ungated (return null from moduleForPath).
// ============================================================================

export const MODULE_KEYS = [
  'pos',
  'clinic',
  'inventory',
  'customers',
  'vendors',
  'workshop',
  'hr',
  'reports',
  'finance',
] as const;

export type ModuleKey = (typeof MODULE_KEYS)[number];

/** Admin-facing label for each gateable module key, in the order shown in the
 *  SettingsAuth "Module Access" grid. Keep keys === MODULE_KEYS so the
 *  checkboxes, the Rail, and ProtectedRoute can never drift apart. */
export const MODULE_ACCESS_OPTIONS: { key: ModuleKey; label: string }[] = [
  { key: 'pos', label: 'POS' },
  { key: 'clinic', label: 'Clinical' },
  { key: 'inventory', label: 'Inventory' },
  { key: 'customers', label: 'Customers (CRM)' },
  { key: 'vendors', label: 'Supply Chain' },
  { key: 'workshop', label: 'Workshop' },
  { key: 'hr', label: 'HR & Tasks' },
  { key: 'reports', label: 'Reports' },
  { key: 'finance', label: 'Finance' },
];

// Route-prefix -> canonical module key. Ordered longest-prefix-first so a more
// specific path wins (none currently overlap ambiguously, but the lookup is
// written to be prefix-safe). Anything not matched here is ungated (null).
//
// Notes on shared paths:
//  - /orders is surfaced by POS, Workshop, and Reports but is owned by POS, so
//    denying `pos` also removes order views (acceptable: no POS => no orders).
//  - /customers/campaigns (Marketing) sits under the `customers` module, so
//    denying `customers` also hides Marketing -- correct, it's a CRM feature.
//  - /catalog* is part of the Inventory module (Add Product / pricing live
//    there in MODULE_CONFIGS).
const PATH_MODULE_PREFIXES: { prefix: string; key: ModuleKey }[] = [
  { prefix: '/pos', key: 'pos' },
  { prefix: '/orders', key: 'pos' },
  { prefix: '/returns', key: 'pos' },
  { prefix: '/walkouts', key: 'pos' },
  { prefix: '/clinical', key: 'clinic' },
  { prefix: '/prescriptions', key: 'clinic' },
  { prefix: '/inventory', key: 'inventory' },
  { prefix: '/catalog', key: 'inventory' },
  { prefix: '/customers', key: 'customers' },
  { prefix: '/purchase', key: 'vendors' },
  { prefix: '/workshop', key: 'workshop' },
  { prefix: '/hr', key: 'hr' },
  { prefix: '/tasks', key: 'hr' },
  { prefix: '/incentive', key: 'hr' },
  { prefix: '/reports', key: 'reports' },
  { prefix: '/finance', key: 'finance' },
];

/** Resolve the canonical module key that owns `path`, or null if ungated.
 *  Strips the query string and matches by the longest route prefix so e.g.
 *  `/inventory/audit?tab=x` -> `inventory`. */
export function moduleForPath(path: string): ModuleKey | null {
  if (!path) return null;
  const clean = path.split('?')[0].split('#')[0];
  let best: { prefix: string; key: ModuleKey } | null = null;
  for (const entry of PATH_MODULE_PREFIXES) {
    if (clean === entry.prefix || clean.startsWith(entry.prefix + '/')) {
      if (!best || entry.prefix.length > best.prefix.length) best = entry;
    }
  }
  return best ? best.key : null;
}

// ============================================================================
// Module Configurations
// ============================================================================

export const MODULE_CONFIGS: ModuleConfig[] = [
  {
    id: 'pos',
    title: 'Point of Sale',
    subtitle: 'Orders, Deliveries, Workshop',
    icon: ShoppingCart,
    color: 'text-red-600',
    bgColor: 'bg-red-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF', 'OPTOMETRIST'],
    sidebarItems: [
      { id: 'pos-new', label: 'New Sale', path: '/pos' },
      { id: 'pos-orders', label: 'All Orders', path: '/orders' },
      { id: 'pos-returns', label: 'Returns & Exchanges', path: '/returns', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER'] },
      { id: 'pos-pending', label: 'Pending Orders', path: '/orders?status=PROCESSING' },
      { id: 'pos-deliveries', label: 'Ready for Delivery', path: '/orders?status=READY', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER'] },
      { id: 'pos-dayend', label: 'Day-End Report', path: '/reports/day-end', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER'] },
      // Footfall Tracking — hidden until the feature is built (route is a "Coming soon" stub). Re-enable when /pos/footfall ships.
      // { id: 'pos-footfall', label: 'Footfall Tracking', path: '/pos/footfall' },
    ],
  },
  {
    id: 'clinic',
    title: 'Eye Clinic',
    subtitle: 'Testing, Prescription, Contact Lens',
    icon: Eye,
    color: 'text-purple-600',
    bgColor: 'bg-purple-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST'],
    sidebarItems: [
      { id: 'clinic-queue', label: 'Patient Queue', path: '/clinical' },
      { id: 'clinic-history', label: 'Test History', path: '/clinical/history' },
      { id: 'clinic-prescriptions', label: 'Prescriptions', path: '/prescriptions' },
      { id: 'clinic-family-rx', label: 'Family Rx', path: '/clinical/family-rx' },
    ],
  },
  {
    id: 'inventory',
    title: 'Inventory & Stock',
    subtitle: 'Stock, Transfers, Barcode',
    icon: Package,
    color: 'text-green-600',
    bgColor: 'bg-green-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'],
    sidebarItems: [
      { id: 'inv-overview', label: 'Stock Overview', path: '/inventory' },
      { id: 'inv-catalog', label: 'Add Product', path: '/catalog/add', roles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
      { id: 'inv-lowstock', label: 'Low Stock Alerts', path: '/inventory?tab=low-stock' },
      { id: 'inv-reorders', label: 'Reorder Dashboard', path: '/inventory?tab=reorders', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'inv-transfers', label: 'Stock Transfers', path: '/inventory?tab=transfers', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'inv-movements', label: 'Stock Movements', path: '/inventory?tab=movements' },
      { id: 'inv-audit', label: 'Stock Audit', path: '/inventory/audit', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
    ],
  },
  {
    id: 'customers',
    title: 'Customers (CRM)',
    subtitle: 'Customer Management, Loyalty',
    icon: Users,
    color: 'text-orange-600',
    bgColor: 'bg-orange-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF', 'OPTOMETRIST'],
    sidebarItems: [
      { id: 'crm-all', label: 'All Customers', path: '/customers' },
      { id: 'crm-search', label: 'Search Customers', path: '/customers?search=true' },
      { id: 'crm-360', label: 'Customer 360', path: '/customers/360', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      { id: 'crm-segmentation', label: 'Segmentation (RFM)', path: '/customers/segmentation', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'crm-loyalty', label: 'Loyalty Program', path: '/customers/loyalty', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      { id: 'crm-campaigns', label: 'Campaign Manager', path: '/customers/campaigns', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      { id: 'crm-referrals', label: 'Referral Tracker', path: '/customers/referrals', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      { id: 'crm-feedback', label: 'Feedback & NPS', path: '/customers/feedback', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      { id: 'crm-recalls', label: 'Recalls & Reminders', path: '/customers?tab=recalls' },
      { id: 'crm-follow-ups', label: 'Follow-ups', path: '/customers/follow-ups' },
      { id: 'crm-loyalty-tiers', label: 'Loyalty Tiers', path: '/customers/loyalty?tab=tiers', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      { id: 'crm-cl-subscriptions', label: 'CL Subscriptions', path: '/customers?tab=cl-subscriptions', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
    ],
  },
  {
    id: 'workshop',
    title: 'Workshop',
    subtitle: 'Lens Fitting & Job Orders',
    icon: Wrench,
    color: 'text-amber-600',
    bgColor: 'bg-amber-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'],
    sidebarItems: [
      { id: 'ws-jobs', label: 'All Jobs', path: '/workshop' },
      { id: 'ws-orders', label: 'Order Pipeline', path: '/orders?status=PROCESSING' },
    ],
  },
  {
    id: 'vendors',
    title: 'Supply Chain & Procurement',
    subtitle: 'Purchase Orders, Vendors, GRN, Replenishment',
    icon: Truck,
    color: 'text-cyan-600',
    bgColor: 'bg-cyan-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    sidebarItems: [
      { id: 'supply-po', label: 'Purchase Orders', path: '/purchase/orders' },
      { id: 'supply-vendor', label: 'Vendor Management', path: '/purchase/vendors' },
      { id: 'supply-grn', label: 'Goods Receipt Notes', path: '/purchase/grn' },
      { id: 'supply-replenish', label: 'Stock Replenishment', path: '/inventory/replenishment' },
      { id: 'supply-audit', label: 'Stock Audit', path: '/inventory/audit' },
    ],
  },
  {
    id: 'hr',
    title: 'HR & Employees',
    subtitle: 'Attendance, Leaves, Tasks, Payroll, Incentives',
    icon: Users2,
    color: 'text-indigo-600',
    bgColor: 'bg-indigo-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    sidebarItems: [
      { id: 'hr-attendance', label: 'Attendance', path: '/hr' },
      { id: 'hr-leaves', label: 'Leave Management', path: '/hr?tab=leave' },
      { id: 'hr-payroll', label: 'Payroll & Salary', path: '/hr/payroll', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'ACCOUNTANT'] },
      { id: 'hr-incentives', label: 'Incentive Tracking', path: '/hr/incentives', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'hr-tasks', label: 'Tasks Dashboard', path: '/tasks/dashboard' },
      { id: 'hr-checklists', label: 'Daily Checklists', path: '/tasks/checklists' },
      { id: 'hr-task-mgmt', label: 'Task Management', path: '/tasks', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'hr-leaderboard', label: 'Staff Leaderboard', path: '/hr?tab=leaderboard', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'hr-eye-camps', label: 'Eye Camps', path: '/hr?tab=eye-camps', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
    ],
  },
  {
    id: 'reports',
    title: 'Reports & Analytics',
    subtitle: 'Sales, Stock, GST Reports',
    icon: BarChart3,
    color: 'text-blue-600',
    bgColor: 'bg-blue-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    sidebarItems: [
      { id: 'rpt-dashboard', label: 'Analytics Dashboard', path: '/reports' },
      { id: 'rpt-dayend', label: 'Day-End Closing', path: '/reports/day-end' },
      { id: 'rpt-outstanding', label: 'Outstanding Payments', path: '/reports/outstanding' },
      { id: 'rpt-sales', label: 'Sales Reports', path: '/reports?tab=sales' },
      { id: 'rpt-inventory', label: 'Inventory Reports', path: '/reports?tab=inventory' },
      { id: 'rpt-gst', label: 'GST Reports', path: '/reports?tab=gst' },
      { id: 'rpt-forecast', label: 'Demand Forecast', path: '/reports?tab=forecast' },
      { id: 'rpt-orders', label: 'Orders Overview', path: '/orders' },
      { id: 'rpt-discount', label: 'Discount Analysis', path: '/reports?tab=discount-analysis', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'rpt-deadstock', label: 'Dead Stock', path: '/reports?tab=dead-stock', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'rpt-demand', label: 'Demand Forecast', path: '/reports?tab=demand', roles: ['SUPERADMIN'] },
      { id: 'rpt-churn', label: 'Churn Prediction', path: '/reports?tab=churn', roles: ['SUPERADMIN'] },
      { id: 'rpt-anomaly', label: 'Anomaly Detection', path: '/reports?tab=anomaly', roles: ['SUPERADMIN'] },
      { id: 'rpt-vendor-margins', label: 'Vendor Margins', path: '/reports?tab=vendor-margins', roles: ['SUPERADMIN'] },
    ],
  },
  {
    id: 'finance',
    title: 'Finance & Expenses',
    subtitle: 'Expense Tracking, Approvals',
    icon: DollarSign,
    color: 'text-emerald-600',
    bgColor: 'bg-emerald-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    sidebarItems: [
      { id: 'fin-dashboard', label: 'Finance Dashboard', path: '/finance/dashboard' },
      { id: 'fin-budgeting', label: 'Budgeting (Planned vs Actual)', path: '/finance/budgeting', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'fin-expenses', label: 'Expense Tracker', path: '/finance/expenses' },
      { id: 'fin-pending', label: 'Pending Approval', path: '/finance/expenses?tab=pending-approval' },
      { id: 'fin-summary', label: 'Category Summary', path: '/finance/expenses?tab=summary' },
    ],
  },
  {
    id: 'settings',
    title: 'Settings & Admin',
    subtitle: 'Company, Branches, Users',
    icon: Settings,
    color: 'text-gray-600',
    bgColor: 'bg-gray-100',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'ACCOUNTANT'],
    sidebarItems: [
      { id: 'set-setup', label: 'Store & Employee Setup', path: '/setup', roles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'set-profile', label: 'My Profile', path: '/settings?tab=profile' },
      { id: 'set-business', label: 'Business Profile', path: '/settings?tab=business', roles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'set-stores', label: 'Store Management', path: '/settings?tab=stores', roles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'set-users', label: 'User Management', path: '/settings?tab=users', roles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      { id: 'set-categories', label: 'Categories & Brands', path: '/settings?tab=categories', roles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
      { id: 'set-discounts', label: 'Discount Rules', path: '/settings?tab=discounts', roles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'] },
      { id: 'set-tax', label: 'Tax & Invoice', path: '/settings?tab=tax-invoice', roles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      { id: 'set-integrations', label: 'Integrations', path: '/settings?tab=integrations', roles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'set-approvals', label: 'Approval Workflows', path: '/settings?tab=approvals', roles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'set-audit', label: 'Audit Log', path: '/settings?tab=audit-logs', roles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'set-system', label: 'System Settings', path: '/settings?tab=system', roles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'set-jarvis', label: 'AI Intelligence', path: '/jarvis', roles: ['SUPERADMIN', 'ADMIN'] },
    ],
  },
];

// ============================================================================
// Context
// ============================================================================

const ModuleContext = createContext<ModuleContextType | undefined>(undefined);

// ============================================================================
// Provider
// ============================================================================

export function ModuleProvider({ children }: { children: ReactNode }) {
  const [activeModule, setActiveModuleState] = useState<ModuleId | null>(null);
  // Per-user deny-only module gate. AuthProvider wraps ModuleProvider (App.tsx),
  // so this is always available here.
  const { hasModuleAccess } = useAuth();

  const setActiveModule = useCallback((module: ModuleId | null) => {
    setActiveModuleState(module);
  }, []);

  const getModuleConfig = useCallback((moduleId: ModuleId): ModuleConfig | undefined => {
    return MODULE_CONFIGS.find(m => m.id === moduleId);
  }, []);

  // Visible module groups = role-allowed AND not denied for this user. The role
  // filter runs FIRST and is the ceiling; hasModuleAccess can only further
  // remove a group (deny-only), never add one the role lacks -- so there's no
  // privilege-escalation path. A MODULE_CONFIGS id that isn't a gateable
  // MODULE_KEY (e.g. `settings`) is never denied (hasModuleAccess returns true).
  const getModulesForRole = useCallback((role: UserRole): ModuleConfig[] => {
    return MODULE_CONFIGS.filter(m =>
      (m.allowedRoles.includes(role) || role === 'SUPERADMIN') &&
      hasModuleAccess(m.id)
    );
  }, [hasModuleAccess]);

  const goToDashboard = useCallback(() => {
    setActiveModuleState(null);
  }, []);

  const value: ModuleContextType = {
    activeModule,
    setActiveModule,
    getModuleConfig,
    getModulesForRole,
    moduleForPath,
    isModuleActive: activeModule !== null,
    goToDashboard,
  };

  return (
    <ModuleContext.Provider value={value}>
      {children}
    </ModuleContext.Provider>
  );
}

// ============================================================================
// Hook
// ============================================================================

export function useModule() {
  const context = useContext(ModuleContext);
  if (!context) {
    throw new Error('useModule must be used within a ModuleProvider');
  }
  return context;
}

export default ModuleContext;
