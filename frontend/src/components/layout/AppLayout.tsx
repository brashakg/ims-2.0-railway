// ============================================================================
// IMS 2.0 - Main Application Layout
// ============================================================================
// Dynamic sidebar navigation with module context awareness

import { useState, useEffect } from 'react';
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useModule, MODULE_CONFIGS, type ModuleId } from '../../context/ModuleContext';
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
  ChevronLeft,
  Store,
  Eye,
  ClipboardList,
  Home,
} from 'lucide-react';
import clsx from 'clsx';
import type { UserRole } from '../../types';

// Navigation items configuration (for when no module is active)
interface NavItem {
  label: string;
  path: string;
  icon: React.ComponentType<{ className?: string }>;
  allowedRoles?: UserRole[];
  moduleId?: ModuleId;
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
    moduleId: 'pos',
  },
  {
    label: 'Customers',
    path: '/customers',
    icon: Users,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF'],
    moduleId: 'customers',
  },
  {
    label: 'Inventory',
    path: '/inventory',
    icon: Package,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'],
    moduleId: 'inventory',
  },
  {
    label: 'Orders',
    path: '/orders',
    icon: FileText,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_CASHIER'],
    moduleId: 'pos',
  },
  {
    label: 'Eye Tests',
    path: '/clinical',
    icon: Eye,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST'],
    moduleId: 'clinic',
  },
  {
    label: 'Workshop',
    path: '/workshop',
    icon: Wrench,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'],
    moduleId: 'pos',
  },
  {
    label: 'Tasks',
    path: '/tasks',
    icon: ClipboardList,
    moduleId: 'hr',
  },
  {
    label: 'HR',
    path: '/hr',
    icon: Calendar,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    moduleId: 'hr',
  },
  {
    label: 'Reports',
    path: '/reports',
    icon: FileText,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    moduleId: 'reports',
  },
  {
    label: 'Settings',
    path: '/settings',
    icon: Settings,
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CATALOG_MANAGER', 'AREA_MANAGER'],
    moduleId: 'settings',
  },
];

// Map URL paths to module IDs
const pathToModule: Record<string, ModuleId> = {
  '/pos': 'pos',
  '/customers': 'customers',
  '/inventory': 'inventory',
  '/orders': 'pos',
  '/clinical': 'clinic',
  '/workshop': 'pos',
  '/tasks': 'hr',
  '/hr': 'hr',
  '/reports': 'reports',
  '/settings': 'settings',
};

export function AppLayout() {
  const { user, logout, hasRole, setActiveRole, setActiveStore } = useAuth();
  const { activeModule, setActiveModule, getModuleConfig, goToDashboard } = useModule();
  const navigate = useNavigate();
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [roleDropdownOpen, setRoleDropdownOpen] = useState(false);
  const [storeDropdownOpen, setStoreDropdownOpen] = useState(false);

  // Auto-detect module from URL path on mount and route changes
  useEffect(() => {
    const path = location.pathname;
    const moduleId = pathToModule[path];

    if (moduleId && moduleId !== activeModule) {
      setActiveModule(moduleId);
    } else if (path === '/dashboard' && activeModule) {
      goToDashboard();
    }
  }, [location.pathname, activeModule, setActiveModule, goToDashboard]);

  // Get active module config
  const moduleConfig = activeModule ? getModuleConfig(activeModule) : null;

  // Filter navigation based on user roles
  const filteredNavItems = navigationItems.filter((item) => {
    if (!item.allowedRoles) return true;
    return hasRole(item.allowedRoles);
  });

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const handleBackToDashboard = () => {
    goToDashboard();
    navigate('/dashboard');
    setSidebarOpen(false);
  };

  // Get brand class for theming
  const brandClass = user?.activeStoreId?.includes('WZ') ? 'wizopt' : 'bettervision';

  // Determine active colors based on module
  const getActiveColors = () => {
    if (moduleConfig) {
      // Use module colors
      return {
        activeBg: moduleConfig.bgColor,
        activeText: moduleConfig.color,
        hoverBg: 'hover:bg-gray-100',
      };
    }
    return {
      activeBg: 'bg-bv-gold-50',
      activeText: 'text-bv-gold-600',
      hoverBg: 'hover:bg-gray-100',
    };
  };

  const colors = getActiveColors();

  return (
    <div className="min-h-screen bg-gray-50" data-brand={brandClass}>
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 tablet:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar - always visible on desktop */}
      <aside
        className={clsx(
          'fixed top-0 left-0 z-50 h-full w-64 bg-white border-r border-gray-200 transition-transform duration-300 flex flex-col',
          sidebarOpen ? 'translate-x-0' : 'tablet:translate-x-0 -translate-x-full'
        )}
      >
        {/* Logo / Module Header */}
        <div className="h-16 flex items-center justify-between px-4 border-b border-gray-200">
          {moduleConfig ? (
            // Module mode - show module title
            <div className="flex items-center gap-2 flex-1">
              <button
                onClick={handleBackToDashboard}
                className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
                title="Back to Dashboard"
              >
                <ChevronLeft className="w-5 h-5 text-gray-500" />
              </button>
              <div className={clsx('w-8 h-8 rounded-lg flex items-center justify-center', moduleConfig.bgColor)}>
                <moduleConfig.icon className={clsx('w-5 h-5', moduleConfig.color)} />
              </div>
              <div className="flex-1 min-w-0">
                <span className="font-bold text-gray-900 text-sm truncate block">{moduleConfig.title}</span>
              </div>
            </div>
          ) : (
            // Dashboard mode - show logo
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-bv-gold-500 rounded-lg flex items-center justify-center">
                <Store className="w-5 h-5 text-white" />
              </div>
              <span className="font-bold text-gray-900">IMS 2.0</span>
            </div>
          )}
          <button
            className="tablet:hidden p-2 text-gray-500 hover:text-gray-700"
            onClick={() => setSidebarOpen(false)}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-4 px-2">
          {moduleConfig ? (
            // Module-specific navigation
            <>
              {/* Back to Dashboard link */}
              <button
                onClick={handleBackToDashboard}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg mb-2 w-full text-gray-500 hover:bg-gray-100 transition-colors"
              >
                <Home className="w-5 h-5" />
                <span>Dashboard</span>
              </button>

              {/* Module section header */}
              <div className="px-3 py-2 mb-1">
                <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  {moduleConfig.title}
                </span>
              </div>

              {/* Module sidebar items */}
              {moduleConfig.sidebarItems.map((item) => (
                <NavLink
                  key={item.id}
                  to={item.path}
                  className={({ isActive }) =>
                    clsx(
                      'flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 transition-colors touch-target',
                      isActive
                        ? `${colors.activeBg} ${colors.activeText} font-medium`
                        : `text-gray-600 ${colors.hoverBg}`
                    )
                  }
                  onClick={() => setSidebarOpen(false)}
                >
                  <span className="w-5 h-5 flex items-center justify-center">
                    <span className="w-1.5 h-1.5 rounded-full bg-current" />
                  </span>
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </>
          ) : (
            // Default navigation (Dashboard mode)
            filteredNavItems.map((item) => (
              <NavLink
                key={item.path}
                to={item.path}
                className={({ isActive }) =>
                  clsx(
                    'flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 transition-colors touch-target',
                    isActive
                      ? `${colors.activeBg} ${colors.activeText} font-medium`
                      : 'text-gray-600 hover:bg-gray-100'
                  )
                }
                onClick={() => {
                  // Set active module when clicking a nav item
                  if (item.moduleId) {
                    setActiveModule(item.moduleId);
                  }
                  setSidebarOpen(false);
                }}
              >
                <item.icon className="w-5 h-5" />
                <span>{item.label}</span>
              </NavLink>
            ))
          )}
        </nav>

        {/* Quick module access (when in module mode) */}
        {moduleConfig && (
          <div className="border-t border-gray-200 p-2">
            <div className="px-3 py-2">
              <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                Quick Access
              </span>
            </div>
            <div className="flex flex-wrap gap-1 px-2">
              {MODULE_CONFIGS.slice(0, 5).map((mod) => (
                <button
                  key={mod.id}
                  onClick={() => {
                    setActiveModule(mod.id);
                    navigate(mod.sidebarItems[0]?.path || '/dashboard');
                    setSidebarOpen(false);
                  }}
                  className={clsx(
                    'p-2 rounded-lg transition-colors',
                    mod.id === activeModule
                      ? `${mod.bgColor}`
                      : 'hover:bg-gray-100'
                  )}
                  title={mod.title}
                >
                  <mod.icon className={clsx('w-4 h-4', mod.id === activeModule ? mod.color : 'text-gray-400')} />
                </button>
              ))}
            </div>
          </div>
        )}

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

      {/* Main content - always add left margin for sidebar on desktop */}
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

          {/* Breadcrumb / Page title */}
          <div className="hidden tablet:flex items-center gap-2 text-sm">
            <button
              onClick={handleBackToDashboard}
              className="text-gray-500 hover:text-gray-700 transition-colors flex items-center gap-1"
            >
              <Home className="w-4 h-4" />
              Dashboard
            </button>
            {moduleConfig && (
              <>
                <ChevronDown className="w-4 h-4 text-gray-400 rotate-[-90deg]" />
                <span className={moduleConfig.color}>{moduleConfig.title}</span>
              </>
            )}
          </div>

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
                        role === user.activeRole && 'bg-bv-gold-50 text-bv-gold-600'
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
                        storeId === user.activeStoreId && 'bg-bv-gold-50 text-bv-gold-600'
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
            <div className="w-8 h-8 bg-bv-gold-100 rounded-full flex items-center justify-center">
              <span className="text-sm font-medium text-bv-gold-600">
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
