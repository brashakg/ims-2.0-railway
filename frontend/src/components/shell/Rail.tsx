// Phone-only nav surface — bottom tab bar (≤767) + hamburger drawer.
// ---------------------------------------------------------------------------
// On tablet+desktop the top horizontal menu (TopNav.tsx) is the primary nav and
// this rail is hidden (see index.css). On phones the rail reflows to a bottom
// tab bar (mobile.css) and the topbar hamburger opens this same markup as a
// left drawer. The nav model + role gating come from the shared navConfig so the
// drawer and the top menu never drift.
// Ported from design_handoff_ims_2_0/shell/shell.jsx -> Rail

import { NavLink, useLocation } from 'react-router-dom';
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useAppearance } from '../../context/AppearanceContext';
import { Icon } from './Icon';
import { NAV_GROUPS, filterVisibleGroups } from './navConfig';
import { getBrandAssets } from '../../utils/brandAssets';

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

export function Rail({ brand = 'bv', mobileOpen = false }: { brand?: 'bv' | 'wizopt'; mobileOpen?: boolean }) {
  const { user, hasModuleAccess, logout } = useAuth();
  const { railExpanded, toggleRailExpanded } = useAppearance();
  // User menu (sign out) anchored on the bottom rail avatar.
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement | null>(null);
  const railRef = useRef<HTMLElement | null>(null);
  // Collapsed-mode tooltips are position:fixed (so they escape the .rail-nav
  // scroll-clip box). Fixed positioning can't read the hovered item's location
  // from CSS, so on hover we publish the item's rect to --tip-x/--tip-y on the
  // rail; the .rail-label reads them. Only meaningful in icon-only mode.
  const positionTooltip = useCallback((e: ReactMouseEvent<HTMLElement>) => {
    const rail = railRef.current;
    if (!rail || rail.classList.contains('expanded')) return;
    const r = e.currentTarget.getBoundingClientRect();
    rail.style.setProperty('--tip-x', `${Math.round(r.right + 10)}px`);
    rail.style.setProperty('--tip-y', `${Math.round(r.top + r.height / 2)}px`);
  }, []);
  useEffect(() => {
    if (!userMenuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [userMenuOpen]);
  const handleSignOut = async () => {
    setUserMenuOpen(false);
    try { await logout(); } finally { window.location.assign('/login'); }
  };
  // In the mobile drawer, always render the expanded layout (group headers +
  // labels) regardless of the desktop icon-only collapse state.
  const expanded = railExpanded || mobileOpen;
  const { pathname } = useLocation();
  const userRoles = user?.roles;
  const activeRole = user?.activeRole;

  // Filter hidden items based on role AND the per-user deny-only module
  // override (shared with the top menu via navConfig.filterVisibleGroups so the
  // two surfaces can never drift). The role check is the ceiling; module access
  // can only further hide an item whose route belongs to a denied module.
  // ProtectedRoute enforces the same gate at the route level so a direct URL is
  // blocked too.
  const visibleGroups = useMemo(
    () => filterVisibleGroups(userRoles, activeRole, hasModuleAccess),
    [userRoles, activeRole, hasModuleAccess],
  );

  // Which group titles are collapsed. Untitled (Hub) groups can't collapse.
  // Persisted to localStorage so a user's collapse choices survive refresh
  // within a session. The group containing the active route is force-expanded
  // on every navigation so the user is never one click away from an invisible
  // nav item.
  //
  // Default (no localStorage key) = ALL titled groups COLLAPSED, so the rail is
  // short immediately on login (the admin/superadmin rail lists ~50 items).
  // AuthContext clears the key on every login, so each login starts collapsed;
  // a user's manual expands are written to localStorage and survive refresh
  // until the next login. The active-route group is force-expanded below.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => {
    const saved = loadCollapsedGroups();
    if (saved !== null) return saved;
    const allTitled = new Set<string>();
    for (const g of NAV_GROUPS) {
      if (g.title) allTitled.add(g.title);
    }
    return allTitled;
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

  // Groups now start COLLAPSED by default (see the collapsedGroups initializer),
  // so the old 15s "auto-collapse after mount" timer was removed — the rail is
  // already compact on login and a timer would only risk clobbering a user's
  // manual expand.

  const toggleGroup = useCallback((title: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      saveCollapsedGroups(next);
      return next;
    });
  }, []);

  const brandAssets = getBrandAssets(brand);
  const wordmark = brandAssets.name;
  const userInitials = (user?.name ?? '')
    .split(/\s+/)
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase() || '?';

  return (
    <aside
      ref={railRef}
      id="rail-drawer"
      className={'rail' + (expanded ? ' expanded' : '') + (mobileOpen ? ' rail-mobile-open' : '')}
      {...(mobileOpen ? { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Navigation menu' } : {})}
    >
      {/* Header row — brand glyph + wordmark (expanded only) + toggle.
          Toggle moved up here so it's discoverable above the fold; the
          old position at the bottom was easy to miss. */}
      <div className="rail-header">
        <div className="rail-brand-row">
          {/* Real brand mark — white knockout so it reads on the dark rail */}
          <div className="brand" title={wordmark}>
            <img
              src={brandAssets.markWhite}
              alt={wordmark}
              width={28}
              height={28}
              style={{ objectFit: 'contain', display: 'block' }}
            />
          </div>
          {expanded && (
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

      {/* Scrollable nav list. The scroll lives HERE (not on .rail) so the rail
          itself keeps overflow: visible and the hover tooltips (.rail-label)
          aren't clipped in collapsed icon-only mode. The brand header above and
          the avatar/user-menu below stay pinned outside this scroll region. */}
      <div className="rail-nav">
      {visibleGroups.map((group, gi) => {
        const isCollapsible = expanded && !!group.title;
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
                  // External app — open in a new tab.
                  return (
                    <a
                      key={item.id}
                      href={item.to}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rail-item"
                      title={`${item.label} (opens in new tab)`}
                      onMouseEnter={positionTooltip}
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
                    onMouseEnter={positionTooltip}
                  >
                    <IconCmp />
                    <span className="rail-label">{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
            {gi < visibleGroups.length - 1 && !expanded && <div className="rail-sep" />}
          </div>
        );
      })}
      <div className="rail-spacer" />
      </div>
      <div className="rail-usermenu" ref={userMenuRef}>
        {userMenuOpen && (
          <div className="rail-usermenu-pop" role="menu">
            <div className="rail-usermenu-head">
              <span className="rail-usermenu-name">{user?.name || 'User'}</span>
              <span className="rail-usermenu-role">{(activeRole || '').toString().replaceAll('_', ' ')}</span>
            </div>
            <button
              type="button"
              className="rail-usermenu-signout"
              role="menuitem"
              onClick={handleSignOut}
            >
              <svg viewBox="0 0 24 24" width={16} height={16} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              Sign out
            </button>
          </div>
        )}
        <button
          type="button"
          className="rail-avatar rail-avatar-btn"
          aria-haspopup="menu"
          aria-expanded={userMenuOpen}
          title={user?.name ? `${user.name} • ${activeRole} — click to sign out` : 'Account'}
          onClick={() => setUserMenuOpen((o) => !o)}
        >
          <span className="rail-avatar-initials">{userInitials}</span>
          {expanded && (
            <span className="rail-avatar-name">
              {user?.name?.split(' ')[0] || 'User'}
            </span>
          )}
          {expanded && (
            <svg className="rail-avatar-caret" viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="18 15 12 9 6 15" />
            </svg>
          )}
        </button>
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
