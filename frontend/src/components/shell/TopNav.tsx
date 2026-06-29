// Top horizontal menu bar — tablet + desktop primary navigation.
// ---------------------------------------------------------------------------
// Replaces the old left rail on tablet+desktop (phones keep the bottom tab bar
// rendered by Rail.tsx). Each nav GROUP becomes a top-level menu item with a
// dropdown of its links (like a classic desktop application's menu bar); the
// untitled first group (Hub / Notifications) renders as direct top-level links.
//
//   [ brand ][ Hub  Notifications  Sales floor▾  Clinical▾  … ][ user menu ]
//
// Reuses the shared nav model + role gating from navConfig.ts so it never drifts
// from the phone drawer. Dropdowns open/close by CLICK/TAP only (identical on
// desktop mouse + iPad touch), are keyboard-accessible (Enter/Space/ArrowDown
// open + focus first item, Esc closes + restores focus), and close on
// outside-click. NOTE: there is deliberately NO hover-open -- this top bar exists
// for the iPad, and a hover-open fighting the click-toggle made a mouse click
// open-then-immediately-close the menu (dead on prod), while touch (no hover)
// left it unreliable. Click is the single, unambiguous trigger.

import { NavLink, useLocation } from 'react-router-dom';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { useAuth } from '../../context/AuthContext';
import { Icon } from './Icon';
import { filterVisibleGroups, type NavItem } from './navConfig';
import { ecommerceSsoApi } from '../../services/api/ecommerceSso';
import { getBrandAssets } from '../../utils/brandAssets';

function MenuChevron() {
  return (
    <svg
      viewBox="0 0 24 24" width={12} height={12}
      fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round"
      className="top-nav-caret" aria-hidden="true"
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

/** A single dropdown link (handles in-app routes, external links, SSO handoff). */
function DropdownItem({ item, onNavigate }: { item: NavItem; onNavigate: () => void }) {
  const IconCmp = Icon[item.icon];
  if (item.external) {
    if (item.sso) {
      return (
        <button
          type="button"
          role="menuitem"
          className="top-nav-dd-item"
          title={`${item.label} (opens in new tab)`}
          onClick={async () => {
            onNavigate();
            try {
              const r = await ecommerceSsoApi.getUrl();
              window.open(r.url, '_blank', 'noopener,noreferrer');
            } catch {
              window.open(item.to, '_blank', 'noopener,noreferrer');
            }
          }}
        >
          <IconCmp />
          <span>{item.label}</span>
        </button>
      );
    }
    return (
      <a
        role="menuitem"
        href={item.to}
        target="_blank"
        rel="noopener noreferrer"
        className="top-nav-dd-item"
        title={`${item.label} (opens in new tab)`}
        onClick={onNavigate}
      >
        <IconCmp />
        <span>{item.label}</span>
      </a>
    );
  }
  return (
    <NavLink
      role="menuitem"
      to={item.to}
      className={({ isActive }) => 'top-nav-dd-item' + (isActive ? ' active' : '')}
      onClick={onNavigate}
    >
      <IconCmp />
      <span>{item.label}</span>
    </NavLink>
  );
}

export function TopNav({ brand = 'bv' }: { brand?: 'bv' | 'wizopt' }) {
  const { user, hasModuleAccess, logout } = useAuth();
  const { pathname } = useLocation();
  const userRoles = user?.roles;
  const activeRole = user?.activeRole;

  // Which top-level dropdown (group title) is open; null = none. Only one at a
  // time. User menu is tracked separately.
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const navRef = useRef<HTMLElement | null>(null);
  const userMenuRef = useRef<HTMLDivElement | null>(null);

  const visibleGroups = useMemo(
    () => filterVisibleGroups(userRoles, activeRole, hasModuleAccess),
    [userRoles, activeRole, hasModuleAccess],
  );

  // The group whose dropdown contains the current route — highlights the parent.
  const activeGroupTitle = useMemo(() => {
    for (const g of visibleGroups) {
      if (!g.title) continue;
      const match = g.items.some((i) => !i.external && (pathname === i.to || pathname.startsWith(i.to + '/')));
      if (match) return g.title;
    }
    return null;
  }, [pathname, visibleGroups]);

  // Close any open menu when the route changes (covers link clicks AND browser
  // back/forward + programmatic nav). React's blessed "adjust state when a value
  // changes" pattern — reset during render, no effect / no extra paint.
  const [lastPath, setLastPath] = useState(pathname);
  if (pathname !== lastPath) {
    setLastPath(pathname);
    setOpenGroup(null);
    setUserMenuOpen(false);
  }

  // Outside-click closes whatever is open.
  useEffect(() => {
    if (!openGroup && !userMenuOpen) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (navRef.current && !navRef.current.contains(t)) {
        setOpenGroup(null);
        setUserMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [openGroup, userMenuOpen]);

  // Esc closes everything (focus stays on the trigger the user pressed Esc on).
  useEffect(() => {
    if (!openGroup && !userMenuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpenGroup(null);
        setUserMenuOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [openGroup, userMenuOpen]);

  const closeAll = useCallback(() => {
    setOpenGroup(null);
    setUserMenuOpen(false);
  }, []);

  const toggleGroup = useCallback((title: string) => {
    setUserMenuOpen(false);
    setOpenGroup((cur) => (cur === title ? null : title));
  }, []);

  const onTriggerKeyDown = useCallback(
    (title: string) => (e: ReactKeyboardEvent<HTMLButtonElement>) => {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        setOpenGroup(title);
        // Focus the first item in the dropdown on the next frame.
        requestAnimationFrame(() => {
          const dd = e.currentTarget.parentElement?.querySelector<HTMLElement>('.top-nav-dropdown [role="menuitem"]');
          dd?.focus();
        });
      }
    },
    [],
  );

  const handleSignOut = async () => {
    setUserMenuOpen(false);
    try { await logout(); } finally { window.location.assign('/login'); }
  };

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
    <nav className="top-nav no-print" aria-label="Primary" ref={navRef}>
      {/* Brand mark + wordmark, pinned left — doubles as the Home link to the Hub
          (the "Hub" top-menu item is dropped below on tablet/desktop since the
          logo provides it; the phone Rail keeps its own Hub tab). */}
      <NavLink
        to="/dashboard"
        className="top-nav-brand"
        title={wordmark}
        aria-label={`${wordmark} — go to Hub`}
        onClick={closeAll}
      >
        <img
          src={brandAssets.markWhite}
          alt={wordmark}
          width={26}
          height={26}
          style={{ objectFit: 'contain', display: 'block' }}
        />
        <span className="top-nav-wordmark" aria-hidden="true">{wordmark}</span>
      </NavLink>

      {/* Menu — direct links + group dropdowns. Scrolls horizontally only if a
          role's menu is too wide for the viewport (brand + user stay pinned). */}
      <ul className="top-nav-menu">
        {visibleGroups.map((group, gi) => {
          // Untitled group (Hub / Notifications) -> direct top-level links.
          // The clickable brand logo above IS the Hub link on tablet/desktop, so
          // drop the redundant "Hub" item here (Notifications stays). navConfig is
          // untouched -> the phone Rail still shows Hub (no clickable logo there).
          if (!group.title) {
            return group.items
              .filter((item) => item.id !== 'hub')
              .map((item) => (
              <li key={item.id} className="top-nav-li">
                <NavLink
                  to={item.to}
                  className={({ isActive }) => 'top-nav-direct' + (isActive ? ' active' : '')}
                  onClick={closeAll}
                >
                  {(() => { const I = Icon[item.icon]; return <I />; })()}
                  <span>{item.label}</span>
                </NavLink>
              </li>
            ));
          }
          const title = group.title;
          const isOpen = openGroup === title;
          const isActive = activeGroupTitle === title;
          return (
            <li
              key={gi}
              className={'top-nav-li top-nav-group' + (isOpen ? ' open' : '')}
            >
              <button
                type="button"
                className={'top-nav-trigger' + (isActive ? ' active' : '')}
                aria-haspopup="true"
                aria-expanded={isOpen}
                onClick={() => toggleGroup(title)}
                onKeyDown={onTriggerKeyDown(title)}
              >
                <span>{title}</span>
                <MenuChevron />
              </button>
              {isOpen && (
                <div className="top-nav-dropdown" role="menu" aria-label={title}>
                  {group.items.map((item) => (
                    <DropdownItem key={item.id} item={item} onNavigate={closeAll} />
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {/* Account / sign-out menu, pinned right */}
      <div className="top-nav-user" ref={userMenuRef}>
        <button
          type="button"
          className="top-nav-avatar-btn"
          aria-haspopup="menu"
          aria-expanded={userMenuOpen}
          title={user?.name ? `${user.name} - click for account` : 'Account'}
          onClick={() => { setOpenGroup(null); setUserMenuOpen((o) => !o); }}
        >
          <span className="top-nav-avatar-initials">{userInitials}</span>
          <span className="top-nav-avatar-name">{user?.name?.split(' ')[0] || 'User'}</span>
          <svg className="top-nav-caret" viewBox="0 0 24 24" width={12} height={12} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
        {userMenuOpen && (
          <div className="top-nav-dropdown top-nav-user-pop" role="menu">
            <div className="top-nav-user-head">
              <span className="top-nav-user-name">{user?.name || 'User'}</span>
              <span className="top-nav-user-role">{(activeRole || '').toString().replaceAll('_', ' ')}</span>
            </div>
            <button
              type="button"
              className="top-nav-signout"
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
      </div>
    </nav>
  );
}
