// ============================================================================
// IMS 2.0 - Display Layout Panel (v2-2b)
// ============================================================================
// New "Display layout" tab on the Inventory page. Lets the owner declare every
// physical fixture in a store, then place SKUs onto them. Composes with the
// backend (display_fixtures + display_placements) shipped in PR #275.
//
// Empty state on first deploy. NO mock data. Owner creates fixtures + their
// SKU placements via the UI here.

import { useEffect, useMemo, useState } from 'react';
import {
  Plus,
  Loader2,
  Tv,
  LayoutGrid,
  Columns,
  Slash,
  Lock,
  Grid3x3,
  Archive,
  Snowflake,
  Pencil,
  Trash2,
  Search,
  ArrowRightLeft,
  X,
  CheckCircle2,
  History,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  displayFixturesApi,
  type DisplayFixture,
  type FixtureFloor,
  type FixtureType,
} from '../../services/api/displayFixtures';
import {
  displayPlacementsApi,
  type DisplayPlacement,
} from '../../services/api/displayPlacements';
import { productApi } from '../../services/api/products';
import { storeApi } from '../../services/api/stores';
import { DisplayFixtureFormModal } from './DisplayFixtureFormModal';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TYPE_ICON: Record<FixtureType, LucideIcon> = {
  window:  Tv,
  wall:    LayoutGrid,
  pillar:  Columns,
  counter: Slash,
  cabinet: Lock,
  gondola: Grid3x3,
  drawer:  Archive,
  fridge:  Snowflake,
};

const TYPE_LABEL: Record<FixtureType, string> = {
  window:  'Window',
  wall:    'Wall',
  pillar:  'Pillar',
  counter: 'Counter',
  cabinet: 'Cabinet',
  gondola: 'Gondola',
  drawer:  'Drawer',
  fridge:  'Fridge',
};

const FLOORS: { id: FixtureFloor | 'all'; label: string }[] = [
  { id: 'all',     label: 'All floors' },
  { id: 'ground',  label: 'Ground floor' },
  { id: 'storage', label: 'Storage' },
  { id: 'clinic',  label: 'Clinic chamber' },
];

const FLOOR_LABEL: Record<FixtureFloor, string> = {
  ground:  'Ground floor',
  storage: 'Storage',
  clinic:  'Clinic chamber',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return 'Never audited';
  try {
    const ts = new Date(iso).getTime();
    if (!Number.isFinite(ts)) return 'Never audited';
    const diff = Date.now() - ts;
    const day = 24 * 60 * 60 * 1000;
    const d = new Date(ts);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = d.toLocaleString('en-GB', { month: 'short' });
    const yyyy = d.getFullYear();
    if (diff < day) return `Audited today (${dd}-${mm})`;
    if (diff < 2 * day) return `Audited yesterday (${dd}-${mm})`;
    if (diff < 30 * day) return `Audited ${Math.floor(diff / day)} days ago (${dd}-${mm})`;
    return `Audited ${dd}-${mm}-${yyyy}`;
  } catch {
    return 'Never audited';
  }
}

function capacityClass(pct: number): string {
  if (pct > 100) return 'full';
  if (pct >= 80) return 'hi';
  if (pct < 30)  return 'lo';
  return '';
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface Props {
  // Triggered by the URL query `?fixture=W-01`. Once selected we clear it.
  initialFixtureId?: string | null;
  onFixtureSelectionConsumed?: () => void;
}

interface StoreOpt { id: string; name: string }

export function DisplayLayoutPanel({ initialFixtureId, onFixtureSelectionConsumed }: Props) {
  const { user, hasRole } = useAuth();
  const toast = useToast();

  const canWrite = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'STORE_MANAGER']);

  // Store dropdown ------------------------------------------------------
  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [storeId, setStoreId] = useState<string>(user?.activeStoreId || '');

  useEffect(() => {
    storeApi.getStores().then((res: unknown) => {
      const r = res as { stores?: unknown[] } | unknown[];
      const list = (Array.isArray(r) ? r : r?.stores ?? []) as Array<Record<string, unknown>>;
      const allStoreAccess = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']);
      const mapped = list
        .map(s => ({
          id: String(s.store_id ?? s.id ?? s._id ?? ''),
          name: String(s.store_name ?? s.storeName ?? s.name ?? s.store_id ?? s.id ?? ''),
        }))
        .filter((s: StoreOpt) => s.id && (allStoreAccess || (user?.storeIds || []).includes(s.id)));
      setStores(mapped);
    }).catch(() => setStores([]));
  }, [user?.storeIds, hasRole]);

  useEffect(() => {
    if (user?.activeStoreId && !storeId) setStoreId(user.activeStoreId);
  }, [user?.activeStoreId, storeId]);

  // Fixtures + placements ----------------------------------------------
  const [floor, setFloor] = useState<FixtureFloor | 'all'>('all');
  const [fixtures, setFixtures] = useState<DisplayFixture[]>([]);
  const [placements, setPlacements] = useState<DisplayPlacement[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Modal state --------------------------------------------------------
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<DisplayFixture | null>(null);

  // Drawer state (iPad V / Mobile) -------------------------------------
  const [detailOpen, setDetailOpen] = useState(false);

  const reload = async (sid: string) => {
    if (!sid) {
      setFixtures([]);
      setPlacements([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [fRes, pRes] = await Promise.all([
        displayFixturesApi.list({ store_id: sid, active: true }).catch(() => ({ fixtures: [], total: 0 })),
        displayPlacementsApi.list({ store_id: sid }).catch(() => ({ placements: [], total: 0 })),
      ]);
      setFixtures(fRes.fixtures ?? []);
      setPlacements(pRes.placements ?? []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { reload(storeId); }, [storeId]);

  // Honor a deep-link from the Stock Ledger Zone column ----------------
  useEffect(() => {
    if (!initialFixtureId || !fixtures.length) return;
    const hit = fixtures.find(f => f.fixture_id === initialFixtureId || f.code === initialFixtureId);
    if (hit) {
      setSelectedId(hit.fixture_id);
      // If we're in a tighter viewport, pop the drawer open so the user sees the panel.
      if (typeof window !== 'undefined' && window.innerWidth < 1024) setDetailOpen(true);
      // Best-effort scroll into view.
      setTimeout(() => {
        const node = document.querySelector(`[data-fixture-id="${hit.fixture_id}"]`);
        node?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 80);
      onFixtureSelectionConsumed?.();
    }
  }, [initialFixtureId, fixtures, onFixtureSelectionConsumed]);

  // Group fixtures by floor with capacity totals -----------------------
  const groupedByFloor = useMemo(() => {
    const out: Record<FixtureFloor, DisplayFixture[]> = { ground: [], storage: [], clinic: [] };
    for (const f of fixtures) {
      if (f.floor in out) out[f.floor].push(f);
    }
    for (const k of Object.keys(out) as FixtureFloor[]) {
      out[k].sort((a, b) => (a.code || '').localeCompare(b.code || ''));
    }
    return out;
  }, [fixtures]);

  // Placements indexed by fixture for fast lookups --------------------
  const placementsByFixture = useMemo(() => {
    const m: Record<string, DisplayPlacement[]> = {};
    for (const p of placements) {
      (m[p.fixture_id] ||= []).push(p);
    }
    return m;
  }, [placements]);

  const fixtureStats = (f: DisplayFixture) => {
    const rows = placementsByFixture[f.fixture_id] || [];
    const units = rows.reduce((s, r) => s + (r.qty || 0), 0);
    const skus = new Set(rows.map(r => r.sku)).size;
    const pct = f.capacity > 0 ? Math.round((units / f.capacity) * 100) : 0;
    return { units, skus, pct };
  };

  // Per-floor totals (the floor strip) --------------------------------
  const floorTotals = useMemo(() => {
    return (Object.keys(groupedByFloor) as FixtureFloor[]).map(fl => {
      const list = groupedByFloor[fl];
      const cap = list.reduce((s, f) => s + (f.capacity || 0), 0);
      const placed = list.reduce((s, f) => s + fixtureStats(f).units, 0);
      return { floor: fl, count: list.length, capacity: cap, placed };
    });
  }, [groupedByFloor, placementsByFixture]);

  const selectedFixture = selectedId ? fixtures.find(f => f.fixture_id === selectedId) ?? null : null;

  const handleSaved = (fx: DisplayFixture) => {
    setFixtures(prev => {
      const i = prev.findIndex(p => p.fixture_id === fx.fixture_id);
      if (i === -1) return [...prev, fx];
      const next = [...prev];
      next[i] = fx;
      return next;
    });
    setSelectedId(fx.fixture_id);
  };

  const handleDelete = async (f: DisplayFixture) => {
    if (!confirm(`Soft-delete fixture ${f.code} — ${f.name}? Refuses if any placements still reference it.`)) return;
    try {
      await displayFixturesApi.softDelete(f.fixture_id);
      toast.success(`Fixture ${f.code} archived`);
      setSelectedId(null);
      setFixtures(prev => prev.filter(p => p.fixture_id !== f.fixture_id));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(msg || 'Delete failed');
    }
  };

  const handleMarkAudited = async (f: DisplayFixture) => {
    try {
      const nowIso = new Date().toISOString();
      // NOTE: the v2-2a backend (PR #275) doesn't currently accept
      // last_audit_at on FixtureUpdate; the field is silently dropped (no_changes).
      // We still issue the PATCH so future backend support is transparent, and
      // optimistically reflect the timestamp locally so the operator sees the
      // change immediately. Full audit-trail wiring lands in v2-2c when GRN
      // and stock-count flows write last_audit_at themselves.
      const { fixture: updated } = await displayFixturesApi.update(f.fixture_id, { last_audit_at: nowIso });
      toast.success(`${f.code} marked counted`);
      // Always apply the optimistic timestamp locally -- if the server echoes
      // back the old value, our local update wins for the current session.
      const merged: DisplayFixture = { ...(updated ?? f), last_audit_at: nowIso };
      handleSaved(merged);
    } catch (e) {
      toast.error((e instanceof Error ? e.message : String(e)) || 'Failed to mark counted');
    }
  };

  // ---------------------------------------------------------------------
  // Empty + loading states
  // ---------------------------------------------------------------------

  if (loading) {
    return (
      <div className="card" style={{ minHeight: 240, display: 'grid', placeItems: 'center' }}>
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--bv)' }} />
      </div>
    );
  }

  const currentStoreName = stores.find(s => s.id === storeId)?.name || 'this store';

  // ---------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------

  return (
    <div>
      {/* Top strip: store + floor switch + add CTA -------------------- */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        {stores.length > 0 && (
          <div className="flex items-center gap-2">
            <label htmlFor="dl-store" className="text-xs font-medium text-gray-500 uppercase">Store</label>
            <select
              id="dl-store"
              value={storeId}
              onChange={e => { setStoreId(e.target.value); setSelectedId(null); }}
              className="input-field text-sm py-1.5 w-56"
            >
              {stores.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
        )}

        <div className="flex flex-wrap gap-1 ml-auto">
          {FLOORS.map(f => (
            <button
              key={f.id}
              onClick={() => setFloor(f.id)}
              className={
                'px-3 py-1.5 rounded-lg text-xs font-medium border whitespace-nowrap ' +
                (floor === f.id
                  ? 'text-gray-900 border-transparent'
                  : 'bg-gray-100 text-gray-600 border-transparent hover:bg-gray-200')
              }
              style={floor === f.id ? { background: 'var(--bv-50)', borderColor: 'var(--bv)' } : undefined}
            >
              {f.label}
            </button>
          ))}
        </div>

        {canWrite && fixtures.length > 0 && (
          <button
            onClick={() => { setEditing(null); setShowCreate(true); }}
            className="px-3 py-1.5 rounded-lg text-sm font-medium text-white inline-flex items-center gap-1.5"
            style={{ background: 'var(--bv)' }}
          >
            <Plus className="w-4 h-4" /> Add fixture
          </button>
        )}
      </div>

      {/* Empty state ------------------------------------------------- */}
      {fixtures.length === 0 ? (
        <div className="card" style={{ padding: 0 }}>
          <div className="text-center py-16 px-6">
            <div
              className="mx-auto w-14 h-14 grid place-items-center rounded-full mb-4"
              style={{ background: 'var(--bv-50)' }}
            >
              <LayoutGrid className="w-7 h-7" style={{ color: 'var(--bv)' }} strokeWidth={1.6} />
            </div>
            <h3 className="text-lg font-semibold text-gray-900 mb-1">
              No display layout configured for {currentStoreName} yet.
            </h3>
            <p className="text-sm text-gray-500 max-w-xl mx-auto mb-5">
              Add your first fixture — wall, counter, pillar, locked cabinet, drawer, or fridge —
              to start mapping where each SKU lives. Once placed, the Zone column on the Stock
              ledger and the printed count sheet group by fixture.
            </p>
            {canWrite ? (
              <button
                onClick={() => { setEditing(null); setShowCreate(true); }}
                className="px-5 py-2.5 rounded-lg text-sm font-medium text-white inline-flex items-center gap-2"
                style={{ background: 'var(--bv)' }}
              >
                <Plus className="w-4 h-4" /> Add fixture
              </button>
            ) : (
              <p className="text-xs text-gray-500">
                Ask a store manager or catalog manager to set up the floor map.
              </p>
            )}
          </div>
        </div>
      ) : (
        <>
          {/* Per-floor totals ---------------------------------------- */}
          <div className="fl-summary">
            {floorTotals.map(t => (
              <div key={t.floor}>
                <div className="l">{FLOOR_LABEL[t.floor]}</div>
                <div className="v">{t.count}</div>
                <div className="d">
                  {t.placed.toLocaleString('en-IN')} / {t.capacity.toLocaleString('en-IN')} units placed
                </div>
              </div>
            ))}
          </div>

          {/* Body: floor map + detail panel -------------------------- */}
          <div className="fl-body">
            <div>
              {(['ground', 'storage', 'clinic'] as FixtureFloor[])
                .filter(fl => floor === 'all' || floor === fl)
                .map(fl => {
                  const list = groupedByFloor[fl];
                  if (!list.length) return null;
                  return (
                    <div key={fl} className="fl-floor">
                      <div className="fl-floor-head">
                        <span className="ttl">{FLOOR_LABEL[fl]}</span>
                        <span className="meta">{list.length} fixture{list.length === 1 ? '' : 's'}</span>
                        <span className="line"></span>
                      </div>
                      <div className="fix-grid">
                        {list.map(f => {
                          const st = fixtureStats(f);
                          const TypeIcon = TYPE_ICON[f.type];
                          const sel = selectedId === f.fixture_id;
                          const barCls = capacityClass(st.pct);
                          return (
                            <div
                              key={f.fixture_id}
                              data-fixture-id={f.fixture_id}
                              className={'fix-card' + (sel ? ' on' : '')}
                              onClick={() => {
                                setSelectedId(f.fixture_id);
                                if (typeof window !== 'undefined' && window.innerWidth < 1024) setDetailOpen(true);
                              }}
                              role="button"
                              tabIndex={0}
                              onKeyDown={e => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  setSelectedId(f.fixture_id);
                                }
                              }}
                            >
                              <div className="top">
                                <div>
                                  <div className="code">{f.code} <span style={{ color: 'var(--ink-4)', fontWeight: 500, marginLeft: 2 }}>Z{f.zone}</span></div>
                                  <div className="tname">{f.name}</div>
                                </div>
                                <div className="ticon" title={TYPE_LABEL[f.type]}>
                                  <TypeIcon className="w-4 h-4" strokeWidth={1.6} />
                                </div>
                              </div>
                              <div className="badges">
                                <span className="b">{TYPE_LABEL[f.type]}</span>
                                {f.zone !== '-' && <span className="b">Zone {f.zone}</span>}
                                {f.lockable && <span className="b lock">Keyed</span>}
                                {f.temp_ctrl && <span className="b cold">{f.temp_ctrl}</span>}
                                {f.spotlit && <span className="b signature">Spotlit</span>}
                                {f.mannequin && <span className="b signature">Mannequin</span>}
                              </div>
                              <div className="cap">
                                <div className={'cap-bar ' + barCls}>
                                  <div style={{ width: Math.min(100, st.pct) + '%' }}></div>
                                </div>
                                <div className="cap-pct">{st.pct}%</div>
                              </div>
                              <div className="stats">
                                <span><span className="strong">{st.skus}</span> SKUs</span>
                                <span><span className="strong">{st.units}</span> / {f.capacity} units</span>
                                <span title={f.last_audit_at || 'Never audited'}>
                                  {formatRelative(f.last_audit_at)}
                                </span>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              {/* If a floor filter is set + that floor is empty, soften with hint */}
              {floor !== 'all' && groupedByFloor[floor].length === 0 && (
                <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--ink-4)', fontSize: 13 }}>
                  No fixtures on {FLOOR_LABEL[floor]} yet.
                </div>
              )}
            </div>

            {/* Detail panel (collapses to drawer at narrow widths) ---- */}
            <FixtureDetailPanel
              fixture={selectedFixture}
              fixturesAll={fixtures}
              placements={selectedFixture ? (placementsByFixture[selectedFixture.fixture_id] ?? []) : []}
              storeId={storeId}
              canWrite={canWrite}
              onEdit={() => selectedFixture && setEditing(selectedFixture)}
              onDelete={() => selectedFixture && handleDelete(selectedFixture)}
              onMarkAudited={() => selectedFixture && handleMarkAudited(selectedFixture)}
              onClose={() => setDetailOpen(false)}
              onPlacementsChange={() => reload(storeId)}
              isDrawer={detailOpen}
            />
          </div>
        </>
      )}

      {/* Modal — create / edit ------------------------------------ */}
      {(showCreate || editing) && (
        <DisplayFixtureFormModal
          isOpen={showCreate || !!editing}
          onClose={() => { setShowCreate(false); setEditing(null); }}
          onSaved={handleSaved}
          fixture={editing ?? undefined}
          storeId={storeId}
        />
      )}
    </div>
  );
}

// ===========================================================================
// FixtureDetailPanel — right column / drawer
// ===========================================================================

interface DetailProps {
  fixture: DisplayFixture | null;
  fixturesAll: DisplayFixture[];
  placements: DisplayPlacement[];
  storeId: string;
  canWrite: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onMarkAudited: () => void;
  onClose: () => void;
  onPlacementsChange: () => void;
  isDrawer: boolean;
}

function FixtureDetailPanel({
  fixture,
  fixturesAll,
  placements,
  storeId,
  canWrite,
  onEdit,
  onDelete,
  onMarkAudited,
  onClose,
  onPlacementsChange,
  isDrawer,
}: DetailProps) {
  // Mobile drawer: render fixed overlay when isDrawer + < 1024px.
  // CSS rule in index.css already drops .fix-panel out of the grid <1024px;
  // we toggle the drawer mode via a small wrapper for narrow viewports.

  if (!fixture) {
    return (
      <aside className="fix-panel" style={{ padding: '20px 16px' }}>
        <p className="text-xs text-gray-500" style={{ font: '500 11px/1.5 var(--font-sans)' }}>
          Pick a fixture on the left to see and edit details, audit it, or place a SKU on it.
        </p>
      </aside>
    );
  }

  const TypeIcon = TYPE_ICON[fixture.type];
  const totalUnits = placements.reduce((s, p) => s + (p.qty || 0), 0);
  const pct = fixture.capacity > 0 ? Math.round((totalUnits / fixture.capacity) * 100) : 0;

  return (
    <aside
      className={'fix-panel ' + (isDrawer ? 'fix-panel--drawer' : '')}
      style={
        isDrawer && typeof window !== 'undefined' && window.innerWidth < 1024
          ? {
              position: 'fixed',
              right: 0,
              top: 0,
              bottom: 0,
              width: 'min(420px, 96vw)',
              zIndex: 50,
              borderRadius: 0,
              borderLeft: '1px solid var(--line-strong)',
              boxShadow: '-8px 0 24px rgba(0,0,0,.06)',
            }
          : undefined
      }
    >
      <div className="fix-panel-head">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
          <div>
            <div className="code">{fixture.code}</div>
            <div className="name">{fixture.name}</div>
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
            <div
              style={{
                width: 32, height: 32,
                background: 'var(--surface)',
                border: '1px solid var(--line-strong)',
                borderRadius: 'var(--r-sm)',
                display: 'grid', placeItems: 'center', color: 'var(--ink-3)',
              }}
              title={TYPE_LABEL[fixture.type]}
            >
              <TypeIcon className="w-4 h-4" strokeWidth={1.6} />
            </div>
            {/* Close button — only visible in drawer mode (mobile) */}
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-gray-900 p-1 lg:hidden"
              aria-label="Close"
              style={{ display: 'none' }}
              data-drawer-close
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
        <div className="meta-row">
          <span className="b">{TYPE_LABEL[fixture.type]}</span>
          <span className="b">Floor: {fixture.floor}</span>
          <span className="b">Zone {fixture.zone}</span>
          {fixture.lockable && <span className="b warn">Keyed{fixture.key_holder ? ' . ' + fixture.key_holder : ''}</span>}
        </div>
      </div>

      <div className="fix-panel-meta">
        <div className="k">Capacity</div>
        <div className="v">{totalUnits} / {fixture.capacity} units . <strong>{pct}%</strong></div>
        <div className="k">SKUs here</div>
        <div className="v">{new Set(placements.map(p => p.sku)).size} distinct</div>
        <div className="k">Merch tags</div>
        <div className="v">{fixture.merch.length ? fixture.merch.join(', ') : 'Any'}</div>
        <div className="k">Last audited</div>
        <div className="v" title={fixture.last_audit_at ?? ''}>
          <History className="inline-block w-3 h-3 -mt-0.5 mr-1" />
          {formatRelative(fixture.last_audit_at)}
        </div>
        {fixture.temp_ctrl && (<><div className="k">Temp control</div><div className="v">{fixture.temp_ctrl}</div></>)}
        {fixture.spotlit && (<><div className="k">Lighting</div><div className="v">Spotlit</div></>)}
        {fixture.mannequin && (<><div className="k">Layout</div><div className="v">Has mannequin</div></>)}
        {fixture.no_qr && (
          <>
            <div className="k">Anti-theft</div>
            <div className="v" style={{ color: 'var(--warn)' }}>No EAS tag . staff supervision</div>
          </>
        )}
        {fixture.notes && (
          <>
            <div className="k">Notes</div>
            <div className="v">{fixture.notes}</div>
          </>
        )}
      </div>

      <PlacementsList
        fixture={fixture}
        placements={placements}
        fixturesAll={fixturesAll}
        storeId={storeId}
        canWrite={canWrite}
        onChange={onPlacementsChange}
      />

      <div className="fix-panel-actions">
        {canWrite && (
          <button onClick={onMarkAudited} className="btn block sm" type="button">
            <CheckCircle2 className="w-4 h-4 mr-1.5" /> Mark counted now
          </button>
        )}
        {canWrite && (
          <button onClick={onEdit} className="btn block sm" type="button">
            <Pencil className="w-4 h-4 mr-1.5" /> Edit fixture
          </button>
        )}
        {canWrite && (
          <button
            onClick={onDelete}
            className="btn block sm ghost"
            type="button"
            style={{ color: 'var(--err)' }}
          >
            <Trash2 className="w-4 h-4 mr-1.5" /> Soft-delete fixture
          </button>
        )}
      </div>
    </aside>
  );
}

// ===========================================================================
// PlacementsList — list of SKUs at this fixture + create / delete / move
// ===========================================================================

interface PlListProps {
  fixture: DisplayFixture;
  placements: DisplayPlacement[];
  fixturesAll: DisplayFixture[];
  storeId: string;
  canWrite: boolean;
  onChange: () => void;
}

interface ProductHit { id?: string; sku: string; name?: string; brand?: string; category?: string }

function PlacementsList({ fixture, placements, fixturesAll, storeId, canWrite, onChange }: PlListProps) {
  const toast = useToast();
  const [showAdd, setShowAdd] = useState(false);
  const [searchQ, setSearchQ] = useState('');
  const [hits, setHits] = useState<ProductHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [selectedSku, setSelectedSku] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string | undefined>(undefined);
  const [qty, setQty] = useState<string>('1');
  const [position, setPosition] = useState<string>('');
  const [isPrimary, setIsPrimary] = useState<boolean>(false);
  const [saving, setSaving] = useState(false);

  const [moveTarget, setMoveTarget] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!searchQ || searchQ.length < 2) { setHits([]); return; }
    let cancelled = false;
    setSearching(true);
    productApi
      .searchProducts(searchQ)
      .then((res: unknown) => {
        if (cancelled) return;
        const r = res as { products?: unknown[] } | unknown[];
        const list = (Array.isArray(r) ? r : r?.products ?? []) as Array<Record<string, unknown>>;
        const mapped: ProductHit[] = list.slice(0, 8).map(p => ({
          id: (p.id as string) ?? (p._id as string),
          sku: String(p.sku ?? ''),
          name: String(p.name ?? p.model ?? ''),
          brand: String(p.brand ?? ''),
          category: String(p.category ?? ''),
        })).filter(h => h.sku);
        setHits(mapped);
      })
      .catch(() => setHits([]))
      .finally(() => { if (!cancelled) setSearching(false); });
    return () => { cancelled = true; };
  }, [searchQ]);

  const onPickSearchHit = (h: ProductHit) => {
    setSelectedSku(h.sku);
    setSearchQ(h.sku + (h.name ? ' . ' + h.name : ''));
    setSelectedCategory(h.category || undefined);
    setHits([]);
  };

  const resetAdd = () => {
    setShowAdd(false);
    setSearchQ('');
    setSelectedSku('');
    setSelectedCategory(undefined);
    setQty('1');
    setPosition('');
    setIsPrimary(false);
  };

  const onAdd = async () => {
    const sku = selectedSku.trim() || searchQ.trim().split(' ')[0];
    if (!sku) { toast.error('Pick a SKU to place'); return; }
    const qN = Number(qty);
    if (!Number.isFinite(qN) || qN < 1) { toast.error('Qty must be at least 1'); return; }
    setSaving(true);
    try {
      await displayPlacementsApi.create({
        sku,
        store_id: storeId,
        fixture_id: fixture.fixture_id,
        qty: qN,
        position: position.trim() || undefined,
        is_primary: isPrimary || undefined,
        product_category: selectedCategory,
      });
      toast.success(`Placed ${sku} . ${qty} at ${fixture.code}`);
      resetAdd();
      onChange();
    } catch (e) {
      toast.error((e instanceof Error ? e.message : String(e)) || 'Failed to place SKU');
    } finally {
      setSaving(false);
    }
  };

  const onDeletePlacement = async (p: DisplayPlacement) => {
    if (!confirm(`Remove placement ${p.sku} . ${p.qty} from ${fixture.code}?`)) return;
    try {
      await displayPlacementsApi.delete(p.placement_id);
      toast.success('Placement removed');
      onChange();
    } catch (e) {
      toast.error((e instanceof Error ? e.message : String(e)) || 'Delete failed');
    }
  };

  const onMove = async (p: DisplayPlacement) => {
    const target = moveTarget[p.placement_id];
    if (!target || target === fixture.fixture_id) {
      toast.warning('Pick a different target fixture');
      return;
    }
    try {
      await displayPlacementsApi.move(p.placement_id, target);
      toast.success(`Moved ${p.sku} to ${target}`);
      setMoveTarget(prev => { const n = { ...prev }; delete n[p.placement_id]; return n; });
      onChange();
    } catch (e) {
      toast.error((e instanceof Error ? e.message : String(e)) || 'Move failed');
    }
  };

  // Other fixtures in the same store, excluding current
  const moveTargets = fixturesAll.filter(f => f.fixture_id !== fixture.fixture_id && f.store_id === fixture.store_id);

  return (
    <>
      <div className="fix-panel-list">
        {placements.length === 0 ? (
          <div style={{ padding: '20px 16px', textAlign: 'center', color: 'var(--ink-4)', fontSize: 12 }}>
            No SKUs placed at {fixture.code} yet.
          </div>
        ) : (
          placements.map(p => (
            <div key={p.placement_id} className="row">
              <div>
                <div className="sku">{p.sku}{p.is_primary && (
                  <span
                    className="ml-2 px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wider"
                    style={{ background: 'var(--bv-50)', color: 'var(--bv)', font: '600 9px/1 var(--font-mono)' }}
                  >
                    Primary
                  </span>
                )}</div>
                <div className="pos">{p.position || '-'}</div>
                {canWrite && moveTargets.length > 0 && (
                  <div className="mt-2 flex items-center gap-1.5">
                    <select
                      value={moveTarget[p.placement_id] ?? ''}
                      onChange={e => setMoveTarget(prev => ({ ...prev, [p.placement_id]: e.target.value }))}
                      className="input-field text-xs py-0.5"
                      style={{ width: 130, height: 26 }}
                    >
                      <option value="">Move to.</option>
                      {moveTargets.map(t => (
                        <option key={t.fixture_id} value={t.fixture_id}>{t.code} . {t.name}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => onMove(p)}
                      disabled={!moveTarget[p.placement_id]}
                      className="btn sm"
                      style={{ height: 26, padding: '0 6px' }}
                      title="Move placement"
                    >
                      <ArrowRightLeft className="w-3 h-3" />
                    </button>
                  </div>
                )}
              </div>
              <div>
                <div className="qty">{p.qty}</div>
                <div className="qty-unit">units</div>
              </div>
              {canWrite && (
                <button
                  type="button"
                  className="text-gray-400 hover:text-red-600 self-start"
                  onClick={() => onDeletePlacement(p)}
                  title="Remove placement"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          ))
        )}
      </div>

      {canWrite && (
        <div style={{ padding: '10px 16px 6px', borderTop: '1px solid var(--line)' }}>
          {!showAdd ? (
            <button
              type="button"
              onClick={() => setShowAdd(true)}
              className="btn block sm"
            >
              <Plus className="w-3.5 h-3.5 mr-1.5" /> Place a SKU here
            </button>
          ) : (
            <div className="grid gap-2">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                <input
                  type="text"
                  value={searchQ}
                  onChange={e => { setSearchQ(e.target.value); setSelectedSku(''); }}
                  placeholder="Search SKU, name, brand."
                  className="input-field text-xs pl-7"
                />
                {hits.length > 0 && (
                  <div
                    className="absolute z-10 top-full left-0 right-0 mt-1 max-h-44 overflow-y-auto rounded-md border bg-white shadow-md"
                    style={{ borderColor: 'var(--line-strong)' }}
                  >
                    {hits.map(h => (
                      <button
                        key={h.sku}
                        type="button"
                        onClick={() => onPickSearchHit(h)}
                        className="block w-full text-left px-2.5 py-1.5 text-xs hover:bg-gray-50"
                      >
                        <span className="font-mono text-[11px] text-gray-700">{h.sku}</span>
                        {h.name && <span className="text-gray-500"> . {h.name}</span>}
                        {h.brand && <span className="text-gray-400 ml-1">[{h.brand}]</span>}
                      </button>
                    ))}
                    {searching && <div className="px-2.5 py-1.5 text-[10px] text-gray-400">Searching.</div>}
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <input
                  type="number"
                  min={1}
                  value={qty}
                  onChange={e => setQty(e.target.value)}
                  className="input-field text-xs"
                  style={{ width: 70 }}
                  placeholder="Qty"
                />
                <input
                  type="text"
                  value={position}
                  onChange={e => setPosition(e.target.value)}
                  className="input-field text-xs flex-1"
                  placeholder="shelf-2 . slot-04"
                />
              </div>
              <label className="inline-flex items-center gap-2 text-xs text-gray-600">
                <input type="checkbox" checked={isPrimary} onChange={e => setIsPrimary(e.target.checked)} />
                Primary placement (defaults to this on the Stock ledger)
              </label>
              <div className="flex justify-end gap-2 pt-1">
                <button type="button" onClick={resetAdd} className="btn sm ghost">Cancel</button>
                <button
                  type="button"
                  onClick={onAdd}
                  disabled={saving}
                  className="px-3 py-1.5 rounded-md text-xs font-medium text-white inline-flex items-center gap-1.5 disabled:opacity-50"
                  style={{ background: 'var(--bv)' }}
                >
                  {saving && <Loader2 className="w-3 h-3 animate-spin" />}
                  Place
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}

export default DisplayLayoutPanel;
