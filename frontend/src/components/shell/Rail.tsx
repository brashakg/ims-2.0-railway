// 64px left rail — dark chrome, vertical nav, hover tooltips, avatar at bottom.
// Replaces the old module-aware sidebar with a flat top-level nav.
// Ported from design_handoff_ims_2_0/shell/shell.jsx → Rail

import { NavLink } from 'react-router-dom';
import { useMemo } from 'react';
import { useAuth } from '../../context/AuthContext';
import { Icon, type IconName } from './Icon';
import type { UserRole } from '../../types';

interface NavItem {
  id: string;
  label: string;
  to: string;
  icon: IconName;
  requireRoles?: UserRole[]; // if set, only visible to users holding one of these roles
}

// Rail groups — dividers render between groups
const RAIL_GROUPS: NavItem[][] = [
  // Overview
  [
    { id: 'hub', label: 'Hub', to: '/dashboard', icon: 'home' },
  ],
  // Sales floor
  [
    { id: 'pos', label: 'POS', to: '/pos', icon: 'cart' },
    { id: 'customers', label: 'Customers', to: '/customers', icon: 'users' },
    { id: 'orders', label: 'Orders', to: '/orders', icon: 'receipt' },
    { id: 'returns', label: 'Returns', to: '/returns', icon: 'refresh' },
  ],
  // Clinical
  [
    { id: 'clinical', label: 'Clinical', to: '/clinical', icon: 'eye' },
  ],
  // Stock & supply
  [
    { id: 'inventory', label: 'Inventory', to: '/inventory', icon: 'box' },
    { id: 'purchase', label: 'Purchase', to: '/purchase', icon: 'truck' },
    { id: 'workshop', label: 'Workshop', to: '/workshop', icon: 'wrench' },
    { id: 'catalog', label: 'Catalog', to: '/catalog/add', icon: 'tag' },
  ],
  // Ops
  [
    { id: 'tasks', label: 'Tasks & SOPs', to: '/tasks', icon: 'check' },
    { id: 'hr', label: 'HR', to: '/hr', icon: 'user' },
  ],
  // Analysis
  [
    { id: 'reports', label: 'Reports', to: '/reports', icon: 'chart' },
    { id: 'finance', label: 'Finance', to: '/finance/dashboard', icon: 'banknote' },
  ],
  // Growth
  [
    { id: 'marketing', label: 'Marketing', to: '/customers/campaigns', icon: 'megaphone' },
  ],
  // Support + Jarvis + Setup
  [
    { id: 'print', label: 'Print', to: '/print', icon: 'printer' },
    { id: 'jarvis', label: 'Jarvis', to: '/jarvis', icon: 'cpu', requireRoles: ['SUPERADMIN'] },
    { id: 'setup', label: 'Store Setup', to: '/settings', icon: 'settings' },
  ],
];

function hasAnyRole(userRoles: readonly UserRole[] | undefined, required: UserRole[]): boolean {
  if (!userRoles || userRoles.length === 0) return false;
  return required.some((r) => userRoles.includes(r));
}

export function Rail({ brand = 'bv' }: { brand?: 'bv' | 'wizopt' }) {
  const { user } = useAuth();
  const userRoles = user?.roles;
  const activeRole = user?.activeRole;

  // Filter hidden items based on role
  const visibleGroups = useMemo(() => {
    return RAIL_GROUPS.map((group) =>
      group.filter((item) => {
        if (!item.requireRoles) return true;
        // Check both stored roles[] and active role (covers role-switching)
        return hasAnyRole(userRoles, item.requireRoles) || (activeRole && item.requireRoles.includes(activeRole));
      })
    ).filter((group) => group.length > 0);
  }, [userRoles, activeRole]);

  const glyph = brand === 'wizopt' ? 'W' : 'B';
  const userInitials = (user?.name ?? '')
    .split(/\s+/)
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase() || '?';

  return (
    <aside className="rail">
      <div className="brand" title={brand === 'wizopt' ? 'WizOpt' : 'Better Vision'}>
        {glyph}
      </div>
      {visibleGroups.map((group, gi) => (
        <div key={gi} className="rail-group">
          {group.map((item) => {
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
          {gi < visibleGroups.length - 1 && <div className="rail-sep" />}
        </div>
      ))}
      <div className="rail-spacer" />
      <div className="rail-avatar" title={user?.name ? `${user.name} • ${activeRole}` : 'User'}>
        {userInitials}
      </div>
    </aside>
  );
}
