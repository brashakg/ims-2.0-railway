// 64px left rail — dark chrome, vertical nav, hover tooltips, avatar at bottom.
// Replaces the old module-aware sidebar with a flat top-level nav.
// Ported from design_handoff_ims_2_0/shell/shell.jsx → Rail

import { NavLink, useLocation } from 'react-router-dom';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useAppearance } from '../../context/AppearanceContext';
import { Icon, type IconName } from './Icon';
import type { UserRole } from '../../types';

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
    items: [
      { id: 'pos', label: 'POS', to: '/pos', icon: 'cart' },
      { id: 'customers', label: 'Customers', to: '/customers', icon: 'users' },
      { id: 'walkouts', label: 'Walkouts', to: '/walkouts', icon: 'user' },
      { id: 'orders', label: 'Orders', to: '/orders', icon: 'receipt' },
      { id: 'returns', label: 'Returns', to: '/returns', icon: 'refresh' },
    ],
  },
  {
    title: 'Clinical',
    items: [
      { id: 'clinical', label: 'Clinical', to: '/clinical', icon: 'eye' },
    ],
  },
  {
    title: 'Stock & supply',
    items: [
      { id: 'inventory', label: 'Inventory', to: '/inventory', icon: 'box' },
      { id: 'purchase', label: 'Purchase', to: '/purchase', icon: 'truck' },
      { id: 'vendor-returns', label: 'Vendor Returns', to: '/purchase/vendor-returns', icon: 'refresh', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'WORKSHOP_STAFF'] },
      { id: 'workshop', label: 'Workshop', to: '/workshop', icon: 'wrench' },
      { id: 'catalog', label: 'Catalog', to: '/catalog/add', icon: 'tag' },
    ],
  },
  {
    title: 'Operations',
    items: [
      { id: 'tasks', label: 'Tasks & SOPs', to: '/tasks', icon: 'check' },
      { id: 'expenses', label: 'Expenses', to: '/finance/expenses', icon: 'banknote' },
      { id: 'hr', label: 'HR', to: '/hr', icon: 'user' },
      { id: 'salary-setup', label: 'Salary Setup', to: '/hr/salary-setup', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'payroll-run', label: 'Payroll Run', to: '/hr/payroll-run', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      { id: 'incentive', label: 'Incentive', to: '/incentive', icon: 'zap' },
    ],
  },
  {
    title: 'Analysis',
    items: [
      { id: 'reports', label: 'Reports', to: '/reports', icon: 'chart' },
      { id: 'finance', label: 'Finance', to: '/finance/dashboard', icon: 'banknote' },
    ],
  },
  {
    title: 'Growth',
    items: [
      { id: 'marketing', label: 'Marketing', to: '/customers/campaigns', icon: 'megaphone' },
      { id: 'online-store', label: 'Online Store', to: ECOMMERCE_URL, icon: 'tag', external: true, requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
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
      { id: 'print', label: 'Print', to: '/print', icon: 'printer' },
      { id: 'setup', label: 'Store Setup', to: '/settings', icon: 'settings' },
      { id: 'entities', label: 'Entities', to: '/settings/entities', icon: 'settings', requireRoles: ['SUPERADMIN', 'ADMIN'] },
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

  const toggleGroup = useCallback((title: string) => {
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
