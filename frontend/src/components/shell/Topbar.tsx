// 52px top bar — breadcrumbs, ⌘K command palette trigger, store pill, role pill,
// notifications bell, and page-level actions.
// Ported from design_handoff_ims_2_0/shell/shell.jsx → Topbar

import { useState, useRef, useEffect } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { storeApi } from '../../services/api';
import { isOnlineStore } from '../../utils/storeMode';
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

// A store's id SHOULD be its human code (BV-BOK-01). A legacy store created
// before that convention can carry a raw uuid as its id, which must never be
// shown to a user. This matches a uuid v4-ish string (8-4-4-4-12 hex) so we can
// hide it behind the store NAME instead.
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const looksLikeUuid = (v?: string | null): boolean => !!v && UUID_RE.test(v.trim());

// Friendly label for a store id: prefer the store NAME from the org list; never
// surface a raw uuid. Falls back to the id only when it's a human code.
function friendlyStoreLabel(
  id: string | null | undefined,
  storeNames: Record<string, string>,
): string {
  if (!id) return 'No store';
  const name = storeNames[id];
  if (name) return name;
  return looksLikeUuid(id) ? 'Store' : id;
}

// OS-029: small "Online" pill rendered next to an ONLINE store's name in the
// store pill + dropdown (lifted from the OrganizationPage store_type badge —
// same information, shell-token styling).
const onlineBadgeStyle: React.CSSProperties = {
  fontSize: 9.5,
  fontWeight: 700,
  letterSpacing: '0.05em',
  textTransform: 'uppercase',
  color: 'var(--info, #2563eb)',
  background: 'var(--info-50, #eff6ff)',
  border: '1px solid currentColor',
  borderRadius: 999,
  padding: '0 6px',
  lineHeight: '14px',
  flexShrink: 0,
};

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
  // OS-029: store_type per id (from the same GET /stores response) so ONLINE
  // stores are badged in the pill + dropdown instead of masquerading as shops.
  const [storeTypes, setStoreTypes] = useState<Record<string, string>>({});
  const [roleOpen, setRoleOpen] = useState(false);
  const [storeOpen, setStoreOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Store-switch confirmation: switching store clears the current POS cart +
  // any in-progress forms (the context/JWT is re-issued). Warn before doing it.
  const [pendingStoreId, setPendingStoreId] = useState<string | null>(null);
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
        const types: Record<string, string> = {};
        stores.forEach((s: any) => {
          const id = s.store_id || s.id || s._id;
          const name = s.store_name || s.storeName || s.name;
          if (id && name) map[id] = name;
          if (id && s.store_type) types[id] = String(s.store_type);
        });
        setStoreNames(map);
        setStoreTypes(types);
      })
      .catch(() => {});
  }, []);

  // OS-029: ONLINE badge for a store id — prefers the fetched store_type, with
  // the known-id fast-path (works before the store list loads).
  const storeIsOnline = (id: string | null | undefined): boolean =>
    !!id && isOnlineStore({ id, store_type: storeTypes[id] });

  const activeStoreName = friendlyStoreLabel(user?.activeStoreId, storeNames);
  // The little mono "code" line under the name: show the code only when it's a
  // real human code -- a raw uuid id is hidden (the name already identifies it).
  const activeStoreCode = looksLikeUuid(user?.activeStoreId)
    ? ''
    : user?.activeStoreId || '';
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
        aria-expanded={navOpen}
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
        /* Centered store pill. A leading .spacer (before this block) + a
           trailing .spacer (after it) flank the pill so it sits in the centre
           of the bar IN FLOW -- not absolutely positioned. The old approach
           (position:absolute; left:50%) anchored to the page and was painted
           over by DOM-later siblings (search, role pill, bell) -- invisible on
           mobile, partially covered on desktop. Flow + flanking spacers can't
           be overpainted and reserve their own space. */
        <>
        <div ref={storeRef} style={{ position: 'relative', flexShrink: 0 }}>
          <button
            type="button"
            className="store-pill"
            /* width is responsive via .store-pill in index.css (wider on
               tablet/desktop); keep only the non-width tweaks inline */
            style={{ height: 42, padding: '0 20px', borderRadius: 12, justifyContent: 'center' }}
            onClick={() => setStoreOpen((o) => !o)}
            aria-haspopup="listbox"
            aria-expanded={storeOpen}
          >
            <span className="dot" style={{ width: 10, height: 10 }} />
            {/* On mobile (<sm) show just the store code to prevent wrapping;
                on desktop show the full store name + code. */}
            <span className="name" style={{ fontSize: 16 }}>{activeStoreName}</span>
            <span className="code" style={{ fontSize: 12 }}>{activeStoreCode}</span>
            {storeIsOnline(user?.activeStoreId) && (
              <span style={onlineBadgeStyle}>Online</span>
            )}
            <Icon.chevronDown width={12} height={12} />
          </button>
          {storeOpen && (
            <div
              role="listbox"
              aria-label="Select store"
              style={{
                position: 'absolute',
                top: '100%',
                right: 0,
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
              {/* Store options source (data-consistency fix): an all-stores role
                  (SUPERADMIN/ADMIN/AREA_MANAGER) picks from the ORG store list
                  (GET /stores -> storeNames), not the per-user store_ids assignment
                  -- which for an all-stores admin is often empty, leaving "No store"
                  and a POS "No Store Selected" dead-end. Store-level roles still see
                  only their assigned stores. */}
              {(hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'])
                ? (Object.keys(storeNames).length ? Object.keys(storeNames) : (user?.storeIds || []))
                : (user?.storeIds?.length ? user.storeIds : Object.keys(storeNames))
              ).map((id) => (
                <button
                  key={id}
                  type="button"
                  role="option"
                  aria-selected={id === user?.activeStoreId}
                  onClick={() => {
                    setStoreOpen(false);
                    // Same store -> no-op. Different store -> confirm first
                    // (switching clears the POS cart + in-progress forms).
                    if (id === user?.activeStoreId) return;
                    setPendingStoreId(id);
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
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span
                      style={{
                        minWidth: 0,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {friendlyStoreLabel(id, storeNames)}
                    </span>
                    {storeIsOnline(id) && <span style={onlineBadgeStyle}>Online</span>}
                  </div>
                  <div className="mono" style={{ color: 'var(--ink-4)', fontSize: 10.5 }}>
                    {looksLikeUuid(id) ? '' : id}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
        {/* Trailing flex spacer -> the leading + trailing spacers centre the
            pill in the bar while the right-side controls stay flush right. */}
        <div className="spacer" />
        </>
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
            aria-expanded={roleOpen}
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
                  aria-selected={r === user.activeRole}
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

      {pendingStoreId && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Confirm store switch"
          onClick={() => setPendingStoreId(null)}
          style={{
            position: 'fixed', inset: 0, zIndex: 200,
            background: 'rgba(17,17,17,0.45)',
            display: 'grid', placeItems: 'center', padding: 16,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 'min(440px, 100%)', background: 'var(--surface)',
              border: '1px solid var(--line)', borderRadius: 'var(--r-md, 12px)',
              boxShadow: 'var(--sh-md)', padding: 20,
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--ink)', marginBottom: 8 }}>
              Switch store?
            </div>
            <div style={{ fontSize: 13.5, color: 'var(--ink-2)', lineHeight: 1.5, marginBottom: 18 }}>
              You're switching to <strong>{friendlyStoreLabel(pendingStoreId, storeNames)}</strong>.
              Any unsaved work on the current store — your <strong>POS cart</strong> and any open forms —
              will be cleared and cannot be recovered. Save first if you need it.
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                type="button"
                onClick={() => setPendingStoreId(null)}
                style={{
                  padding: '8px 16px', borderRadius: 8, minHeight: 40,
                  border: '1px solid var(--line)', background: 'var(--surface)',
                  color: 'var(--ink-2)', font: '600 13px var(--font-sans)', cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => { const id = pendingStoreId; setPendingStoreId(null); setActiveStore(id); }}
                style={{
                  padding: '8px 16px', borderRadius: 8, minHeight: 40, border: 0,
                  background: 'var(--bv)', color: '#fff',
                  font: '600 13px var(--font-sans)', cursor: 'pointer',
                }}
              >
                Switch store
              </button>
            </div>
          </div>
        </div>
      )}

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </header>
  );
}
