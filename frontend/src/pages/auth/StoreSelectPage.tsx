// ============================================================================
// IMS 2.0 — Post-login Store Selector (interstitial)
// ----------------------------------------------------------------------------
// A dedicated full-screen step shown AFTER login for multi-store roles so the
// operator confirms which store they are working as BEFORE landing on the
// dashboard. Single-store users never reach the grid: this page auto-proceeds
// when the user has <=1 accessible store, so it is also safe to land on
// directly (the AppLayout guard / a typed URL).
//
// Setting the active store REUSES the existing AuthContext.setActiveStore path
// (which calls authApi.switchStore to re-issue the JWT with the new
// active_store_id) — the SAME mechanism the topbar store pill uses. No parallel
// store-state path is introduced, so geo-fence + store-scoped JWT keep working.
// ============================================================================

import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { storeApi } from '../../services/api';
import { getBrandAssets } from '../../utils/brandAssets';
import {
  accessibleStoresFrom,
  hasNoActiveStore,
  type AccessibleStore,
} from '../../utils/storeAccess';
import { Icon } from '../../components/shell/Icon';

type Phase = 'loading' | 'choose' | 'empty';

export function StoreSelectPage() {
  const { user, setActiveStore, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const from = (location.state as { from?: string })?.from || '/dashboard';

  const [phase, setPhase] = useState<Phase>('loading');
  const [stores, setStores] = useState<AccessibleStore[]>([]);
  // Bumped by "Try again" to re-run the resolve effect after a failed fetch.
  const [retry, setRetry] = useState(0);

  // Resolve accessible stores, then either auto-proceed (<=1) or render the grid
  // (>1). Never auto-navigates into an empty-store loop. The `cancelled` flag
  // makes the StrictMode double-invoke (and unmounts) safe.
  useEffect(() => {
    if (!user) return;

    const proceed = (storeId?: string) => {
      if (storeId && storeId !== user.activeStoreId) {
        setActiveStore(storeId); // reuse the topbar switch-store (JWT re-issue)
      }
      navigate(from, { replace: true });
    };

    let cancelled = false;
    (async () => {
      let accessible: AccessibleStore[];
      try {
        const res: any = await storeApi.getStores();
        const raw = res?.stores ?? res ?? [];
        accessible = accessibleStoresFrom(user, raw);
      } catch {
        // Network/permission failure: fall back to the ids on the user object so
        // a non-admin can still pick from their assignment. An all-stores admin
        // with no fallback ids lands on the empty state (with Retry) rather than
        // bouncing in a redirect loop.
        accessible = (user.storeIds || []).map((id) => ({ id, name: id, code: id }));
      }
      if (cancelled) return;

      if (accessible.length >= 2) {
        setStores(accessible);
        setPhase('choose');
        return;
      }
      if (accessible.length === 1) {
        proceed(accessible[0].id);
        return;
      }
      // Zero accessible stores.
      if (!hasNoActiveStore(user)) {
        // Already operating with a store somehow — don't trap them here.
        proceed();
        return;
      }
      setPhase('empty');
    })();

    return () => {
      cancelled = true;
    };
    // Keyed on the user identity (not the whole object) so a setActiveStore
    // dispatch — which produces a new user reference — doesn't re-trigger a fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, retry]);

  // Login uses the Better Vision house lockup; mirror it here for visual parity.
  const brand = getBrandAssets('bv');
  const roleLabel = (user?.activeRole || '').toString().replaceAll('_', ' ');

  const handlePick = (storeId: string) => {
    if (storeId !== user?.activeStoreId) {
      setActiveStore(storeId); // EXISTING mechanism — re-issues the JWT
    }
    navigate(from, { replace: true });
  };

  const handleSignOut = async () => {
    try {
      await logout();
    } finally {
      navigate('/login', { replace: true });
    }
  };

  // ---- Loading / auto-proceed splash --------------------------------------
  if (phase === 'loading') {
    return (
      <div style={shellStyle}>
        <div style={{ textAlign: 'center' }} role="status" aria-live="polite">
          <div
            className="w-10 h-10 border-4 border-bv-red-600 border-t-transparent rounded-full animate-spin mx-auto"
            aria-hidden="true"
          />
          <p style={{ marginTop: 16, color: 'var(--ink-3, #4a4a45)', fontSize: 14 }}>
            Preparing your stores…
          </p>
        </div>
      </div>
    );
  }

  // ---- No accessible store (degenerate / fetch failed) --------------------
  if (phase === 'empty') {
    return (
      <div style={shellStyle}>
        <div style={{ ...cardStyle, textAlign: 'center' }}>
          <div style={iconBadgeStyle} aria-hidden="true">
            <Icon.store width={22} height={22} />
          </div>
          <h1 className="display" style={{ fontSize: 24, color: 'var(--ink)', margin: '4px 0 8px' }}>
            No store available
          </h1>
          <p style={{ color: 'var(--ink-3, #4a4a45)', fontSize: 14, lineHeight: 1.5, marginBottom: 20 }}>
            Your account isn't assigned to an active store yet. Please ask your
            administrator to assign you a store, then sign in again.
          </p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
            <button
              type="button"
              onClick={() => {
                setPhase('loading');
                setRetry((r) => r + 1);
              }}
              style={secondaryBtnStyle}
            >
              Try again
            </button>
            <button type="button" onClick={handleSignOut} style={primaryBtnStyle}>
              Sign out
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---- Choose a store ------------------------------------------------------
  return (
    <div style={shellStyle}>
      <div style={{ width: '100%', maxWidth: 760 }}>
        <div style={{ textAlign: 'center', marginBottom: 28 }}>
          <img
            src={brand.lockup}
            alt={brand.name}
            style={{ height: 44, width: 'auto', maxWidth: 200, margin: '0 auto 18px', objectFit: 'contain' }}
          />
          <h1 className="display" style={{ fontSize: 30, color: 'var(--ink)', margin: '0 0 6px' }}>
            Choose your store
          </h1>
          <p style={{ color: 'var(--ink-3, #4a4a45)', fontSize: 14.5 }}>
            {user?.name ? <strong style={{ color: 'var(--ink-2)' }}>{user.name}</strong> : 'Signed in'}
            {roleLabel ? <span> · {roleLabel}</span> : null}
            <span> — select the store you're operating as.</span>
          </p>
        </div>

        <div
          role="listbox"
          aria-label="Select store"
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
            gap: 14,
          }}
        >
          {stores.map((s) => {
            const isCurrent = s.id === user?.activeStoreId;
            const meta = [s.city, s.brand].filter(Boolean).join(' · ');
            return (
              <button
                key={s.id}
                type="button"
                role="option"
                aria-selected={isCurrent}
                onClick={() => handlePick(s.id)}
                style={{
                  ...storeCardStyle,
                  borderColor: isCurrent ? 'var(--bv)' : 'var(--line)',
                  background: isCurrent ? 'var(--bv-50)' : 'var(--surface)',
                }}
                onMouseEnter={(e) => {
                  if (!isCurrent) e.currentTarget.style.borderColor = 'var(--line-strong)';
                }}
                onMouseLeave={(e) => {
                  if (!isCurrent) e.currentTarget.style.borderColor = 'var(--line)';
                }}
              >
                <span style={{ ...iconBadgeStyle, width: 40, height: 40, margin: 0, flexShrink: 0 }} aria-hidden="true">
                  <Icon.store width={18} height={18} />
                </span>
                <span style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span
                      style={{
                        fontWeight: 600,
                        fontSize: 15,
                        color: 'var(--ink)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {s.name}
                    </span>
                    {isCurrent && (
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: '0.04em',
                          textTransform: 'uppercase',
                          color: 'var(--bv)',
                          background: 'var(--surface)',
                          border: '1px solid var(--bv)',
                          borderRadius: 999,
                          padding: '1px 7px',
                          flexShrink: 0,
                        }}
                      >
                        Current
                      </span>
                    )}
                  </span>
                  {s.code && (
                    <span className="mono" style={{ display: 'block', fontSize: 11.5, color: 'var(--ink-4)', marginTop: 2 }}>
                      {s.code}
                    </span>
                  )}
                  {meta && (
                    <span style={{ display: 'block', fontSize: 12.5, color: 'var(--ink-3, #4a4a45)', marginTop: 2 }}>
                      {meta}
                    </span>
                  )}
                </span>
                <Icon.chevron width={16} height={16} aria-hidden="true" />
              </button>
            );
          })}
        </div>

        <div style={{ textAlign: 'center', marginTop: 26 }}>
          <button
            type="button"
            onClick={handleSignOut}
            style={{
              background: 'transparent',
              border: 0,
              color: 'var(--ink-4)',
              fontSize: 13,
              cursor: 'pointer',
              padding: 6,
            }}
          >
            Not you? Sign out
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline style objects (CSS-variable driven, matching the shell tokens).
// ---------------------------------------------------------------------------
const shellStyle: React.CSSProperties = {
  minHeight: '100vh',
  background: 'var(--bg)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 24,
};

const cardStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: 440,
  background: 'var(--surface)',
  border: '1px solid var(--line)',
  borderRadius: 'var(--r-lg, 12px)',
  boxShadow: 'var(--sh-md)',
  padding: 32,
};

const iconBadgeStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 48,
  height: 48,
  borderRadius: 'var(--r-md, 8px)',
  background: 'var(--bv-50)',
  color: 'var(--bv)',
  margin: '0 auto 14px',
};

const storeCardStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 12,
  width: '100%',
  padding: 14,
  borderRadius: 'var(--r-lg, 12px)',
  border: '1px solid var(--line)',
  background: 'var(--surface)',
  boxShadow: 'var(--sh-md)',
  cursor: 'pointer',
  color: 'var(--ink-2)',
  textAlign: 'left',
  transition: 'border-color 120ms ease',
};

const primaryBtnStyle: React.CSSProperties = {
  padding: '9px 18px',
  borderRadius: 8,
  border: 0,
  background: 'var(--bv)',
  color: '#fff',
  font: '600 13px var(--font-sans)',
  cursor: 'pointer',
};

const secondaryBtnStyle: React.CSSProperties = {
  padding: '9px 18px',
  borderRadius: 8,
  border: '1px solid var(--line-strong, #d8d8d5)',
  background: 'var(--surface)',
  color: 'var(--ink-2)',
  font: '600 13px var(--font-sans)',
  cursor: 'pointer',
};

export default StoreSelectPage;
