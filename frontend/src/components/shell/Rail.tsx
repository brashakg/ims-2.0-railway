// 64px left rail — dark chrome, vertical nav, hover tooltips, avatar at bottom.
// Replaces the old module-aware sidebar with a flat top-level nav.
// Ported from design_handoff_ims_2_0/shell/shell.jsx → Rail

import { NavLink } from 'react-router-dom';
import { useMemo } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useAppearance } from '../../context/AppearanceContext';
import { Icon, type IconName } from './Icon';
import type { UserRole } from '../../types';

interface NavItem {
  id: string;
  label: string;
  to: string;
  icon: IconName;
  requireRoles?: UserRole[]; // if set, only visible to users holding one of these roles
}

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
      { id: 'workshop', label: 'Workshop', to: '/workshop', icon: 'wrench' },
      { id: 'catalog', label: 'Catalog', to: '/catalog/add', icon: 'tag' },
    ],
  },
  {
    title: 'Ops',
    items: [
      { id: 'tasks', label: 'Tasks & SOPs', to: '/tasks', icon: 'check' },
      { id: 'hr', label: 'HR', to: '/hr', icon: 'user' },
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
    ],
  },
  {
    title: 'System',
    items: [
      { id: 'print', label: 'Print', to: '/print', icon: 'printer' },
      { id: 'jarvis', label: 'Jarvis', to: '/jarvis', icon: 'cpu', requireRoles: ['SUPERADMIN'] },
      { id: 'setup', label: 'Store Setup', to: '/settings', icon: 'settings' },
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

      {visibleGroups.map((group, gi) => (
        <div key={gi} className="rail-group">
          {railExpanded && group.title && (
            <div className="rail-group-title" aria-hidden="true">{group.title}</div>
          )}
          {group.items.map((item) => {
            const IconCmp = Icon[item.icon];
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
          {gi < visibleGroups.length - 1 && !railExpanded && <div className="rail-sep" />}
        </div>
      ))}
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
