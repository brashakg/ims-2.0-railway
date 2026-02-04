// ============================================================================
// IMS 2.0 - Module Context
// Manages active module state for dynamic sidebar navigation
// ============================================================================

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
import type { UserRole } from '../types';
import {
  ShoppingCart, Eye, Package, Users, Truck, FileText,
  Users2, BarChart3, Gift, Settings,
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
  | 'finance'
  | 'hr'
  | 'reports'
  | 'marketing'
  | 'settings';

export interface SidebarItem {
  id: string;
  label: string;
  path: string;
  icon?: LucideIcon;
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
  isModuleActive: boolean;
  goToDashboard: () => void;
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
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'SALES_CASHIER', 'SALES_STAFF', 'OPTOMETRIST'],
    sidebarItems: [
      { id: 'pos-new', label: 'New Sale', path: '/pos' },
      { id: 'pos-orders', label: 'All Orders', path: '/orders' },
      { id: 'pos-pending', label: 'Pending Orders', path: '/orders?status=pending' },
      { id: 'pos-deliveries', label: 'Ready for Delivery', path: '/orders?status=ready' },
      { id: 'pos-workshop', label: 'Workshop Jobs', path: '/workshop' },
      { id: 'pos-returns', label: 'Returns & Exchange', path: '/orders/returns' },
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
      { id: 'clinic-test', label: 'New Eye Test', path: '/clinical/test' },
      { id: 'clinic-history', label: 'Test History', path: '/clinical/history' },
      { id: 'clinic-prescriptions', label: 'Prescriptions', path: '/prescriptions' },
      { id: 'clinic-contactlens', label: 'Contact Lens Fitting', path: '/clinical/contact-lens' },
    ],
  },
  {
    id: 'inventory',
    title: 'Inventory & Stock',
    subtitle: 'Stock, Categories, Barcode',
    icon: Package,
    color: 'text-green-600',
    bgColor: 'bg-green-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'],
    sidebarItems: [
      { id: 'inv-overview', label: 'Stock Overview', path: '/inventory' },
      { id: 'inv-add', label: 'Add/Remove Stock', path: '/inventory/stock' },
      { id: 'inv-categories', label: 'Categories & Brands', path: '/inventory/categories' },
      { id: 'inv-barcode', label: 'Barcode Management', path: '/inventory/barcode' },
      { id: 'inv-catalog', label: 'Catalog Management', path: '/inventory/catalog' },
      { id: 'inv-product', label: 'Add Product (Wizard)', path: '/inventory/product/new' },
    ],
  },
  {
    id: 'customers',
    title: 'Customers (CRM)',
    subtitle: 'Customer Management, Loyalty',
    icon: Users,
    color: 'text-orange-600',
    bgColor: 'bg-orange-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'SALES_CASHIER', 'SALES_STAFF', 'OPTOMETRIST'],
    sidebarItems: [
      { id: 'crm-all', label: 'All Customers', path: '/customers' },
      { id: 'crm-new', label: 'Add Customer', path: '/customers/new' },
      { id: 'crm-search', label: 'Search', path: '/customers?search=true' },
      { id: 'crm-loyalty', label: 'Loyalty Program', path: '/customers/loyalty' },
      { id: 'crm-followup', label: 'Follow-ups', path: '/customers/followup' },
      { id: 'crm-birthdays', label: 'Birthdays & Reminders', path: '/customers/reminders' },
    ],
  },
  {
    id: 'vendors',
    title: 'Vendors & Purchase',
    subtitle: 'Vendors, Purchase Orders',
    icon: Truck,
    color: 'text-cyan-600',
    bgColor: 'bg-cyan-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    sidebarItems: [
      { id: 'vendor-list', label: 'All Vendors', path: '/vendors' },
      { id: 'vendor-new', label: 'Add Vendor', path: '/vendors/new' },
      { id: 'vendor-po', label: 'Purchase Orders', path: '/vendors/purchase-orders' },
      { id: 'vendor-po-new', label: 'New Purchase Order', path: '/vendors/purchase-orders/new' },
      { id: 'vendor-grn', label: 'Goods Receipt', path: '/vendors/grn' },
    ],
  },
  {
    id: 'finance',
    title: 'Finance & Accounts',
    subtitle: 'Day Book, Expenses, GST',
    icon: FileText,
    color: 'text-emerald-600',
    bgColor: 'bg-emerald-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'],
    sidebarItems: [
      { id: 'fin-daybook', label: 'Day Book', path: '/finance/daybook' },
      { id: 'fin-expenses', label: 'Expenses', path: '/finance/expenses' },
      { id: 'fin-advances', label: 'Advances', path: '/finance/advances' },
      { id: 'fin-payments', label: 'Payment Collections', path: '/finance/payments' },
      { id: 'fin-gst', label: 'GST Returns', path: '/finance/gst' },
      { id: 'fin-ledger', label: 'Ledger', path: '/finance/ledger' },
    ],
  },
  {
    id: 'hr',
    title: 'HR & Employees',
    subtitle: 'Attendance, Salary, Performance',
    icon: Users2,
    color: 'text-indigo-600',
    bgColor: 'bg-indigo-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'],
    sidebarItems: [
      { id: 'hr-employees', label: 'All Employees', path: '/hr/employees' },
      { id: 'hr-attendance', label: 'Attendance', path: '/hr/attendance' },
      { id: 'hr-leaves', label: 'Leave Management', path: '/hr/leaves' },
      { id: 'hr-salary', label: 'Salary & Payroll', path: '/hr/salary' },
      { id: 'hr-tasks', label: 'Tasks & Assignments', path: '/tasks' },
      { id: 'hr-performance', label: 'Performance', path: '/hr/performance' },
    ],
  },
  {
    id: 'reports',
    title: 'Reports & Analytics',
    subtitle: 'Sales, Stock, Financial Reports',
    icon: BarChart3,
    color: 'text-blue-600',
    bgColor: 'bg-blue-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    sidebarItems: [
      { id: 'rpt-dashboard', label: 'Analytics Dashboard', path: '/reports' },
      { id: 'rpt-sales', label: 'Sales Reports', path: '/reports/sales' },
      { id: 'rpt-inventory', label: 'Inventory Reports', path: '/reports/inventory' },
      { id: 'rpt-financial', label: 'Financial Reports', path: '/reports/financial' },
      { id: 'rpt-employee', label: 'Employee Reports', path: '/reports/employee' },
      { id: 'rpt-custom', label: 'Custom Reports', path: '/reports/custom' },
    ],
  },
  {
    id: 'marketing',
    title: 'Marketing & Offers',
    subtitle: 'Promotions, Vouchers, SMS',
    icon: Gift,
    color: 'text-pink-600',
    bgColor: 'bg-pink-50',
    allowedRoles: ['SUPERADMIN', 'ADMIN'],
    sidebarItems: [
      { id: 'mkt-promos', label: 'Active Promotions', path: '/marketing/promotions' },
      { id: 'mkt-new', label: 'Create Promotion', path: '/marketing/promotions/new' },
      { id: 'mkt-vouchers', label: 'Vouchers & Coupons', path: '/marketing/vouchers' },
      { id: 'mkt-sms', label: 'SMS Campaigns', path: '/marketing/sms' },
      { id: 'mkt-whatsapp', label: 'WhatsApp Broadcasts', path: '/marketing/whatsapp' },
    ],
  },
  {
    id: 'settings',
    title: 'Settings & Admin',
    subtitle: 'Company, Branches, Users',
    icon: Settings,
    color: 'text-gray-600',
    bgColor: 'bg-gray-100',
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'],
    sidebarItems: [
      { id: 'set-profile', label: 'My Profile', path: '/settings?tab=profile' },
      { id: 'set-business', label: 'Business Profile', path: '/settings?tab=business' },
      { id: 'set-stores', label: 'Store Management', path: '/settings?tab=stores' },
      { id: 'set-users', label: 'User Management', path: '/settings?tab=users' },
      { id: 'set-categories', label: 'Categories & Brands', path: '/settings?tab=categories' },
      { id: 'set-integrations', label: 'Integrations', path: '/settings?tab=integrations' },
      { id: 'set-system', label: 'System Settings', path: '/settings?tab=system' },
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

  const setActiveModule = useCallback((module: ModuleId | null) => {
    setActiveModuleState(module);
  }, []);

  const getModuleConfig = useCallback((moduleId: ModuleId): ModuleConfig | undefined => {
    return MODULE_CONFIGS.find(m => m.id === moduleId);
  }, []);

  const getModulesForRole = useCallback((role: UserRole): ModuleConfig[] => {
    return MODULE_CONFIGS.filter(m =>
      m.allowedRoles.includes(role) || role === 'SUPERADMIN'
    );
  }, []);

  const goToDashboard = useCallback(() => {
    setActiveModuleState(null);
  }, []);

  const value: ModuleContextType = {
    activeModule,
    setActiveModule,
    getModuleConfig,
    getModulesForRole,
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
