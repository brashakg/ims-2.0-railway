// 52px top bar — breadcrumbs, ⌘K command palette trigger, store pill, role pill,
// notifications bell, and page-level actions.
// Ported from design_handoff_ims_2_0/shell/shell.jsx → Topbar

import { useState, useRef, useEffect } from 'react';
import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { storeApi } from '../../services/api';
import { Icon } from './Icon';

export interface Crumb {
  label: string;
  to?: string;
}

interface TopbarProps {
  crumbs?: Crumb[];
  actions?: ReactNode;
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

export function Topbar({ crumbs = [], actions }: TopbarProps) {
  const navigate = useNavigate();
  const { user, setActiveRole, setActiveStore, hasRole } = useAuth();
  const [storeNames, setStoreNames] = useState<Record<string, string>>({});
  const [roleOpen, setRoleOpen] = useState(false);
  const [storeOpen, setStoreOpen] = useState(false);
  const roleRef = useRef<HTMLDivElement>(null);
  const storeRef = useRef<HTMLDivElement>(null);

  useClickOutside(roleRef, () => setRoleOpen(false));
  useClickOutside(storeRef, () => setStoreOpen(false));

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

      <button className="cmdk" type="button" aria-label="Search or jump to...">
        <Icon.search width={14} height={14} />
        <span>Search or jump to…</span>
        <span className="kbd">⌘K</span>
      </button>

      {multiStore && (
        <div ref={storeRef} style={{ position: 'relative' }}>
          <button
            type="button"
            className="store-pill"
            onClick={() => setStoreOpen((o) => !o)}
            aria-haspopup="listbox"
            aria-expanded={storeOpen}
          >
            <span className="dot" />
            <span>{activeStoreName}</span>
            {user?.activeStoreId && <span className="code">· {user.activeStoreId}</span>}
            <Icon.chevronDown width={12} height={12} />
          </button>
          {storeOpen && (
            <div
              role="listbox"
              style={{
                position: 'absolute',
                top: '100%',
                right: 0,
                marginTop: 6,
                width: 240,
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
                  onClick={() => {
                    setActiveStore(id);
                    setStoreOpen(false);
                  }}
                  style={{
                    width: '100%',
                    textAlign: 'left',
                    padding: '8px 10px',
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

      <button className="btn icon ghost" type="button" title="Notifications" aria-label="Notifications">
        <Icon.bell />
      </button>

      {actions}
    </header>
  );
}
