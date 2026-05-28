// 64px left rail — dark chrome, vertical nav, hover tooltips, avatar at bottom.
// Replaces the old module-aware sidebar with a flat top-level nav.
// Ported from design_handoff_ims_2_0/shell/shell.jsx → Rail

import { NavLink, useLocation } from 'react-router-dom';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useAppearance } from '../../context/AppearanceContext';
import { Icon, type IconName } from './Icon';
import type { UserRole } from '../../types';
import { ecommerceSsoApi } from '../../services/api/ecommerceSso';

const COLLAPSED_GROUPS_KEY = 'ims_rail_collapsed_groups';

/** Load saved collapsed-group state from localStorage. Returns null when
 *  no key is present so the caller can apply the closed-by-default rule
 *  (all titled groups collapsed) on first visit. An empty array stored
 *  in localStorage is treated as a real user choice — all expanded. */
function loadCollapsedGroups(): Set<string> | null {
  try {
    const raw = localStorage.getItem(COLLAPSED_GROUPS_KEY);
    if (raw === null) return null;
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((x) => typeof x === 'string') : []);
  } catch {
    return null;
  }
}

function saveCollapsedGroups(set: Set<string>): void {
  try {
    localStorage.setItem(COLLAPSED_GROUPS_KEY, JSON.stringify(Array.from(set)));
  } catch {
    /* localStorage full or disabled — collapse state stays in-memory only */
  }
}

interface NavItem {
  id: string;
  label: string;
  to: string;
  icon: IconName;
  requireRoles?: UserRole[]; // if set, only visible to users holding one of these roles
  external?: boolean; // render as <a target=_blank> instead of an in-app route
  sso?: boolean; // external app reached via an SSO handoff (mint token, then open)
}

// The consolidated e-commerce (BVI) admin. Configurable per-env so the URL can
// change with the uniparallel.com cutover without a code change.
const ECOMMERCE_URL =
  (import.meta.env.VITE_ECOMMERCE_URL as string | undefined) || 'https://uniparallel.com';

interface NavGroup {
  /** Section title rendered only in expanded mode. Omit for the first
   *  group so the menu starts flush with the brand wordmark. */
  title?: string;
  items: NavItem[];
}

// Rail groups — section titles render only in expanded mode; thin
// dividers render between groups in collapsed mode for orientation.
const RAIL_GROUPS: NavGroup[] = [
  {
    items: [
      { id: 'hub', label: 'Hub', to: '/dashboard', icon: 'home' },
      { id: 'notifications', label: 'Notifications', to: '/notifications', icon: 'bell' },
    ],
  },
  {
    title: 'Sales floor',
    // requireRoles on each item MIRRORS the route's ProtectedRoute allowedRoles
    // in App.tsx, so a role never sees a nav link that lands it on /unauthorized.
    items: [
      { id: 'pos', label: 'POS', to: '/pos', icon: 'cart', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
      { id: 'customers', label: 'Customers', to: '/customers', icon: 'users', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
      { id: 'walkouts', label: 'Walkouts', to: '/walkouts', icon: 'user', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'SALES_CASHIER', 'CASHIER'] },
      { id: 'orders', label: 'Orders', to: '/orders', icon: 'receipt', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF', 'OPTOMETRIST', 'WORKSHOP_STAFF'] },
      { id: 'returns', label: 'Returns', to: '/returns', icon: 'refresh', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER'] },
    ],
  },
  {
    title: 'Clinical',
    items: [
      { id: 'clinical', label: 'Clinical', to: '/clinical', icon: 'eye', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST'] },
    ],
  },
  {
    title: 'Stock & supply',
    items: [
      { id: 'inventory', label: 'Inventory', to: '/inventory', icon: 'box', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'] },
      { id: 'power-grid', label: 'Power Grid', to: '/inventory/power-grid', icon: 'box', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'OPTOMETRIST'] },
      { id: 'online-stock', label: 'Online Stock', to: '/inventory/online-sync', icon: 'box', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER'] },
      { id: 'purchase', label: 'Purchase', to: '/purchase', icon: 'truck', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'vendor-returns', label: 'Vendor Returns', to: '/purchase/vendor-returns', icon: 'refresh', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'WORKSHOP_STAFF'] },
      { id: 'workshop', label: 'Workshop', to: '/workshop', icon: 'wrench', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'] },
      { id: 'catalog', label: 'Catalog', to: '/catalog/add', icon: 'tag', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
      { id: 'catalog-autopilot', label: 'Catalog Autopilot', to: '/catalog/autopilot', icon: 'cpu', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
      { id: 'pricing', label: 'Pricing & Offers', to: '/catalog/pricing', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
    ],
  },
  {
    title: 'Operations',
    items: [
      { id: 'tasks', label: 'Tasks & SOPs', to: '/tasks', icon: 'check', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'expenses', label: 'Expenses', to: '/finance/expenses', icon: 'banknote' },
      { id: 'hr', label: 'HR', to: '/hr', icon: 'user', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'salary-setup', label: 'Salary Setup', to: '/hr/salary-setup', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'payroll-run', label: 'Payroll Run', to: '/hr/payroll-run', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      { id: 'incentive', label: 'Incentive', to: '/incentive', icon: 'zap', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'SALES_CASHIER', 'CASHIER'] },
    ],
  },
  {
    title: 'Analysis',
    items: [
      { id: 'reports', label: 'Reports', to: '/reports', icon: 'chart', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'finance', label: 'Finance', to: '/finance/dashboard', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'cash-register', label: 'Cash Register', to: '/finance/cash-register', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'cashflow', label: 'Cash Flow', to: '/finance/cash-flow', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      { id: 'itc', label: 'GST Credit (ITC)', to: '/finance/itc', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
    ],
  },
  {
    title: 'Growth',
    items: [
      { id: 'marketing', label: 'Marketing', to: '/customers/campaigns', icon: 'megaphone', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      { id: 'online-store', label: 'Online Store', to: ECOMMERCE_URL, icon: 'tag', external: true, sso: true, requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
    ],
  },
  {
    // AI features (JARVIS + the 8 superhero agents live behind /jarvis).
    // SUPERADMIN-only, so the whole group only renders for superadmins.
    title: 'AI',
    items: [
      { id: 'jarvis', label: 'Jarvis', to: '/jarvis', icon: 'cpu', requireRoles: ['SUPERADMIN'] },
    ],
  },
  {
    title: 'System',
    items: [
      { id: 'print', label: 'Print', to: '/print', icon: 'printer', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
      { id: 'setup', label: 'Settings', to: '/settings', icon: 'settings', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'ACCOUNTANT'] },
      { id: 'onboarding', label: 'Store Onboarding', to: '/setup', icon: 'settings', requireRoles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'organization', label: 'Organization', to: '/organization', icon: 'settings', requireRoles: ['SUPERADMIN', 'ADMIN'] },
    ],
  },
];

function hasAnyRole(userRoles: readonly UserRole[] | undefined, required: UserRole[]): boolean {
  if (!userRoles || userRoles.length === 0) return false;
  return required.some((r) => userRoles.includes(r));
}

export function Rail({ brand = 'bv' }: { brand?: 'bv' | 'wizopt' }) {
  const { user } = useAuth();
  const { railExpanded, toggleRailExpanded } = useAppearance();
  const { pathname } = useLocation();
  const userRoles = user?.roles;
  const activeRole = user?.activeRole;

  // Filter hidden items based on role
  const visibleGroups = useMemo(() => {
    return RAIL_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        if (!item.requireRoles) return true;
        // Check both stored roles[] and active role (covers role-switching)
        return hasAnyRole(userRoles, item.requireRoles) || (activeRole && item.requireRoles.includes(activeRole));
      }),
    })).filter((group) => group.items.length > 0);
  }, [userRoles, activeRole]);

  // Which group titles are collapsed. Untitled (Hub) groups can't collapse.
  // Persisted to localStorage so a user's collapse choices survive refresh
  // within a session. The group containing the active route is force-expanded
  // on every navigation so the user is never one click away from an invisible
  // nav item.
  //
  // Default (no localStorage key) = ALL groups EXPANDED, so the sidebar shows
  // its full grouped structure. AuthContext clears the key on every login, so
  // each login starts grouped/expanded; in-session collapses still persist
  // across refresh until the next login.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => {
    const saved = loadCollapsedGroups();
    if (saved !== null) return saved;
    return new Set<string>();
  });

  const activeGroupTitle = useMemo(() => {
    for (const g of visibleGroups) {
      if (!g.title) continue;
      const match = g.items.some((i) => pathname === i.to || pathname.startsWith(i.to + '/'));
      if (match) return g.title;
    }
    return null;
  }, [pathname, visibleGroups]);

  useEffect(() => {
    if (!activeGroupTitle) return;
    setCollapsedGroups((prev) => {
      if (!prev.has(activeGroupTitle)) return prev;
      const next = new Set(prev);
      next.delete(activeGroupTitle);
      saveCollapsedGroups(next);
      return next;
    });
  }, [activeGroupTitle]);

  // Latest active-group title for the auto-group timer (read at fire time so
  // navigating around doesn't restart the countdown).
  const activeGroupTitleRef = useRef(activeGroupTitle);
  activeGroupTitleRef.current = activeGroupTitle;

  // The rail starts fully expanded after login (all groups open), which makes a
  // long sidebar that can scroll past the main content, leaving empty space on
  // the right. 15s after mount, auto-collapse every group except the active one
  // so the rail settles into a compact grouped state. A manual group toggle
  // cancels this (the user has taken control of the layout).
  const autoGroupTimer = useRef<number | null>(null);
  useEffect(() => {
    autoGroupTimer.current = window.setTimeout(() => {
      autoGroupTimer.current = null;
      setCollapsedGroups(() => {
        const next = new Set<string>();
        for (const g of RAIL_GROUPS) {
          if (g.title && g.title !== activeGroupTitleRef.current) next.add(g.title);
        }
        saveCollapsedGroups(next);
        return next;
      });
    }, 15000);
    return () => {
      if (autoGroupTimer.current) {
        window.clearTimeout(autoGroupTimer.current);
        autoGroupTimer.current = null;
      }
    };
  }, []);

  const toggleGroup = useCallback((title: string) => {
    // User is managing groups manually — stop the pending auto-collapse.
    if (autoGroupTimer.current) {
      window.clearTimeout(autoGroupTimer.current);
      autoGroupTimer.current = null;
    }
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      saveCollapsedGroups(next);
      return next;
    });
  }, []);

  const glyph = brand === 'wizopt' ? 'W' : 'B';
  const wordmark = brand === 'wizopt' ? 'WizOpt' : 'Better Vision';
  const userInitials = (user?.name ?? '')
    .split(/\s+/)
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase() || '?';

  return (
    <aside className={'rail' + (railExpanded ? ' expanded' : '')}>
      {/* Header row — brand glyph + wordmark (expanded only) + toggle.
          Toggle moved up here so it's discoverable above the fold; the
          old position at the bottom was easy to miss. */}
      <div className="rail-header">
        <div className="rail-brand-row">
          <div className="brand" title={wordmark}>{glyph}</div>
          {railExpanded && (
            <span className="rail-wordmark" aria-hidden="true">{wordmark}</span>
          )}
        </div>
        <button
          type="button"
          className="rail-toggle"
          onClick={toggleRailExpanded}
          title={railExpanded ? 'Collapse — show icons only' : 'Expand — show icons + labels'}
          aria-label={railExpanded ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          <ChevronIcon flipped={railExpanded} />
        </button>
      </div>

      {visibleGroups.map((group, gi) => {
        const isCollapsible = railExpanded && !!group.title;
        const isCollapsed = isCollapsible && collapsedGroups.has(group.title!);
        const itemsHidden = isCollapsed;
        return (
          <div key={gi} className="rail-group">
            {isCollapsible && (
              <button
                type="button"
                className={'rail-group-title' + (isCollapsed ? ' collapsed' : '')}
                onClick={() => toggleGroup(group.title!)}
                aria-expanded={!isCollapsed}
                aria-controls={`rail-group-${gi}-items`}
              >
                <span className="rail-group-title-label">{group.title}</span>
                <GroupChevron expanded={!isCollapsed} />
              </button>
            )}
            <div id={`rail-group-${gi}-items`} hidden={itemsHidden}>
              {group.items.map((item) => {
                const IconCmp = Icon[item.icon];
                if (item.external) {
                  if (item.sso) {
                    // SSO handoff: mint a short-lived token, then open the
                    // external app already logged in. Falls back to a plain
                    // open if SSO isn't configured / the user isn't allowed.
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className="rail-item"
                        title={`${item.label} (opens in new tab)`}
                        onClick={async () => {
                          try {
                            const r = await ecommerceSsoApi.getUrl();
                            window.open(r.url, '_blank', 'noopener,noreferrer');
                          } catch {
                            window.open(item.to, '_blank', 'noopener,noreferrer');
                          }
                        }}
                      >
                        <IconCmp />
                        <span className="rail-label">{item.label}</span>
                      </button>
                    );
                  }
                  // External app (e.g. the e-commerce admin) — open in a new tab.
                  return (
                    <a
                      key={item.id}
                      href={item.to}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rail-item"
                      title={`${item.label} (opens in new tab)`}
                    >
                      <IconCmp />
                      <span className="rail-label">{item.label}</span>
                    </a>
                  );
                }
                return (
                  <NavLink
                    key={item.id}
                    to={item.to}
                    className={({ isActive }) => 'rail-item' + (isActive ? ' active' : '')}
                    title={item.label}
                  >
                    <IconCmp />
                    <span className="rail-label">{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
            {gi < visibleGroups.length - 1 && !railExpanded && <div className="rail-sep" />}
          </div>
        );
      })}
      <div className="rail-spacer" />
      <div className="rail-avatar" title={user?.name ? `${user.name} • ${activeRole}` : 'User'}>
        <span className="rail-avatar-initials">{userInitials}</span>
        {railExpanded && (
          <span className="rail-avatar-name" aria-hidden="true">
            {user?.name?.split(' ')[0] || 'User'}
          </span>
        )}
      </div>
    </aside>
  );
}

function ChevronIcon({ flipped }: { flipped: boolean }) {
  // Right-pointing chevron when collapsed (=> expand). Left when expanded.
  return (
    <svg
      viewBox="0 0 24 24" width={20} height={20}
      fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round"
      style={{ transform: flipped ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }}
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function GroupChevron({ expanded }: { expanded: boolean }) {
  // Down chevron when expanded (group is open), right chevron when collapsed.
  return (
    <svg
      viewBox="0 0 24 24" width={12} height={12}
      fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round"
      style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform .15s', flexShrink: 0 }}
      aria-hidden="true"
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}
