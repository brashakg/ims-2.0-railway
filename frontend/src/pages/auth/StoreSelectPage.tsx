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
//
// Layout (design 2026-08-30, built 2026-09-04): content-sized and top-aligned;
// "Where you were" first (Enter); shops grouped by the brand they trade as
// (the store doc's `brand`: BETTER_VISION / WIZOPT); ONLINE stores in their own
// section with the storefront's live/dark push posture (joined by brand);
// names WRAP (never an
// ellipsis); typing filters; digits 1-9 pick the Nth visible card.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { storeApi } from '../../services/api';
import { onlineStoreApi, type StorefrontPosture } from '../../services/api/onlineStore';
import { getBrandAssets } from '../../utils/brandAssets';
import {
  accessibleStoresFrom,
  hasNoActiveStore,
  type AccessibleStore,
} from '../../utils/storeAccess';
import { isOnlineStore } from '../../utils/storeMode';
import { Icon } from '../../components/shell/Icon';

type Phase = 'loading' | 'choose' | 'empty';

interface Group {
  key: string;
  title: string;
  note: string;
  stores: AccessibleStore[];
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

const online = (s: AccessibleStore) => isOnlineStore({ id: s.id, store_type: s.storeType });

/** Human brand name for a store doc's `brand` (BETTER_VISION / WIZOPT). */
const brandLabel = (brand?: string) => (brand ? getBrandAssets(brand).name : 'Stores');

const byName = (a: AccessibleStore, b: AccessibleStore) => a.name.localeCompare(b.name);

/** Shops grouped by brand, then ONLINE stores as their own section. */
export function groupStores(stores: AccessibleStore[]): Group[] {
  const byBrand = new Map<string, AccessibleStore[]>();
  const web: AccessibleStore[] = [];
  for (const s of stores) {
    if (online(s)) {
      web.push(s);
      continue;
    }
    const k = (s.brand || '').toUpperCase();
    byBrand.set(k, [...(byBrand.get(k) ?? []), s]);
  }
  const groups: Group[] = [...byBrand.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, list]) => ({
      key: k || 'other',
      title: brandLabel(k),
      note: `${list.length} ${list.length === 1 ? 'shop' : 'shops'}`,
      stores: list.sort(byName),
    }));
  if (web.length) {
    groups.push({
      key: 'online',
      title: 'Online stores',
      note: 'no till, no walk-ins',
      stores: web.sort(byName),
    });
  }
  return groups;
}

const matches = (s: AccessibleStore, q: string) =>
  !q || [s.name, s.code, s.city, brandLabel(s.brand)].join(' ').toLowerCase().includes(q);

/** The storefront posture row for an ONLINE store, joined on BRAND — the only
 *  field the two documents actually share. A store doc carries `brand`
 *  (BETTER_VISION / WIZOPT, a required field on the store model) and NEVER a
 *  storefront_id; the storefronts registry row carries `brand` too. A store
 *  with no brand falls back to the default storefront.
 *
 *  A MISS IS NOT "unknown", it is DARK: `is_live` is decided purely by IMS's
 *  own push gates (writes + dispatch + that storefront's credentials), so a
 *  storefront IMS has not even registered — WizOpt today — cannot be one IMS
 *  is selling on. Only an unreadable list (rows === null: the summary 403'd or
 *  failed) means we genuinely do not know, and then the card claims nothing. */
function postureFor(s: AccessibleStore, rows: StorefrontPosture[]) {
  const brand = (s.brand || '').trim().toUpperCase();
  return brand
    ? rows.find((p) => (p.brand || '').trim().toUpperCase() === brand)
    : rows.find((p) => !!p.is_default);
}

const lastStoreKey = (userId: string) => `ims_last_store:${userId}`;
function readLastStore(userId?: string): string {
  try {
    return (userId && localStorage.getItem(lastStoreKey(userId))) || '';
  } catch {
    return '';
  }
}
function writeLastStore(userId: string | undefined, storeId: string) {
  try {
    if (userId) localStorage.setItem(lastStoreKey(userId), storeId);
  } catch {
    /* private mode / quota: the fallback is user.activeStoreId */
  }
}

const titleCase = (s: string) =>
  s
    .toLowerCase()
    .split('_')
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ');

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function StoreSelectPage() {
  const { user, setActiveStore, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const from = (location.state as { from?: string })?.from || '/dashboard';

  const [phase, setPhase] = useState<Phase>('loading');
  const [stores, setStores] = useState<AccessibleStore[]>([]);
  // Bumped by "Try again" to re-run the resolve effect after a failed fetch.
  const [retry, setRetry] = useState(0);
  const [query, setQuery] = useState('');
  const [postures, setPostures] = useState<StorefrontPosture[] | null>(null);

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
      let accessible: AccessibleStore[] = [];
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

  // Live/dark state for ONLINE stores = the storefront's push posture from the
  // online-store summary (BV live, WizOpt dark). getSummary never throws: a
  // role that cannot read it resolves to storefronts:null and the card simply
  // shows no posture chip — never a guessed one.
  useEffect(() => {
    if (phase !== 'choose' || !stores.some(online)) return;
    let cancelled = false;
    onlineStoreApi.getSummary().then((s) => {
      const rows = s?.storefronts ?? null;
      // A pre-`brand` backend (the window between the two deploys) sends rows
      // with nothing to join on. Treat that as NOT READABLE so the card claims
      // nothing, rather than calling a live storefront dark.
      if (!cancelled) setPostures(rows?.some((r) => r.brand) ? rows : null);
    });
    return () => {
      cancelled = true;
    };
  }, [phase, stores]);

  const groups = useMemo(() => groupStores(stores), [stores]);
  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      groups
        .map((g) => ({ ...g, stores: g.stores.filter((s) => matches(s, q)) }))
        .filter((g) => g.stores.length > 0),
    [groups, q],
  );
  // Cards in DOM order: digit N picks visible[N-1].
  const visible = useMemo(() => filtered.flatMap((g) => g.stores), [filtered]);
  const lastId = readLastStore(user?.id) || user?.activeStoreId;
  const resume = !q ? stores.find((s) => s.id === lastId) : undefined;

  const handlePick = (storeId: string) => {
    writeLastStore(user?.id, storeId);
    if (storeId !== user?.activeStoreId) {
      setActiveStore(storeId); // EXISTING mechanism — re-issues the JWT
    }
    navigate(from, { replace: true });
  };

  // Keyboard: Enter = "Where you were" (or the single filtered match); digits
  // 1-9 = the Nth visible card, only while the filter is EMPTY so "sec 4"
  // still types. Letters are never intercepted — they belong to the input.
  useEffect(() => {
    if (phase !== 'choose') return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === 'Enter') {
        const target = q ? (visible.length === 1 ? visible[0] : undefined) : resume;
        if (target) {
          e.preventDefault();
          handlePick(target.id);
        }
        return;
      }
      if (!q && /^[1-9]$/.test(e.key)) {
        const target = visible[Number(e.key) - 1];
        if (target) {
          e.preventDefault();
          handlePick(target.id);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, q, visible, resume?.id, user?.activeStoreId, from]);

  const handleSignOut = async () => {
    try {
      await logout();
    } finally {
      navigate('/login', { replace: true });
    }
  };

  // Login uses the Better Vision house lockup; mirror its mark here.
  const brand = getBrandAssets('bv');
  const roleLabel = titleCase((user?.activeRole || '').toString());

  // ---- Loading / auto-proceed splash --------------------------------------
  if (phase === 'loading') {
    return (
      <div style={centredStyle}>
        <div style={{ textAlign: 'center' }} role="status" aria-live="polite">
          <div
            className="w-10 h-10 border-4 border-bv-red-600 border-t-transparent rounded-full animate-spin mx-auto"
            aria-hidden="true"
          />
          <p style={{ marginTop: 16, color: 'var(--ink-3)', fontSize: 14 }}>Preparing your stores…</p>
        </div>
      </div>
    );
  }

  // ---- No accessible store (degenerate / fetch failed) --------------------
  if (phase === 'empty') {
    return (
      <div style={centredStyle}>
        <div style={emptyCardStyle}>
          <span style={{ ...badgeStyle, width: 48, height: 48, margin: '0 auto 14px' }} aria-hidden="true">
            <Icon.store width={22} height={22} />
          </span>
          <h1 className="display" style={{ fontSize: 24, color: 'var(--ink)', margin: '4px 0 8px' }}>
            No store available
          </h1>
          <p style={{ color: 'var(--ink-3)', fontSize: 14, lineHeight: 1.5, marginBottom: 20 }}>
            Your account isn't assigned to an active store yet. Please ask your administrator to
            assign you a store, then sign in again.
          </p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="btn lg"
              onClick={() => {
                setPhase('loading');
                setRetry((r) => r + 1);
              }}
            >
              Try again
            </button>
            <button type="button" className="btn accent lg" onClick={handleSignOut}>
              Sign out
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---- Choose a store ------------------------------------------------------
  let digit = 0;
  return (
    <main style={pageStyle}>
      <div style={columnStyle}>
        {/* Header: mark, title, who/role/count, filter */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <img src={brand.mark64} alt={brand.name} style={{ width: 34, height: 34, borderRadius: 9 }} />
          <div style={{ flex: '1 1 220px', minWidth: 0 }}>
            <h1 className="display" style={{ fontSize: 26, color: 'var(--ink)', margin: 0, lineHeight: 1.15 }}>
              Choose your store
            </h1>
            <p style={{ color: 'var(--ink-3)', fontSize: 13, margin: '2px 0 0' }}>
              {user?.name ? <strong style={{ color: 'var(--ink-2)' }}>{user.name}</strong> : 'Signed in'}
              {roleLabel ? <span> · {roleLabel}</span> : null}
              <span> — you have access to {stores.length}.</span>
            </p>
          </div>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type to filter"
            aria-label="Type to filter stores"
            autoFocus
            style={inputStyle}
          />
        </div>

        {/* Where you were: Enter */}
        {resume && (
          <button
            type="button"
            onClick={() => handlePick(resume.id)}
            aria-label={`Continue at ${resume.name}`}
            style={resumeStyle}
          >
            <span style={{ ...badgeStyle, width: 40, height: 40, background: 'var(--bv-50)' }} aria-hidden="true">
              {online(resume) ? <Icon.globe width={18} height={18} /> : <Icon.store width={18} height={18} />}
            </span>
            <span style={{ flex: '1 1 200px', minWidth: 0, textAlign: 'left' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={nameStyle}>{resume.name}</span>
                <span className="chip accent">Where you were</span>
              </span>
              <span className="mono" style={metaStyle}>
                {[resume.code, resume.city, resume.brand ? brandLabel(resume.brand) : '']
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, marginLeft: 'auto' }}>
              <kbd className="kbd" style={{ height: 22, minWidth: 30 }} aria-hidden="true">
                ↵
              </kbd>
              <span className="btn primary" style={{ height: 38 }}>
                Continue here
              </span>
            </span>
          </button>
        )}

        {filtered.length === 0 && (
          <p style={{ color: 'var(--ink-3)', fontSize: 13, margin: 0 }} role="status">
            No store matches “{query.trim()}”.
          </p>
        )}

        {/* Groups: brand, brand, online */}
        {filtered.map((g) => (
          <section key={g.key} aria-labelledby={`grp-${g.key}`} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <h2 id={`grp-${g.key}`} className="eyebrow" style={{ margin: 0 }}>
                {g.title}
              </h2>
              <span style={{ flex: 1, height: 1, background: 'var(--line)' }} aria-hidden="true" />
              <span className="mono" style={{ color: 'var(--ink-4)' }}>{g.note}</span>
            </div>
            <div role="listbox" aria-label={g.title} style={gridStyle}>
              {g.stores.map((s) => {
                const n = ++digit;
                const isCurrent = s.id === user?.activeStoreId;
                const web = online(s);
                // null postures = we could not read them; [] or a miss = dark.
                const live = web && postures ? !!postureFor(s, postures)?.is_live : false;
                return (
                  <button
                    key={s.id}
                    type="button"
                    role="option"
                    aria-selected={isCurrent}
                    onClick={() => handlePick(s.id)}
                    style={{
                      ...cardStyle,
                      borderColor: isCurrent ? 'var(--bv)' : 'var(--line)',
                      background: isCurrent ? 'var(--bv-soft)' : web ? 'var(--surface-2)' : 'var(--surface)',
                    }}
                  >
                    <span
                      style={{ ...badgeStyle, background: web ? 'var(--info-50)' : isCurrent ? 'var(--bv-50)' : 'var(--bg-sunk)' }}
                      aria-hidden="true"
                    >
                      {web ? <Icon.globe width={16} height={16} /> : <Icon.store width={16} height={16} />}
                    </span>
                    <span style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
                      <span style={nameStyle}>{s.name}</span>
                      {(s.code || s.city) && (
                        <span className="mono" style={metaStyle}>
                          {s.code}
                          {s.code && s.city ? <br /> : null}
                          {s.city}
                        </span>
                      )}
                      {(isCurrent || web) && (
                        <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
                          {web &&
                            (!postures ? (
                              <span className="chip info">Online</span>
                            ) : live ? (
                              <span className="chip ok">Selling</span>
                            ) : (
                              <span className="chip">Dark</span>
                            ))}
                          {isCurrent && <span className="chip accent">Current</span>}
                        </span>
                      )}
                    </span>
                    {n <= 9 && (
                      <kbd className="kbd" aria-label={`Press ${n}`}>
                        {n}
                      </kbd>
                    )}
                  </button>
                );
              })}
            </div>
          </section>
        ))}

        {/* Footer */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginTop: 6 }}>
          <button type="button" className="btn ghost lg" onClick={handleSignOut} style={{ paddingLeft: 4 }}>
            Not you? <span style={{ color: 'var(--info)', marginLeft: 4 }}>Sign out</span>
          </button>
          <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--ink-4)' }}>
            You can switch stores any time from the top bar.
          </span>
        </div>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Inline style objects (CSS-variable driven, matching the shell tokens).
// ---------------------------------------------------------------------------
const pageStyle: React.CSSProperties = {
  minHeight: '100vh',
  background: 'var(--bg)',
  padding: 'clamp(16px, 3vw, 40px)',
  boxSizing: 'border-box',
};

const columnStyle: React.CSSProperties = {
  maxWidth: 1000,
  margin: '0 auto',
  display: 'flex',
  flexDirection: 'column',
  gap: 18,
};

const centredStyle: React.CSSProperties = {
  minHeight: '100vh',
  background: 'var(--bg)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 24,
};

const emptyCardStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: 440,
  background: 'var(--surface)',
  border: '1px solid var(--line)',
  borderRadius: 'var(--r-lg)',
  boxShadow: 'var(--sh-md)',
  padding: 32,
  textAlign: 'center',
};

const inputStyle: React.CSSProperties = {
  flex: '1 1 200px',
  maxWidth: 320,
  height: 44,
  padding: '0 12px',
  border: '1px solid var(--line-strong)',
  borderRadius: 8,
  background: 'var(--surface)',
  color: 'var(--ink)',
  font: '400 13.5px var(--font-sans)',
};

const badgeStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 34,
  height: 34,
  borderRadius: 9,
  background: 'var(--bg-sunk)',
  color: 'var(--ink-2)',
  flex: '0 0 auto',
};

const resumeStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 13,
  flexWrap: 'wrap',
  width: '100%',
  minHeight: 44,
  padding: '12px 14px',
  border: '1px solid var(--bv)',
  borderRadius: 'var(--r-lg)',
  background: 'var(--bv-soft)',
  color: 'var(--ink)',
  cursor: 'pointer',
  textAlign: 'left',
};

const gridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 280px), 1fr))',
  gap: 11,
};

const cardStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  gap: 11,
  width: '100%',
  minHeight: 44,
  padding: '12px 13px',
  borderRadius: 'var(--r-lg)',
  border: '1px solid var(--line)',
  background: 'var(--surface)',
  cursor: 'pointer',
  color: 'var(--ink-2)',
  textAlign: 'left',
};

// The visible bug: the name used to be nowrap + ellipsis in a 240px card, so
// half the estate read as "GANG…" / "WizOpt…". It wraps now — never truncate.
const nameStyle: React.CSSProperties = {
  display: 'block',
  fontWeight: 600,
  fontSize: 14,
  lineHeight: 1.25,
  color: 'var(--ink)',
  overflowWrap: 'anywhere',
};

const metaStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 11,
  lineHeight: 1.35,
  color: 'var(--ink-4)',
  marginTop: 3,
};

export default StoreSelectPage;
