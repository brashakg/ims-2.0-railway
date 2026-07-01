// ============================================================================
// IMS 2.0 - Online Store : Shopify sync CONTROL PANEL  (BVI Phase 6)
// ============================================================================
// The frontend control surface for the (already-built, dark) IMS -> Shopify push
// engine. It is a STATUS + DRY-RUN cockpit — it never arms or bypasses the live
// gates. The live push stays owner-armed behind the backend triple-gate
// (IMS_SHOPIFY_WRITES=1 + DISPATCH_MODE=live + Shopify creds); this page only:
//
//   1. DISPLAYS the three gates + the effective mode (unmistakably "DRY-RUN" vs
//      "LIVE"), reading GET /online-store/push/status (pushApi.getStatus).
//   2. Shows per-entity pending counts from the same status payload.
//   3. Offers per-entity DRY-RUN buttons that call the SAME push engine
//      (pushApi.pushAllPending, filtered) — SIMULATED when the gates are off, so
//      they are always safe, and shows the returned plan.
//   4. Surfaces the admin sync-health / parity / drift diagnostics as read-only
//      tiles (syncHealthApi — SUPERADMIN-gated; fail-soft to "unavailable").
//   5. A "Go live" affordance that is DISABLED unless the BACKEND status reports
//      the triple-gate armed AND the viewer is ADMIN/SUPERADMIN. Even when
//      enabled it calls the SAME existing endpoint (the backend gate is the real
//      control) behind a confirm. There is NO client-side way to arm the gates.
//
// Light theme only. Matches the existing Online Store page style.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  RefreshCw,
  ShieldCheck,
  ShieldAlert,
  Zap,
  Loader2,
  Package,
  Layers,
  Menu as MenuIcon,
  Image as ImageIcon,
  Play,
  Rocket,
  Activity,
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import {
  pushApi,
  syncHealthApi,
  type PushStatus,
  type PushSweepResult,
  type SyncHealth,
  type SyncParity,
  type SyncDrift,
} from '../../services/api/onlineStore';
import OnlineStoreSyncBanner, {
  formatPushResult,
  type OnlineStoreSyncBannerHandle,
} from '../../components/online-store/OnlineStoreSyncBanner';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

// ----------------------------------------------------------------------------
// Per-entity descriptor: which status-count block + all-pending filter it uses.
// ----------------------------------------------------------------------------
type EntityKey = 'products' | 'collections' | 'menus' | 'images';

interface EntityDef {
  key: EntityKey;
  /** all-pending `entities` CSV token. */
  token: string;
  label: string;
  icon: typeof Package;
  /** Label for the "already on Shopify" count. */
  pushedLabel: string;
  /** Label for the "not yet pushed / dirty" count. */
  pendingLabel: string;
}

const ENTITIES: EntityDef[] = [
  { key: 'products', token: 'products', label: 'Products', icon: Package, pushedLabel: 'pushed', pendingLabel: 'pending' },
  { key: 'collections', token: 'collections', label: 'Collections', icon: Layers, pushedLabel: 'pushed', pendingLabel: 'pending' },
  { key: 'menus', token: 'menus', label: 'Menus', icon: MenuIcon, pushedLabel: 'pushed', pendingLabel: 'pending' },
  { key: 'images', token: 'images', label: 'Images', icon: ImageIcon, pushedLabel: 'pushed', pendingLabel: 'pending' },
];

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  try {
    return n.toLocaleString('en-IN');
  } catch {
    return String(n);
  }
}

/** Read a per-entity count block from the status payload (shapes differ slightly
 *  per entity — products/images use staged/approved, all carry pushed/pending). */
function countsFor(
  status: PushStatus | null,
  key: EntityKey,
): { pushed: number; pending: number; totalLabel: string; total: number } {
  const c = (status?.counts ?? {}) as Record<string, any>;
  const block = (c[key] ?? {}) as Record<string, number | null | undefined>;
  const pushed = Number(block.pushed ?? 0);
  const pending = Number(block.pending ?? 0);
  // Products -> staged; collections/menus -> total; images -> approved.
  const total = Number(block.staged ?? block.total ?? block.approved ?? 0);
  const totalLabel = key === 'products' ? 'staged' : key === 'images' ? 'approved' : 'total';
  return { pushed, pending, total, totalLabel };
}

// ----------------------------------------------------------------------------
// Gate chip — one for each of the three gates, colour-keyed to armed/blocked.
// ----------------------------------------------------------------------------
function GateChip({
  label,
  on,
  detail,
}: {
  label: string;
  /** null = unknown (backend didn't say), false = blocking, true = armed. */
  on: boolean | null | undefined;
  detail?: string;
}) {
  const armed = on === true;
  const unknown = on === null || on === undefined;
  const cls = armed
    ? 'bg-green-100 text-green-800 border-green-200'
    : unknown
      ? 'bg-gray-100 text-gray-600 border-gray-200'
      : 'bg-amber-100 text-amber-900 border-amber-300';
  return (
    <div className={'rounded-lg border px-3 py-2 ' + cls}>
      <div className="flex items-center gap-1.5 text-xs font-semibold">
        {armed ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
        {label}
      </div>
      <div className="mt-0.5 text-[11px] opacity-90">{detail ?? (armed ? 'armed' : unknown ? 'unknown' : 'blocking')}</div>
    </div>
  );
}

export default function OnlineShopifySyncPage() {
  const toast = useToast();
  const { hasRole } = useAuth();
  // Go-live is integration-critical -> SUPERADMIN / ADMIN only (matches the
  // backend push router gate). This only controls whether the affordance is
  // clickable; the BACKEND triple-gate is the real control.
  const canGoLive = hasRole(['SUPERADMIN', 'ADMIN']);

  const bannerRef = useRef<OnlineStoreSyncBannerHandle>(null);

  const [status, setStatus] = useState<PushStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState<SyncHealth | null>(null);
  const [parity, setParity] = useState<SyncParity | null>(null);
  const [drift, setDrift] = useState<SyncDrift | null>(null);
  const [diagLoading, setDiagLoading] = useState(true);

  // Per-entity dry-run state: which entity is running + its last sweep result.
  const [running, setRunning] = useState<EntityKey | null>(null);
  const [sweeps, setSweeps] = useState<Partial<Record<EntityKey, PushSweepResult>>>({});
  const [goingLive, setGoingLive] = useState(false);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    try {
      const s = await pushApi.getStatus();
      setStatus(s);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDiagnostics = useCallback(async () => {
    setDiagLoading(true);
    try {
      const [h, p, d] = await Promise.all([
        syncHealthApi.getSyncHealth(),
        syncHealthApi.getParity(),
        syncHealthApi.getDrift(),
      ]);
      setHealth(h);
      setParity(p);
      setDrift(d);
    } finally {
      setDiagLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    loadDiagnostics();
  }, [loadStatus, loadDiagnostics]);

  const refreshAll = () => {
    loadStatus();
    loadDiagnostics();
    bannerRef.current?.refresh();
  };

  const isLive = !!status?.mode?.is_live;

  // Run a DRY-RUN (or, if the gates are armed, the real) push for one entity via
  // the shared engine. It is SIMULATED whenever the backend reports DARK, so this
  // is safe to run at any time. The returned mode tells the truth either way.
  const runEntity = async (ent: EntityDef) => {
    setRunning(ent.key);
    try {
      const res = await pushApi.pushAllPending(ent.token, 100);
      setSweeps((prev) => ({ ...prev, [ent.key]: res }));
      const where = res.mode?.mode === 'LIVE' ? 'LIVE push' : 'dry-run (SIMULATED)';
      const s = res.summary?.[ent.token] ?? {};
      toast.success(
        `${ent.label}: ${where} — ${res.pushed_count ?? 0} processed` +
          (s?.failed ? ` (${s.failed} failed)` : ''),
      );
      // A push may have written back Shopify ids (LIVE) or cleared nothing
      // (dry-run) — refresh counts + the banner either way.
      loadStatus();
      bannerRef.current?.refresh();
    } catch (e: any) {
      toast.error(`${ent.label}: push failed — ${e?.response?.data?.detail || e?.message || 'error'}`);
    } finally {
      setRunning(null);
    }
  };

  // The "Go live" cutover push. Enabled ONLY when the backend reports the triple
  // gate armed AND the viewer is ADMIN/SUPERADMIN. Even then it just calls the
  // SAME all-pending endpoint — the backend gate is the real control. We never
  // arm anything client-side.
  const goLive = async () => {
    if (!isLive || !canGoLive) return;
    const ok = window.confirm(
      'LIVE Shopify push — this writes products, collections, menus and images to the ' +
        'live storefront (bettervision.in). This is final and owner-armed at the ' +
        'storefront cutover. Continue?',
    );
    if (!ok) return;
    setGoingLive(true);
    try {
      const res = await pushApi.pushAllPending(undefined, 500);
      const where = res.mode?.mode === 'LIVE' ? 'LIVE' : 'dry-run (SIMULATED)';
      toast.success(`Cutover push (${where}): ${res.pushed_count ?? 0} objects processed`);
      loadStatus();
      loadDiagnostics();
      bannerRef.current?.refresh();
    } catch (e: any) {
      toast.error(`Cutover push failed — ${e?.response?.data?.detail || e?.message || 'error'}`);
    } finally {
      setGoingLive(false);
    }
  };

  const mode = status?.mode;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-1">
        <Link
          to="/online-store"
          className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 mb-2"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Online Store
        </Link>
      </div>
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <RefreshCw className="w-5 h-5" /> Shopify sync
        </h1>
        <button
          type="button"
          onClick={refreshAll}
          disabled={loading || diagLoading}
          className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5 disabled:opacity-60"
        >
          <RefreshCw className={'w-4 h-4 ' + (loading || diagLoading ? 'animate-spin' : '')} /> Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        The single-writer push of products, collections, menus and images to Shopify. Every action here is a
        SIMULATED dry-run unless the owner has armed the live gates at the storefront cutover — nothing reaches
        the live storefront (bettervision.in) until then.
      </p>

      {/* Prominent DARK / LIVE banner (shared component). */}
      <OnlineStoreSyncBanner ref={bannerRef} className="mb-4" />

      {/* ---- Gate posture panel — the three gates + effective mode ---------- */}
      <section className="mb-6 rounded-xl border border-gray-200 bg-white p-4">
        <div className="flex items-center justify-between gap-2 mb-3">
          <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4" /> Push gates
          </h2>
          {loading ? (
            <span className="inline-flex items-center gap-1 text-xs text-gray-400">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> loading
            </span>
          ) : (
            <span
              className={
                'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold border ' +
                (isLive
                  ? 'bg-green-100 text-green-800 border-green-300'
                  : 'bg-amber-100 text-amber-900 border-amber-300')
              }
            >
              {isLive ? <Zap className="w-3.5 h-3.5" /> : <ShieldAlert className="w-3.5 h-3.5" />}
              {isLive ? 'LIVE — writes reach the storefront' : 'DRY-RUN — preview only'}
            </span>
          )}
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <GateChip
            label="IMS_SHOPIFY_WRITES"
            on={mode?.writes_enabled}
            detail={mode?.writes_enabled ? 'writes enabled' : 'writes off (single-writer kill-switch)'}
          />
          <GateChip
            label="DISPATCH_MODE"
            on={mode?.dispatch_mode ? mode.dispatch_mode === 'live' : null}
            detail={mode?.dispatch_mode ? `mode = "${mode.dispatch_mode}"` : 'unknown'}
          />
          <GateChip
            label="Shopify credentials"
            on={mode?.creds_present}
            detail={mode?.creds_present ? 'shop_url + token set' : 'not configured'}
          />
        </div>
        {mode?.single_writer_note && (
          <p className="mt-3 text-[11px] text-gray-500">{mode.single_writer_note}</p>
        )}
        {mode?.api_version && (
          <p className="mt-1 text-[11px] text-gray-400">Shopify Admin API {mode.api_version}</p>
        )}
        {status && status.db_connected === false && (
          <p className="mt-2 inline-flex items-center gap-1 text-[11px] text-amber-700">
            <AlertTriangle className="w-3.5 h-3.5" /> Push store unavailable (no DB) — counts show zero.
          </p>
        )}
      </section>

      {/* ---- Per-entity counts + DRY-RUN buttons --------------------------- */}
      <section className="mb-6">
        <h2 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
          <Play className="w-4 h-4" /> Per-entity dry-run
          <span className="text-[11px] font-normal text-gray-400">
            (simulated preview — safe; runs the real engine only when the gates are armed)
          </span>
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {ENTITIES.map((ent) => {
            const EntIcon = ent.icon;
            const { pushed, pending, total, totalLabel } = countsFor(status, ent.key);
            const sweep = sweeps[ent.key];
            const busy = running === ent.key;
            return (
              <div key={ent.key} className="rounded-xl border border-gray-200 bg-white p-4 flex flex-col">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-bv-red-50 text-bv-red-600">
                      <EntIcon className="w-4 h-4" />
                    </span>
                    <h3 className="text-sm font-semibold text-gray-900">{ent.label}</h3>
                  </div>
                  <button
                    type="button"
                    onClick={() => runEntity(ent)}
                    disabled={busy || loading}
                    className={
                      'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium border disabled:opacity-60 ' +
                      (isLive
                        ? 'bg-green-600 text-white border-green-600 hover:bg-green-700'
                        : 'bg-gray-900 text-white border-gray-900 hover:bg-gray-800')
                    }
                    title={isLive ? 'Push pending (LIVE — writes to Shopify)' : 'Dry-run pending (SIMULATED — no Shopify call)'}
                  >
                    {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                    {isLive ? 'Push' : 'Dry-run'}
                  </button>
                </div>
                <div className="flex items-center gap-4 text-xs text-gray-600">
                  <span>
                    <span className="text-sm font-semibold text-gray-900">{fmt(total)}</span> {totalLabel}
                  </span>
                  <span>
                    <span className="text-sm font-semibold text-green-700">{fmt(pushed)}</span> {ent.pushedLabel}
                  </span>
                  <span>
                    <span className="text-sm font-semibold text-amber-700">{fmt(pending)}</span> {ent.pendingLabel}
                  </span>
                </div>

                {/* Last sweep result (the returned plan / outcome). */}
                {sweep && (
                  <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-2.5">
                    <div className="flex items-center gap-1.5 text-[11px] font-semibold text-gray-700">
                      {sweep.mode?.mode === 'LIVE' ? (
                        <span className="inline-flex items-center gap-1 text-green-700">
                          <Zap className="w-3 h-3" /> LIVE result
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-gray-600">
                          <ShieldAlert className="w-3 h-3" /> dry-run plan (SIMULATED)
                        </span>
                      )}
                      <span className="text-gray-400">· {sweep.pushed_count ?? 0} processed</span>
                    </div>
                    {Array.isArray(sweep.results) && sweep.results.length > 0 ? (
                      <ul className="mt-1.5 space-y-1 max-h-40 overflow-auto">
                        {sweep.results.slice(0, 12).map((r, i) => (
                          <li
                            key={(r.target_id ?? '') + i}
                            className="text-[11px] text-gray-600 flex items-center gap-1"
                          >
                            {r.ok ? (
                              <CheckCircle2 className="w-3 h-3 text-green-600 shrink-0" />
                            ) : (
                              <XCircle className="w-3 h-3 text-amber-600 shrink-0" />
                            )}
                            <span className="truncate">{formatPushResult(r.entity + ' ' + (r.target_id ?? ''), r)}</span>
                          </li>
                        ))}
                        {sweep.results.length > 12 && (
                          <li className="text-[11px] text-gray-400">
                            +{sweep.results.length - 12} more…
                          </li>
                        )}
                      </ul>
                    ) : (
                      <p className="mt-1 text-[11px] text-gray-500">
                        Nothing pending to push for {ent.label.toLowerCase()}.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {/* ---- Read-only diagnostics tiles (SUPERADMIN; fail-soft) ----------- */}
      <section className="mb-6">
        <h2 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
          <Activity className="w-4 h-4" /> Sync diagnostics
          <span className="text-[11px] font-normal text-gray-400">(read-only)</span>
        </h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {/* Sync health */}
          <DiagTile title="Sync health" loading={diagLoading} unavailable={health?.unavailable}>
            <DiagRow
              label="Last successful sync"
              value={health?.last_successful_shopify_sync_at ? new Date(health.last_successful_shopify_sync_at).toLocaleString('en-IN') : '—'}
            />
            <DiagRow label="Online configured" value={health?.online_configured ? 'yes' : 'no'} />
            <DiagRow
              label="Oversell misses"
              value={health?.stock_miss?.checked ? fmt(health.stock_miss.unresolved) : '—'}
              warn={!!health?.stock_miss?.unresolved}
            />
            <DiagRow
              label="Failed webhooks"
              value={health?.webhooks?.checked ? fmt(health.webhooks.failed) : '—'}
              warn={!!health?.webhooks?.failed}
            />
          </DiagTile>

          {/* Parity */}
          <DiagTile title="Parity (IMS vs Shopify)" loading={diagLoading} unavailable={parity?.unavailable}>
            {parity?.parity?.entities ? (
              Object.entries(parity.parity.entities).map(([name, v]) => (
                <DiagRow
                  key={name}
                  label={name.replace(/_/g, ' ')}
                  value={`${fmt(v?.pushed)} / ${fmt(v?.total)}`}
                  warn={!!v?.missing}
                />
              ))
            ) : (
              <p className="text-[11px] text-gray-500">No parity data.</p>
            )}
            {parity?.uploads_audit?.checked && (
              <DiagRow
                label="Local /uploads/ images"
                value={fmt(parity.uploads_audit.local_url_count)}
                warn={!!parity.uploads_audit.local_url_count}
              />
            )}
          </DiagTile>

          {/* Drift */}
          <DiagTile title="Dual-writer drift" loading={diagLoading} unavailable={drift?.unavailable}>
            {drift?.checked ? (
              <>
                <DiagRow label="Scanned" value={fmt(drift.counts?.scanned)} />
                <DiagRow label="Drifted gids" value={fmt(drift.counts?.drifted)} warn={!!drift.counts?.drifted} />
                {Array.isArray(drift.drifted) && drift.drifted.length > 0 && (
                  <ul className="mt-1 space-y-0.5">
                    {drift.drifted.slice(0, 5).map((d, i) => (
                      <li key={(d.gid ?? '') + i} className="text-[11px] text-amber-700 truncate">
                        {d.sku || d.gid}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : (
              <p className="text-[11px] text-gray-500">
                {drift?.reason || 'Drift check skipped (needs Shopify credentials).'}
              </p>
            )}
          </DiagTile>
        </div>
      </section>

      {/* ---- Go live (owner-armed cutover) --------------------------------- */}
      <section className="rounded-xl border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-2">
          <Rocket className="w-4 h-4" /> Storefront cutover
        </h2>
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 mb-3">
          <p className="text-xs text-amber-900 font-medium flex items-center gap-1.5">
            <ShieldAlert className="w-3.5 h-3.5" />
            Live push is final and owner-armed at the storefront cutover.
          </p>
          <p className="mt-1 text-[11px] text-amber-800">
            This button can only push live when the owner has armed all three gates on the server
            (IMS_SHOPIFY_WRITES=1, DISPATCH_MODE=live, Shopify credentials set). It never arms them from here —
            the server gate is the real control. Until then this is disabled.
          </p>
        </div>
        <button
          type="button"
          onClick={goLive}
          disabled={!isLive || !canGoLive || goingLive || loading}
          className={
            'inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold border disabled:cursor-not-allowed ' +
            (isLive && canGoLive
              ? 'bg-green-600 text-white border-green-600 hover:bg-green-700'
              : 'bg-gray-100 text-gray-400 border-gray-200')
          }
          title={
            !canGoLive
              ? 'Requires ADMIN or SUPERADMIN'
              : !isLive
                ? 'Disabled — the live gates are not armed on the server'
                : 'Push all pending objects to the live storefront'
          }
        >
          {goingLive ? <Loader2 className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
          {isLive ? 'Push all pending to live storefront' : 'Go live (gates not armed)'}
        </button>
        {!canGoLive && (
          <p className="mt-2 text-[11px] text-gray-500">
            You need the ADMIN or SUPERADMIN role to run the cutover push.
          </p>
        )}
      </section>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Small read-only diagnostic tile + row primitives.
// ----------------------------------------------------------------------------
function DiagTile({
  title,
  loading,
  unavailable,
  children,
}: {
  title: string;
  loading?: boolean;
  unavailable?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <h3 className="text-xs font-semibold text-gray-700 mb-2">{title}</h3>
      {loading ? (
        <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> loading…
        </div>
      ) : unavailable ? (
        <p className="text-[11px] text-gray-400">Unavailable (needs SUPERADMIN, or not deployed).</p>
      ) : (
        <div className="space-y-1">{children}</div>
      )}
    </div>
  );
}

function DiagRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2 text-[11px]">
      <span className="text-gray-500 capitalize">{label}</span>
      <span className={'font-medium ' + (warn ? 'text-amber-700' : 'text-gray-900')}>{value}</span>
    </div>
  );
}
