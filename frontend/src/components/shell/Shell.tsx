// Shell wrapper — TopNav (tablet+desktop) / Rail (phone) + Topbar + page body.
// ---------------------------------------------------------------------------
// Tablet+desktop: a vertical stack — [TopNav horizontal menu] over [Topbar row]
// over [full-width page body]. The Rail is hidden here.
// Phone (≤767): the TopNav is hidden; the Rail reflows to a bottom tab bar and
// the topbar hamburger opens it as a left drawer (unchanged behaviour).
// Used by AppLayout; also usable directly if a page needs custom crumbs/actions.

import { type ReactNode, useState, useEffect, useCallback, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import { Rail } from './Rail';
import { TopNav } from './TopNav';
import { Topbar, type Crumb } from './Topbar';
import { useAppearance } from '../../context/AppearanceContext';

interface ShellProps {
  crumbs?: Crumb[];
  actions?: ReactNode;
  brand?: 'bv' | 'wizopt';
  children: ReactNode;
}

export function Shell({ crumbs, actions, brand, children }: ShellProps) {
  const { railExpanded } = useAppearance();
  const location = useLocation();
  // navOpen drives the PHONE drawer only (the Rail rendered as a left overlay).
  const [navOpen, setNavOpen] = useState(false);
  // Track last path so we only close on an actual navigation, not initial mount.
  const lastPath = useRef(location.pathname);

  const closeNav = useCallback(() => setNavOpen(false), []);

  // Close drawer on route change (covers NavLink clicks inside Rail).
  useEffect(() => {
    if (location.pathname !== lastPath.current) {
      lastPath.current = location.pathname;
      setNavOpen(false);
    }
  }, [location.pathname]);

  // Close on Esc.
  useEffect(() => {
    if (!navOpen) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') closeNav(); };
    document.addEventListener('keydown', handler);
    // Prevent body scroll while drawer is open.
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handler);
      document.body.style.overflow = '';
    };
  }, [navOpen, closeNav]);

  return (
    <div
      className={'app-shell' + (railExpanded ? ' rail-expanded' : '')}
      data-brand={brand ?? 'bv'}
    >
      {/* Skip to main content link for keyboard navigation */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:bg-bv-red-600 focus:text-white focus:px-3 focus:py-2 focus:rounded focus:text-sm focus:font-medium"
      >
        Skip to main content
      </a>

      {/* Mobile nav backdrop — click to close the phone drawer; hidden ≥768 */}
      {navOpen && (
        <div
          className="mobile-nav-backdrop"
          aria-hidden="true"
          onClick={closeNav}
        />
      )}

      {/* Tablet + desktop primary nav (hidden on phones via CSS). */}
      <TopNav brand={brand} />

      {/* Phone bottom tab bar + hamburger drawer (hidden ≥768 via CSS). */}
      <Rail brand={brand} mobileOpen={navOpen} />

      <div className="app-main">
        <Topbar
          crumbs={crumbs}
          actions={actions}
          onHamburgerClick={() => setNavOpen((o) => !o)}
          navOpen={navOpen}
        />
        <div id="main-content" className="page-body">{children}</div>
      </div>
    </div>
  );
}
