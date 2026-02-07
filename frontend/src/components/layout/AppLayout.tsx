// ============================================================================
// IMS 2.0 - Main Application Layout
// ============================================================================
// Module-aware layout with conditional sidebar

import { useState, useEffect, useRef } from 'react';
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useModule, type ModuleId } from '../../context/ModuleContext';
import { storeApi } from '../../services/api';
import {
  LogOut,
  ChevronDown,
  ChevronLeft,
  Store,
  Home,
  Menu,
  X,
} from 'lucide-react';
import clsx from 'clsx';

// Map URL paths to module IDs
const pathToModule: Record<string, ModuleId> = {
  '/pos': 'pos',
  '/customers': 'customers',
  '/inventory': 'inventory',
  '/orders': 'pos',
  '/clinical': 'clinic',
  '/clinical/test': 'clinic',
  '/clinical/history': 'clinic',
  '/clinical/contact-lens': 'clinic',
  '/prescriptions': 'clinic',
  '/workshop': 'workshop',
  '/tasks': 'hr',
  '/hr': 'hr',
  '/purchase': 'vendors',
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
  const [userDropdownOpen, setUserDropdownOpen] = useState(false);
  const roleDropdownRef = useRef<HTMLDivElement>(null);
  const storeDropdownRef = useRef<HTMLDivElement>(null);
  const userDropdownRef = useRef<HTMLDivElement>(null);

  // Load store names for display
  const [storeNames, setStoreNames] = useState<Record<string, string>>({});
  useEffect(() => {
    storeApi.getStores().then((stores: any) => {
      const storesArr = stores?.stores || stores || [];
      if (Array.isArray(storesArr)) {
        const nameMap: Record<string, string> = {};
        storesArr.forEach((s: any) => { if (s.id && s.storeName) nameMap[s.id] = s.storeName; });
        setStoreNames(nameMap);
      }
    }).catch(() => {});
  }, []);

  // Close dropdowns on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (roleDropdownRef.current && !roleDropdownRef.current.contains(event.target as Node)) {
        setRoleDropdownOpen(false);
      }
      if (storeDropdownRef.current && !storeDropdownRef.current.contains(event.target as Node)) {
        setStoreDropdownOpen(false);
      }
      if (userDropdownRef.current && !userDropdownRef.current.contains(event.target as Node)) {
        setUserDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

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
      {sidebarOpen && moduleConfig && (
        <div
          className="fixed inset-0 bg-black/50 z-40 tablet:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar - only show when module is active */}
      {moduleConfig && (
        <aside
          className={clsx(
            'fixed top-0 left-0 z-50 h-full w-64 bg-white border-r border-gray-200 transition-transform duration-300 flex flex-col',
            sidebarOpen ? 'translate-x-0' : 'tablet:translate-x-0 -translate-x-full'
          )}
        >
          {/* Module Header */}
          <div className="h-16 flex items-center justify-between px-4 border-b border-gray-200">
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
            <button
              className="tablet:hidden p-2 text-gray-500 hover:text-gray-700"
              onClick={() => setSidebarOpen(false)}
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Navigation */}
          <nav className="flex-1 overflow-y-auto py-4 px-2">
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
      )}

      {/* Main content - adjust margin based on sidebar presence */}
      <div className={clsx(moduleConfig ? 'tablet:ml-64' : 'w-full')}>
        {/* Top header */}
        <header className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-4 tablet:px-6">
          {/* Logo and Breadcrumb */}
          <div className="flex items-center gap-4">
            {/* Mobile menu button - only show when module is active */}
            {moduleConfig && (
              <button
                className="tablet:hidden p-2 text-gray-600 hover:text-gray-900"
                onClick={() => setSidebarOpen(true)}
              >
                <Menu className="w-6 h-6" />
              </button>
            )}

            {/* Logo */}
            <div className="flex items-center gap-2 cursor-pointer" onClick={handleBackToDashboard}>
              <div className="w-8 h-8 bg-bv-gold-500 rounded-lg flex items-center justify-center">
                <Store className="w-5 h-5 text-white" />
              </div>
              <span className="font-bold text-gray-900 text-lg">IMS 2.0</span>
            </div>

            {/* Breadcrumb - only show when module is active */}
            {moduleConfig && (
              <div className="hidden tablet:flex items-center gap-2 text-sm">
                <ChevronDown className="w-4 h-4 text-gray-400 rotate-[-90deg]" />
                <button
                  onClick={handleBackToDashboard}
                  className="text-gray-500 hover:text-gray-700 transition-colors flex items-center gap-1"
                >
                  <Home className="w-4 h-4" />
                  Dashboard
                </button>
                <ChevronDown className="w-4 h-4 text-gray-400 rotate-[-90deg]" />
                <div className="flex items-center gap-2">
                  <div className={clsx('w-6 h-6 rounded-lg flex items-center justify-center', moduleConfig.bgColor)}>
                    <moduleConfig.icon className={clsx('w-4 h-4', moduleConfig.color)} />
                  </div>
                  <span className={clsx('font-medium', moduleConfig.color)}>{moduleConfig.title}</span>
                </div>
              </div>
            )}
          </div>

          {/* Right side - Role selector, Store selector, User avatar */}
          <div className="flex items-center gap-3">
            {/* Role selector */}
            {user && user.roles.length > 1 && (
              <div className="relative" ref={roleDropdownRef}>
                <button
                  className="flex items-center gap-2 px-3 py-2 text-sm bg-gray-100 rounded-lg hover:bg-gray-200"
                  onClick={() => setRoleDropdownOpen(!roleDropdownOpen)}
                >
                  <span className="font-medium">{user.activeRole.replaceAll('_', ' ')}</span>
                  <ChevronDown className="w-4 h-4" />
                </button>
                {roleDropdownOpen && (
                  <div className="absolute top-full right-0 mt-1 w-48 bg-white border border-gray-200 rounded-lg shadow-lg z-50">
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
                        {role.replaceAll('_', ' ')}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Store selector - not available for single-store roles like Optometrist */}
            {user && (hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']) || (user.storeIds.length > 1 && !hasRole(['OPTOMETRIST']))) && (
              <div className="relative" ref={storeDropdownRef}>
                <button
                  className="flex items-center gap-2 px-3 py-2 text-sm bg-gray-100 rounded-lg hover:bg-gray-200"
                  onClick={() => setStoreDropdownOpen(!storeDropdownOpen)}
                >
                  <Store className="w-4 h-4" />
                  <span className="font-medium hidden tablet:inline">{storeNames[user.activeStoreId] || user.activeStoreId || 'Select Store'}</span>
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
                        {storeNames[storeId] || storeId}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* User avatar and dropdown */}
            <div className="relative" ref={userDropdownRef}>
              <button
                onClick={() => setUserDropdownOpen(!userDropdownOpen)}
                className="w-8 h-8 bg-bv-gold-100 rounded-full flex items-center justify-center hover:bg-bv-gold-200 transition-colors"
                title="User menu"
              >
                <span className="text-sm font-medium text-bv-gold-600">
                  {user?.name?.charAt(0).toUpperCase()}
                </span>
              </button>
              {userDropdownOpen && (
                <div className="absolute top-full right-0 mt-2 w-48 bg-white border border-gray-200 rounded-lg shadow-lg z-50">
                  <div className="px-4 py-3 border-b border-gray-100">
                    <div className="text-sm font-medium text-gray-900">{user?.name}</div>
                    <div className="text-xs text-gray-500 mt-1">{user?.email}</div>
                    <div className="text-xs text-gray-500 mt-1">Role: {user?.activeRole?.replaceAll('_', ' ')}</div>
                  </div>
                  <button
                    onClick={() => {
                      navigate('/settings?tab=profile');
                      setUserDropdownOpen(false);
                    }}
                    className="w-full text-left px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
                  >
                    My Profile
                  </button>
                  <button
                    onClick={() => {
                      handleLogout();
                      setUserDropdownOpen(false);
                    }}
                    className="w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors border-t border-gray-100"
                  >
                    Logout
                  </button>
                </div>
              )}
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
