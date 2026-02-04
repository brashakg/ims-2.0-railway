// ============================================================================
// IMS 2.0 - Main Application Layout
// ============================================================================

import { useState } from 'react';
import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import {
  LayoutDashboard,
  ShoppingCart,
  Package,
  Users,
  FileText,
  Wrench,
  Calendar,
  Settings,
  LogOut,
  Menu,
  X,
  ChevronDown,
  Store,
  Eye,
  ClipboardList,
} from 'lucide-react';
import clsx from 'clsx';
import type { UserRole } from '../../types';

// Navigation items configuration
interface NavItem {
  label: string;
  path: string;
  icon: React.ComponentType<{ className?: string }>;
  allowedRoles?: UserRole[];
}

const navigationItems: NavItem[] = [
  {
    label: 'Dashboard',
    path: '/dashboard',
    icon: LayoutDashboard,
  },
  {
    label: 'POS',
    path: '/pos',
    icon: ShoppingCart,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF'],
  },
  {
    label: 'Customers',
    path: '/customers',
    icon: Users,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF'],
  },
  {
    label: 'Inventory',
    path: '/inventory',
    icon: Package,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'],
  },
  {
    label: 'Orders',
    path: '/orders',
    icon: FileText,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_CASHIER'],
  },
  {
    label: 'Eye Tests',
    path: '/clinical',
    icon: Eye,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST'],
  },
  {
    label: 'Workshop',
    path: '/workshop',
    icon: Wrench,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'],
  },
  {
    label: 'Tasks',
    path: '/tasks',
    icon: ClipboardList,
  },
  {
    label: 'HR',
    path: '/hr',
    icon: Calendar,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
  },
  {
    label: 'Reports',
    path: '/reports',
    icon: FileText,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
  },
  {
    label: 'Settings',
    path: '/settings',
    icon: Settings,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CATALOG_MANAGER', 'AREA_MANAGER'],
  },
];

export function AppLayout() {
  const { user, logout, hasRole, setActiveRole, setActiveStore } = useAuth();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [roleDropdownOpen, setRoleDropdownOpen] = useState(false);
  const [storeDropdownOpen, setStoreDropdownOpen] = useState(false);

  // Filter navigation based on user roles
  const filteredNavItems = navigationItems.filter((item) => {
    if (!item.allowedRoles) return true;
    return hasRole(item.allowedRoles);
  });

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  // Get brand class for theming
  const brandClass = user?.activeStoreId?.includes('WZ') ? 'wizopt' : 'bettervision';

  return (
    <div className="min-h-screen bg-gray-50" data-brand={brandClass}>
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 tablet:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={clsx(
          'fixed top-0 left-0 z-50 h-full w-64 bg-white border-r border-gray-200 transition-transform duration-300 tablet:translate-x-0',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        {/* Logo */}
        <div className="h-16 flex items-center justify-between px-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-bv-red-600 rounded-lg flex items-center justify-center">
              <Store className="w-5 h-5 text-white" />
            </div>
            <span className="font-bold text-gray-900">IMS 2.0</span>
          </div>
          <button
            className="tablet:hidden p-2 text-gray-500 hover:text-gray-700"
            onClick={() => setSidebarOpen(false)}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-4 px-2">
          {filteredNavItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 transition-colors touch-target',
                  isActive
                    ? 'bg-bv-red-50 text-bv-red-600 font-medium'
                    : 'text-gray-600 hover:bg-gray-100'
                )
              }
              onClick={() => setSidebarOpen(false)}
            >
              <item.icon className="w-5 h-5" />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        {/* User info */}
        <div className="border-t border-gray-200 p-4">
          <div className="text-sm text-gray-500 mb-2">{user?.name}</div>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 text-gray-600 hover:text-bv-red-600 transition-colors"
          >
            <LogOut className="w-4 h-4" />
            <span className="text-sm">Logout</span>
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="tablet:ml-64">
        {/* Top header */}
        <header className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-4">
          {/* Mobile menu button */}
          <button
            className="tablet:hidden p-2 text-gray-600 hover:text-gray-900"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="w-6 h-6" />
          </button>

          {/* Role selector */}
          {user && user.roles.length > 1 && (
            <div className="relative">
              <button
                className="flex items-center gap-2 px-3 py-2 text-sm bg-gray-100 rounded-lg hover:bg-gray-200"
                onClick={() => setRoleDropdownOpen(!roleDropdownOpen)}
              >
                <span className="font-medium">{user.activeRole.replace('_', ' ')}</span>
                <ChevronDown className="w-4 h-4" />
              </button>
              {roleDropdownOpen && (
                <div className="absolute top-full left-0 mt-1 w-48 bg-white border border-gray-200 rounded-lg shadow-lg z-50">
                  {user.roles.map((role) => (
                    <button
                      key={role}
                      className={clsx(
                        'w-full text-left px-4 py-2 text-sm hover:bg-gray-50',
                        role === user.activeRole && 'bg-bv-red-50 text-bv-red-600'
                      )}
                      onClick={() => {
                        setActiveRole(role);
                        setRoleDropdownOpen(false);
                      }}
                    >
                      {role.replace('_', ' ')}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Store selector */}
          {user && (hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']) || user.storeIds.length > 1) && (
            <div className="relative ml-2">
              <button
                className="flex items-center gap-2 px-3 py-2 text-sm bg-gray-100 rounded-lg hover:bg-gray-200"
                onClick={() => setStoreDropdownOpen(!storeDropdownOpen)}
              >
                <Store className="w-4 h-4" />
                <span className="font-medium">{user.activeStoreId || 'Select Store'}</span>
                <ChevronDown className="w-4 h-4" />
              </button>
              {storeDropdownOpen && (
                <div className="absolute top-full right-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-50">
                  {user.storeIds.map((storeId) => (
                    <button
                      key={storeId}
                      className={clsx(
                        'w-full text-left px-4 py-2 text-sm hover:bg-gray-50',
                        storeId === user.activeStoreId && 'bg-bv-red-50 text-bv-red-600'
                      )}
                      onClick={() => {
                        setActiveStore(storeId);
                        setStoreDropdownOpen(false);
                      }}
                    >
                      {storeId}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Spacer */}
          <div className="flex-1" />

          {/* User avatar */}
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-bv-red-100 rounded-full flex items-center justify-center">
              <span className="text-sm font-medium text-bv-red-600">
                {user?.name?.charAt(0).toUpperCase()}
              </span>
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="p-4 tablet:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default AppLayout;
