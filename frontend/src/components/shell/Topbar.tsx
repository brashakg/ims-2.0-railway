// 52px top bar — breadcrumbs, ⌘K command palette trigger, store pill, role pill,
// notifications bell, and page-level actions.
// Ported from design_handoff_ims_2_0/shell/shell.jsx → Topbar

import { useState, useRef, useEffect } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { storeApi } from '../../services/api';
import { Icon } from './Icon';
import { NotificationBell } from './NotificationBell';
import { CommandPalette } from './CommandPalette';

export interface Crumb {
  label: string;
  to?: string;
}

interface TopbarProps {
  crumbs?: Crumb[];
  actions?: ReactNode;
  /** Called when the mobile hamburger is tapped. */
  onHamburgerClick?: () => void;
  /** Whether the mobile nav drawer is open (controls aria-expanded). */
  navOpen?: boolean;
}

function useClickOutside(ref: React.RefObject<HTMLElement | null>, onOutside: () => void) {
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onOutside();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [ref, onOutside]);
}

export function Topbar({ crumbs = [], actions, onHamburgerClick, navOpen = false }: TopbarProps) {
  const navigate = useNavigate();
  const { user, setActiveRole, setActiveStore, hasRole } = useAuth();
  const [storeNames, setStoreNames] = useState<Record<string, string>>({});
  const [roleOpen, setRoleOpen] = useState(false);
  const [storeOpen, setStoreOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const roleRef = useRef<HTMLDivElement>(null);
  const storeRef = useRef<HTMLDivElement>(null);

  useClickOutside(roleRef, () => setRoleOpen(false));
  useClickOutside(storeRef, () => setStoreOpen(false));

  // Cmd/Ctrl+K opens the global command palette. Replaces the old Phase 6.13
  // stub that just navigated to /customers - see CommandPalette.tsx for the
  // proper implementation (cross-entity search + keyboard navigation).
  const openGlobalSearch = () => {
    setPaletteOpen(true);
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => {
    storeApi
      .getStores()
      .then((res: any) => {
        const stores = res?.stores || res || [];
        if (!Array.isArray(stores)) return;
        const map: Record<string, string> = {};
        stores.forEach((s: any) => {
          const id = s.store_id || s.id || s._id;
          const name = s.store_name || s.storeName || s.name;
          if (id && name) map[id] = name;
        });
        setStoreNames(map);
      })
      .catch(() => {});
  }, []);

  const activeStoreName =
    (user?.activeStoreId && storeNames[user.activeStoreId]) || user?.activeStoreId || 'No store';
  const roleLabel = (user?.activeRole || '—').toString().replaceAll('_', ' ');
  const multiStore =
    hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']) ||
    ((user?.storeIds?.length ?? 0) > 1 && !hasRole(['OPTOMETRIST']));

  return (
    <header className="topbar no-print">
      {/* Mobile-only hamburger — hidden on ≥768px via CSS */}
      <button
        type="button"
        className="topbar-hamburger"
        aria-label={navOpen ? 'Close navigation' : 'Open navigation'}
        aria-expanded={navOpen ? "true" : "false"}
        aria-controls="rail-drawer"
        onClick={onHamburgerClick}
      >
        {/* Three horizontal lines, no external deps */}
        <span className="topbar-hamburger-icon">
          <span /><span /><span />
        </span>
      </button>

      <nav className="crumbs" aria-label="Breadcrumb">
        {crumbs.map((c, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {i > 0 && (
              <span className="sep">
                <Icon.chevron width={12} height={12} />
              </span>
            )}
            {c.to && i < crumbs.length - 1 ? (
              <button
                type="button"
                onClick={() => navigate(c.to!)}
                style={{ background: 'transparent', border: 0, color: 'inherit', cursor: 'pointer', padding: 0, font: 'inherit' }}
              >
                {c.label}
              </button>
            ) : (
              <span className={i === crumbs.length - 1 ? 'cur' : ''}>{c.label}</span>
            )}
          </span>
        ))}
      </nav>

      <div className="spacer" />

      {multiStore && (
        <div ref={storeRef} style={{ position: 'absolute', left: '50%', transform: 'translateX(-50%)' }}>
          <button
            type="button"
            className="store-pill"
            style={{ height: 42, padding: '0 16px', borderRadius: 12 }}
            onClick={() => setStoreOpen((o) => !o)}
            aria-haspopup="listbox"
            aria-expanded={storeOpen ? "true" : "false"}
          >
            <span className="dot" style={{ width: 10, height: 10 }} />
            {/* On mobile (<sm) show just the store code to prevent wrapping;
                on desktop show the full store name + code. */}
            <span className="name" style={{ fontSize: 16 }}>{activeStoreName}</span>
            <span className="code" style={{ fontSize: 12 }}>{user?.activeStoreId || ''}</span>
            <Icon.chevronDown width={12} height={12} />
          </button>
          {storeOpen && (
            <div
              role="listbox"
              aria-label="Select store"
              style={{
                position: 'absolute',
                top: '100%',
                left: '50%',
                transform: 'translateX(-50%)',
                marginTop: 6,
                width: 240,
                maxWidth: 'calc(100vw - 16px)',
                maxHeight: 'min(70vh, 360px)',
                overflowY: 'auto',
                background: 'var(--surface)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-md)',
                boxShadow: 'var(--sh-md)',
                zIndex: 60,
                padding: 4,
              }}
            >
              {(user?.storeIds?.length ? user.storeIds : Object.keys(storeNames)).map((id) => (
                <button
                  key={id}
                  type="button"
                  role="option"
                  aria-selected={id === user?.activeStoreId ? "true" : "false"}
                  onClick={() => {
                    setActiveStore(id);
                    setStoreOpen(false);
                  }}
                  style={{
                    width: '100%',
                    textAlign: 'left',
                    padding: '8px 10px',
                    minHeight: 40,
                    borderRadius: 6,
                    border: 0,
                    background: id === user?.activeStoreId ? 'var(--bv-50)' : 'transparent',
                    color: id === user?.activeStoreId ? 'var(--bv)' : 'var(--ink-2)',
                    font: '500 12.5px var(--font-sans)',
                    cursor: 'pointer',
                  }}
                >
                  <div>{storeNames[id] || id}</div>
                  <div className="mono" style={{ color: 'var(--ink-4)', fontSize: 10.5 }}>
                    {id}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <button
        className="cmdk"
        type="button"
        aria-label="Search or jump to…"
        onClick={openGlobalSearch}
      >
        <Icon.search width={14} height={14} />
        <span>Search or jump to…</span>
        <span className="kbd">⌘K</span>
      </button>

      {user && (user.roles?.length ?? 0) > 1 ? (
        <div ref={roleRef} style={{ position: 'relative' }}>
          <button
            type="button"
            className="role-pill"
            onClick={() => setRoleOpen((o) => !o)}
            style={{ cursor: 'pointer', border: 0 }}
            aria-haspopup="listbox"
            aria-expanded={roleOpen ? "true" : "false"}
          >
            <span className="k">Role</span>
            {roleLabel}
            <Icon.chevronDown width={12} height={12} />
          </button>
          {roleOpen && (
            <div
              role="listbox"
              aria-label="Select role"
              style={{
                position: 'absolute',
                top: '100%',
                right: 0,
                marginTop: 6,
                width: 200,
                background: 'var(--surface)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-md)',
                boxShadow: 'var(--sh-md)',
                zIndex: 60,
                padding: 4,
              }}
            >
              {(user.roles || []).map((r) => (
                <button
                  key={r}
                  type="button"
                  role="option"
                  aria-selected={r === user.activeRole ? "true" : "false"}
                  onClick={() => {
                    setActiveRole(r);
                    setRoleOpen(false);
                  }}
                  style={{
                    width: '100%',
                    textAlign: 'left',
                    padding: '8px 10px',
                    borderRadius: 6,
                    border: 0,
                    background: r === user.activeRole ? 'var(--bv-50)' : 'transparent',
                    color: r === user.activeRole ? 'var(--bv)' : 'var(--ink-2)',
                    font: '500 12.5px var(--font-sans)',
                    cursor: 'pointer',
                  }}
                >
                  {r.replaceAll('_', ' ')}
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        <span className="role-pill">
          <span className="k">Role</span>
          {roleLabel}
        </span>
      )}

      <NotificationBell />

      {actions}

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </header>
  );
}
