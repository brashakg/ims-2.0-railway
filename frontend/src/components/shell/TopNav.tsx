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
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { useAuth } from '../../context/AuthContext';
import { Icon } from './Icon';
import { NavBadge } from './NavBadge';
import { filterVisibleGroups, type NavItem } from './navConfig';
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
      {item.badge && <NavBadge badge={item.badge} />}
    </NavLink>
  );
}

/** Anchor a fixed-positioned group dropdown to its trigger's viewport rect,
 *  clamped so a right-edge menu doesn't spill past the viewport. */
function dropdownAnchor(el: HTMLElement): { left: number; top: number } {
  const r = el.getBoundingClientRect();
  const left = Math.max(8, Math.min(Math.round(r.left), window.innerWidth - 240));
  return { left, top: Math.round(r.bottom + 2) };
}

export function TopNav({ brand = 'bv' }: { brand?: 'bv' | 'wizopt' }) {
  const { user, hasModuleAccess, logout } = useAuth();
  const { pathname } = useLocation();
  const userRoles = user?.roles;
  const activeRole = user?.activeRole;

  // Which top-level dropdown (group title) is open; null = none. Only one at a
  // time. User menu is tracked separately.
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  // The group dropdown is rendered position:FIXED and anchored to its trigger's
  // viewport rect. Why fixed: the menu list is `overflow-x: auto` (so a wide
  // role menu can scroll horizontally), and per CSS that forces overflow-y to
  // clip too -- an absolutely-positioned dropdown hanging below the 52px bar got
  // clipped to nothing (the menu opened in the DOM but was invisible). A fixed
  // anchor escapes the ancestor's overflow clip. null = use no inline position.
  const [ddPos, setDdPos] = useState<{ left: number; top: number } | null>(null);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const navRef = useRef<HTMLElement | null>(null);
  const userMenuRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLUListElement | null>(null);
  // R38: which ends of the menu bar have groups scrolled out of sight. The bar
  // hides its scrollbar, so on a tablet a role with many groups needs a
  // visible arrow (mouse without a sideways wheel; keyboard focus scrolls by
  // itself, touch can swipe).
  const [hidden, setHidden] = useState({ before: false, after: false });
  const measureMenu = useCallback(() => {
    const ul = menuRef.current;
    if (!ul) return;
    const before = ul.scrollLeft > 1;
    const after = ul.scrollLeft + ul.clientWidth < ul.scrollWidth - 1;
    setHidden((h) => (h.before === before && h.after === after ? h : { before, after }));
  }, []);
  const scrollMenu = (dir: 1 | -1) => {
    const ul = menuRef.current;
    if (!ul) return;
    ul.scrollLeft += dir * Math.max(120, Math.round(ul.clientWidth * 0.7));
    measureMenu();
  };

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

  // Esc closes everything. Focus that was inside the open group's menu goes
  // back to that group's trigger (R11), never to the page body.
  useEffect(() => {
    if (!openGroup && !userMenuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        const trig = navRef.current?.querySelector<HTMLElement>('.top-nav-group.open > .top-nav-trigger');
        if (trig && trig.parentElement?.contains(document.activeElement)) trig.focus();
        setOpenGroup(null);
        setUserMenuOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [openGroup, userMenuOpen]);

  // The group dropdown is position:fixed (anchored to its trigger), so it would
  // visually detach if the page scrolls, the window resizes, or the menu scrolls
  // horizontally. Close it on any of those instead of trying to re-track.
  // Scroll is captured (scroll doesn't bubble) but only closes when the thing that
  // scrolled CONTAINS the open trigger, i.e. the anchor actually moved. Scrolling
  // INSIDE the dropdown, or the page body below the fixed bar, leaves it open --
  // closing on every captured scroll made a long menu impossible to scroll.
  useEffect(() => {
    if (!openGroup) return;
    const close = () => setOpenGroup(null);
    const onScroll = (e: Event) => {
      const anchor = navRef.current?.querySelector('.top-nav-group.open');
      if (anchor && e.target instanceof Node && e.target.contains(anchor)) close();
    };
    window.addEventListener('resize', close);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [openGroup]);

  const closeAll = useCallback(() => {
    setOpenGroup(null);
    setUserMenuOpen(false);
  }, []);

  const toggleGroup = useCallback((title: string, el: HTMLElement) => {
    setUserMenuOpen(false);
    setDdPos(dropdownAnchor(el));
    setOpenGroup((cur) => (cur === title ? null : title));
  }, []);

  const onTriggerKeyDown = useCallback(
    (title: string) => (e: ReactKeyboardEvent<HTMLButtonElement>) => {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        // R11: read the element NOW -- React clears e.currentTarget once this
        // handler returns, so reading it inside the frame callback threw.
        const li = e.currentTarget.parentElement;
        setDdPos(dropdownAnchor(e.currentTarget));
        setOpenGroup(title);
        // Focus the first item in the dropdown on the next frame.
        requestAnimationFrame(() => {
          li?.querySelector<HTMLElement>('.top-nav-dropdown [role="menuitem"]')?.focus();
        });
      }
    },
    [],
  );

  // R11: inside an open menu the arrows move between its entries (wrapping),
  // Home/End jump to the ends; Tab and Esc keep their usual meaning.
  const onMenuKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'));
    if (!items.length) return;
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next =
      e.key === 'ArrowDown' ? (at + 1) % items.length
      : e.key === 'ArrowUp' ? (at <= 0 ? items.length - 1 : at - 1)
      : e.key === 'Home' ? 0
      : e.key === 'End' ? items.length - 1
      : -1;
    if (next < 0) return;
    e.preventDefault();
    items[next].focus();
  };

  // Re-measure the menu bar when its width or its groups change.
  useLayoutEffect(() => {
    measureMenu();
    const ul = menuRef.current;
    window.addEventListener('resize', measureMenu);
    const ro = typeof ResizeObserver !== 'undefined' && ul ? new ResizeObserver(measureMenu) : null;
    if (ro && ul) ro.observe(ul);
    return () => {
      window.removeEventListener('resize', measureMenu);
      ro?.disconnect();
    };
  }, [measureMenu, visibleGroups]);

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
      {hidden.before && (
        <button type="button" className="top-nav-scroll" aria-label="Show earlier menus" onClick={() => scrollMenu(-1)}>
          <MenuChevron />
        </button>
      )}
      <ul className="top-nav-menu" ref={menuRef} onScroll={measureMenu}>
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
                onClick={(e) => toggleGroup(title, e.currentTarget)}
                onKeyDown={onTriggerKeyDown(title)}
              >
                <span>{title}</span>
                <MenuChevron />
              </button>
              {isOpen && (
                <div
                  className="top-nav-dropdown"
                  role="menu"
                  aria-label={title}
                  onKeyDown={onMenuKeyDown}
                  // Fixed + JS-anchored so the menu's overflow-x clip can't hide it.
                  // Height is capped to the VISIBLE viewport below the trigger (dvh, not
                  // vh): on a tablet/phone the layout viewport runs under the browser
                  // toolbar, so a 72vh box could end behind it and its last rows were
                  // unreachable while the box itself believed it fit.
                  style={ddPos ? { position: 'fixed', left: ddPos.left, top: ddPos.top, margin: 0, maxHeight: `calc(100dvh - ${ddPos.top}px - 12px)` } : undefined}
                >
                  {/* Section headings only on a long list (6+ rows for this
                      role). aria-hidden: a role=menu may only hold menuitems,
                      and the rows already say what they are. */}
                  {group.items.map((item, i) => (
                    <Fragment key={item.id}>
                      {group.items.length >= 6 && item.section && item.section !== group.items[i - 1]?.section && (
                        <div className="top-nav-dd-heading" aria-hidden="true">{item.section}</div>
                      )}
                      <DropdownItem item={item} onNavigate={closeAll} />
                    </Fragment>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {hidden.after && (
        <button type="button" className="top-nav-scroll after" aria-label="Show more menus" onClick={() => scrollMenu(1)}>
          <MenuChevron />
        </button>
      )}

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
