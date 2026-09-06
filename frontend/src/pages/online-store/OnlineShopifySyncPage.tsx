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
  Tag,
  Play,
  Rocket,
  Activity,
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  History as HistoryIcon,
  MinusCircle,
} from 'lucide-react';
import {
  pushApi,
  syncHealthApi,
  type PushStatus,
  type PushResult,
  type PushSweepResult,
  type PushHistoryResult,
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
type EntityKey = 'products' | 'collections' | 'menus' | 'images' | 'variant-prices';

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
  /** Opt-in resync action with NO status-count block (e.g. variant-prices):
   *  the card shows `note` instead of the staged/pushed/pending triplet. */
  optIn?: boolean;
  /** Explanatory copy for opt-in cards, shown in place of the count triplet. */
  note?: string;
}

const ENTITIES: EntityDef[] = [
  { key: 'products', token: 'products', label: 'Products', icon: Package, pushedLabel: 'pushed', pendingLabel: 'pending' },
  { key: 'collections', token: 'collections', label: 'Collections', icon: Layers, pushedLabel: 'pushed', pendingLabel: 'pending' },
  { key: 'menus', token: 'menus', label: 'Menus', icon: MenuIcon, pushedLabel: 'pushed', pendingLabel: 'pending' },
  { key: 'images', token: 'images', label: 'Images', icon: ImageIcon, pushedLabel: 'pushed', pendingLabel: 'pending' },
  {
    // Opt-in bulk price/compare-at/barcode resync over every product already on
    // Shopify (the "MRP revision" resync). NOT in the default sweep -- a normal
    // product push already carries prices -- so it is its own explicit card.
    key: 'variant-prices',
    token: 'variant-prices',
    label: 'Variant prices',
    icon: Tag,
    pushedLabel: 'resynced',
    pendingLabel: '',
    optIn: true,
    note:
      'Re-push price / compare-at / barcode for EVERY product already on Shopify — the bulk MRP / ' +
      'discount-rule price resync. Pages through the whole mapped set in batches; a product whose ' +
      'variants have no Shopify mapping yet is reported as "no-op", never counted as pushed.',
  },
];

/** An aware ISO instant rendered on the IST clock (the business calendar),
 *  never the viewer's browser zone. */
function fmtIST(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }) + ' IST';
}

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
  const [syncingLive, setSyncingLive] = useState(false);
  // STOCK (2026-09-07, make website quantities real): the one-button pass
  // that writes the pooled quantity of every listing whose number changed.
  const [pushingStock, setPushingStock] = useState(false);
  const [stockResult, setStockResult] = useState<PushResult | null>(null);
  // Variant-prices paged resync progress (OS-017): {done, total} while looping.
  const [resyncProgress, setResyncProgress] = useState<{ done: number; total: number | null } | null>(null);

  // Push history (OS-047): the read-only ONLINE_STORE_PUSH audit ledger.
  const [history, setHistory] = useState<PushHistoryResult | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyFilter, setHistoryFilter] = useState<'all' | 'live' | 'failed'>('all');

  const loadStatus = useCallback(async () => {
    setLoading(true);
    try {
      const s = await pushApi.getStatus();
      setStatus(s);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async (filter?: 'all' | 'live' | 'failed') => {
    const f = filter ?? historyFilter;
    setHistoryLoading(true);
    try {
      const opts: { limit: number; mode?: 'LIVE'; ok?: boolean } = { limit: 50 };
      if (f === 'live') opts.mode = 'LIVE';
      if (f === 'failed') opts.ok = false;
      setHistory(await pushApi.getHistory(opts));
    } finally {
      setHistoryLoading(false);
    }
    // historyFilter is passed explicitly on filter clicks; the deps stay stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyFilter]);

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
    loadHistory();
    // Load once on mount; loadHistory re-fires on filter clicks explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadStatus, loadDiagnostics]);

  const refreshAll = () => {
    loadStatus();
    loadDiagnostics();
    loadHistory();
    bannerRef.current?.refresh();
  };

  const isLive = !!status?.mode?.is_live;

  // Run a DRY-RUN (or, if the gates are armed, the real) push for one entity via
  // the shared engine. It is SIMULATED whenever the backend reports DARK, so this
  // is safe to run at any time. The returned mode tells the truth either way.
  //
  // OS-017: the variant-prices resync PAGES through the WHOLE mapped set. Its
  // eligible set (products already on Shopify) never shrinks after a resync, so
  // a single capped call would rescan the same first ~100 products forever —
  // instead we loop with the backend's next_offset until it comes back null,
  // and report updated / no-op / failed separately (a no-op is NOT a success).
  const runEntity = async (ent: EntityDef) => {
    setRunning(ent.key);
    try {
      if (ent.key === 'variant-prices') {
        let offset = 0;
        let total: number | null = null;
        let updated = 0;
        let noop = 0;
        let failed = 0;
        let processed = 0;
        let last: PushSweepResult | null = null;
        setResyncProgress({ done: 0, total: null });
        // Hard page cap: 80 pages x 100 = 8,000 products (catalog is ~4.4k).
        for (let page = 0; page < 80; page++) {
          const res = await pushApi.pushAllPending(ent.token, 100, offset);
          last = res;
          const s = res.summary?.[ent.token] ?? {};
          updated += Number(s?.pushed ?? 0);
          noop += Number(s?.noop ?? 0);
          failed += Number(s?.failed ?? 0);
          processed += Number(res.pushed_count ?? 0);
          total = res.eligible_total ?? total;
          setResyncProgress({ done: processed, total });
          if (res.next_offset === null || res.next_offset === undefined) break;
          offset = res.next_offset;
        }
        // #950: the loop caps at 80 pages. If it exited with next_offset still
        // non-null, the cap (not an exhausted set) ended it — NOT everything was
        // resynced. Surface it like the OS-046 safety-cap case (limit_reached +
        // the run-again toast) instead of silently reporting a finished sweep.
        const capHit =
          last != null && last.next_offset !== null && last.next_offset !== undefined;
        if (last) {
          // Store the AGGREGATED tallies (the last page's rows for the detail
          // list, the whole run's counts for the honest headline).
          setSweeps((prev) => ({
            ...prev,
            [ent.key]: {
              ...last!,
              pushed_count: processed,
              limit_reached: capHit,
              summary: { [ent.token]: { pushed: updated, failed, noop } },
            },
          }));
        }
        const where = last?.mode?.mode === 'LIVE' ? 'LIVE push' : 'dry-run (SIMULATED)';
        const msg =
          `${ent.label}: ${where} — ${updated} updated, ${noop} no-op (variants not mapped), ` +
          `${failed} failed of ${total ?? processed} mapped products`;
        if (failed > 0) toast.warning(msg);
        else toast.success(msg);
        // OS-046: a capped run must never read as complete — same run-again cue.
        if (capHit) {
          toast.info(
            `${ent.label}: stopped at the page safety cap — not everything was resynced; ` +
              'run again to continue.',
          );
        }
      } else {
        const res = await pushApi.pushAllPending(ent.token, 100);
        setSweeps((prev) => ({ ...prev, [ent.key]: res }));
        const where = res.mode?.mode === 'LIVE' ? 'LIVE push' : 'dry-run (SIMULATED)';
        const s = res.summary?.[ent.token] ?? {};
        // WHAT DID NOT GO. A refusal (no photograph) and a withheld publish (the
        // product reached Shopify but no customer can see it) are neither
        // successes nor Shopify breakages — and a press that reports only its
        // successes is the "pending: 0 over an empty queue" lie one screen over.
        const refused = Number(s?.refused_no_photo ?? 0);
        const withheld = Number(s?.publish_withheld ?? 0);
        const heldDown = Number(s?.taken_down_skipped ?? 0);
        const archived = Number(s?.archived_not_listed ?? 0);
        const oldPrice = Number(s?.price_not_synced ?? 0);
        // WHY. Counts alone ("6 NOT made visible") send the owner hunting; the
        // first failed row's server message rides on the toast itself. A row
        // that is ok but carries a code (live at the OLD price) counts too.
        const firstFail = (res.results ?? []).find((r) => (!r.ok || r.code) && (r.error || r.reason));
        const why = firstFail
          ? ` — ${firstFail.error || firstFail.reason}${firstFail.code ? ` [${firstFail.code}]` : ''}`
          : '';
        const msg =
          `${ent.label}: ${where} — ${res.pushed_count ?? 0} processed` +
          (s?.failed ? ` · ${s.failed} failed` : '') +
          (s?.noop ? ` · ${s.noop} no-op` : '') +
          (refused ? ` · ${refused} refused (no photograph)` : '') +
          (withheld ? ` · ${withheld} NOT made visible` : '') +
          (heldDown ? ` · ${heldDown} skipped (taken down)` : '') +
          (archived ? ` · ${archived} archived (not listed)` : '') +
          (oldPrice ? ` · ${oldPrice} at the OLD price (price not synced)` : '') +
          why;
        if (refused || withheld || s?.failed) toast.warning(msg);
        else toast.success(msg);
        // OS-046: never let a capped sweep read as complete. The batch cap is a
        // PRODUCTS number; collections/menus/images stop at the request limit.
        if (res.limit_reached) {
          const capText =
            ent.token === 'products' ? `the safety cap of ${res.batch_cap ?? 25} products` : 'the page safety cap';
          // "Run again to continue" promises progress a repeat press can only
          // make if THIS press made some. When nothing went live, say so
          // instead — the reasons above are what has to change, not the count
          // of presses.
          toast.info(
            Number(res.pushed_count ?? 0) > 0
              ? `${ent.label}: stopped at ${capText} — run again to continue (the pending count ` +
                  'below shows the remainder).'
              : `${ent.label}: stopped at ${capText}, but NOTHING went live this press — ` +
                  'the lines above say why. Press again to try the products behind these.',
          );
        }
      }
      // A push may have written back Shopify ids (LIVE) or cleared nothing
      // (dry-run) — refresh counts + banner + history either way.
      loadStatus();
      loadHistory();
      bannerRef.current?.refresh();
    } catch (e: any) {
      toast.error(`${ent.label}: push failed — ${e?.response?.data?.detail || e?.message || 'error'}`);
    } finally {
      setRunning(null);
      setResyncProgress(null);
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
      // OS-046: a capped run must never read as "cutover done". Products stop at
      // the backend's hard batch cap (one press = a bounded number of listings in
      // front of customers); everything else at the 500-object valve.
      if (res.limit_reached) {
        toast.info(
          `Stopped at a safety cap (${res.batch_cap ?? 25} PRODUCTS per press; other objects ` +
            'stop at 500) — NOT everything was pushed. ' +
            (Number(res.pushed_count ?? 0) > 0
              ? 'Click again to continue; the pending counts below show the remainder.'
              : 'NOTHING went live this press — the lines below say why. Click again to try ' +
                'the objects behind these.'),
        );
      }
      loadStatus();
      loadDiagnostics();
      loadHistory();
      bannerRef.current?.refresh();
    } catch (e: any) {
      toast.error(`Cutover push failed — ${e?.response?.data?.detail || e?.message || 'error'}`);
    } finally {
      setGoingLive(false);
    }
  };

  // "Sync live products now" (owner ruling 2026-09-06): the SAME function the
  // 01:00 / 09:00 IST schedule runs. Re-pushes only products ALREADY on
  // Shopify that were edited in IMS; never a first publish. SIMULATED when
  // the gates are off, so it is always safe; a LIVE press confirms first.
  const syncLiveNow = async () => {
    if (!canGoLive || syncingLive) return;
    if (isLive) {
      const ok = window.confirm(
        'LIVE sync — every product already on bettervision.in that was edited in IMS will be ' +
          'updated on Shopify now. Products never pushed are left alone. Continue?',
      );
      if (!ok) return;
    }
    setSyncingLive(true);
    try {
      const { run } = await pushApi.syncLiveNow();
      const where = run?.mode === 'LIVE' ? 'LIVE' : 'dry-run (SIMULATED)';
      toast.success(
        `Live sync (${where}): ${run?.pushed_ok ?? 0} updated, ${run?.failed ?? 0} failed, ` +
          `${run?.awaiting_first_publish ?? 0} awaiting first publish`,
      );
      if (run?.limit_reached) {
        toast.info(`Stopped at the per-run cap (${run.limit ?? '?'} products) — run again to continue.`);
      }
      loadStatus();
      loadHistory();
      bannerRef.current?.refresh();
    } catch (e: any) {
      toast.error(`Live sync failed — ${e?.response?.data?.detail || e?.message || 'error'}`);
    } finally {
      setSyncingLive(false);
    }
  };

  const pushStock = async () => {
    setPushingStock(true);
    try {
      const res = await pushApi.pushStock();
      setStockResult(res);
      const p = (res.payload ?? {}) as Record<string, any>;
      const where = res.mode === 'LIVE' ? 'LIVE' : 'simulated';
      if (res.ok) {
        toast.success(
          `Stock (${where}): ${p.changed ?? 0} of ${p.candidates ?? 0} listings changed` +
            (res.mode === 'LIVE' ? `, ${p.synced ?? 0} written` : ' — nothing sent (dry-run)'),
        );
      } else {
        toast.warning(`Stock not written — ${res.error || res.code || 'see result'}`);
      }
      refreshAll();
    } catch (e: any) {
      toast.error(`Stock push failed — ${e?.response?.data?.detail || e?.message || 'error'}`);
    } finally {
      setPushingStock(false);
    }
  };

  const mode = status?.mode;
  const liveSync = status?.live_sync ?? null;
  const lastRun = liveSync?.last_run ?? null;
  const stockPayload = (stockResult?.payload ?? null) as Record<string, any> | null;

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
          {/* THE THIRD DOOR, shown BEFORE the press. The three gates above say
              nothing about it: with no resolvable Online Store publication every
              press honestly withholds and NOTHING goes live, however green the
              other three are. */}
          <GateChip
            label="Online Store channel"
            on={!!mode?.online_store_publication_id}
            detail={
              mode?.online_store_publication_id
                ? `publication ${mode.online_store_publication_source ?? 'resolved'}`
                : 'NOT resolved — presses will publish nothing'
            }
          />
          {/* THE FOURTH DOOR (2026-09-07): where the website's quantity lives.
              Unresolved => every stock write refuses with a code; never guessed. */}
          <GateChip
            label="Online stock location"
            on={!!mode?.online_location_id}
            detail={
              mode?.online_location_id
                ? `location ${mode.online_location_source ?? 'resolved'}`
                : mode?.online_location_code
                  ? mode.online_location_code
                  : 'NOT resolved — stock will not be written'
            }
          />
        </div>
        {mode?.is_live && !mode?.online_location_id && (
          <p className="mt-3 inline-flex items-start gap-1 text-[11px] text-amber-800">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span>
              {mode?.online_location_error ||
                'No Shopify location fulfils online orders, so quantities cannot be written. Enable one in Shopify admin (Settings > Locations) or pin SHOPIFY_ONLINE_LOCATION_ID.'}
            </span>
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={pushStock}
            disabled={pushingStock || loading || !canGoLive}
            className={
              'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium border disabled:opacity-60 ' +
              (isLive
                ? 'bg-green-600 text-white border-green-600 hover:bg-green-700'
                : 'bg-gray-900 text-white border-gray-900 hover:bg-gray-800')
            }
            title={
              isLive
                ? 'Write the pooled quantity of every changed listing (LIVE)'
                : 'Preview the stock pass (SIMULATED — no Shopify call)'
            }
          >
            {pushingStock ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Package className="w-3.5 h-3.5" />}
            {isLive ? 'Push stock' : 'Dry-run stock'}
          </button>
          <span className="text-[11px] text-gray-500">
            Pooled on-hand of every physical shop, per SKU, written to Shopify for listings whose
            number changed since the last send. A product push and the all-pending press run this too.
          </span>
        </div>
        {stockResult && (
          <p className={'mt-2 text-[11px] ' + (stockResult.ok ? 'text-gray-600' : 'text-amber-800')}>
            Last stock pass ({stockResult.mode}): {stockPayload?.changed ?? 0} of {stockPayload?.candidates ?? 0}{' '}
            listings changed
            {stockResult.mode === 'LIVE'
              ? `, ${stockPayload?.synced ?? 0} written, ${stockPayload?.failed ?? 0} failed`
              : ' (dry-run, nothing sent)'}
            {stockResult.error ? ` — ${stockResult.error}` : ''}
          </p>
        )}
        {mode?.is_live && !mode?.online_store_publication_id && (
          <p className="mt-3 inline-flex items-start gap-1 text-[11px] text-amber-800">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span>
              No Online Store sales channel is resolved, so a press can create the product but
              cannot make it visible on bettervision.in. Set
              SHOPIFY_ONLINE_STORE_PUBLICATION_ID (or grant the app the read_publications and
              write_publications scopes) before pressing.
            </span>
          </p>
        )}
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

      {/* ---- Live sync: the scheduled re-push of already-live products ------ */}
      <section className="mb-6 rounded-xl border border-gray-200 bg-white p-4" data-testid="live-sync-card">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-2">
          <div>
            <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
              <RefreshCw className="w-4 h-4" /> Live sync
              <span className="text-[11px] font-normal text-gray-400">
                (edited products already on Shopify — never a first publish)
              </span>
            </h2>
            {liveSync ? (
              <p className="mt-1 text-xs text-gray-600">
                {liveSync.enabled ? (
                  <>
                    Schedule ON — daily at {liveSync.slots.join(', ')} IST. Next run:{' '}
                    <strong>{liveSync.next_slot?.label ?? '—'}</strong>.
                  </>
                ) : (
                  <>Schedule OFF — only the button below syncs.</>
                )}{' '}
                Up to {liveSync.max_products_per_run} products per run.
              </p>
            ) : (
              <p className="mt-1 text-xs text-gray-400">Schedule status unavailable.</p>
            )}
          </div>
          <button
            type="button"
            onClick={syncLiveNow}
            disabled={!canGoLive || syncingLive || loading}
            title={
              !canGoLive
                ? 'Only an Admin or Superadmin can run the sync'
                : isLive
                  ? 'Re-push every edited product already on the live storefront'
                  : 'Gates not armed — runs as a SIMULATED dry-run'
            }
            className="btn-primary text-sm"
          >
            {syncingLive ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-2" />}
            {syncingLive ? 'Syncing…' : 'Sync live products now'}
          </button>
        </div>

        {lastRun ? (
          <div className="mt-2 rounded-lg bg-gray-50 p-3 text-xs text-gray-700">
            <p>
              <span className="font-medium">Last run:</span>{' '}
              {lastRun.started_at ? fmtIST(lastRun.started_at) : '—'} · {lastRun.trigger === 'scheduled' ? 'scheduled' : 'manual'}
              {lastRun.actor && lastRun.trigger !== 'scheduled' ? ` by ${lastRun.actor}` : ''} ·{' '}
              {lastRun.mode === 'LIVE' ? 'LIVE' : 'dry-run'}
            </p>
            <p className="mt-1">
              <span className="font-medium">{fmt(lastRun.pushed_ok)}</span> updated ·{' '}
              <span className={(lastRun.failed ?? 0) > 0 ? 'font-medium text-red-700' : 'font-medium'}>
                {fmt(lastRun.failed)}
              </span>{' '}
              failed · <span className="font-medium">{fmt(lastRun.awaiting_first_publish)}</span> awaiting first publish
              {(lastRun.refused_no_photo ?? 0) > 0 && <> · {fmt(lastRun.refused_no_photo)} refused (no photo)</>}
              {(lastRun.publish_withheld ?? 0) > 0 && <> · {fmt(lastRun.publish_withheld)} withheld</>}
              {(lastRun.price_not_synced ?? 0) > 0 && <> · {fmt(lastRun.price_not_synced)} at the OLD price</>}
              {lastRun.limit_reached && <> · stopped at the {lastRun.limit ?? '?'}-product cap</>}
            </p>
            {(lastRun.failures?.length ?? 0) > 0 && (
              <ul className="mt-2 space-y-1" aria-label="Live sync failures">
                {lastRun.failures!.map((f, i) => (
                  <li key={f.product_id ?? i} className="flex flex-wrap items-baseline gap-x-2 text-red-700">
                    <XCircle className="w-3.5 h-3.5 shrink-0 self-center" />
                    <span className="font-medium">{f.sku || f.product_id || '?'}</span>
                    {f.name && <span className="text-gray-600">{f.name}</span>}
                    {f.code && <code className="rounded bg-red-50 px-1 text-[11px]">{f.code}</code>}
                    <span>{f.error || f.reason || 'failed'}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <p className="mt-2 text-xs text-gray-400">No live sync has run yet.</p>
        )}
        <p className="mt-2 text-[11px] text-gray-400">
          Configured in{' '}
          {hasRole(['SUPERADMIN']) ? (
            <Link to="/settings/shopify-live-sync" className="text-blue-700 hover:underline">
              Settings &gt; Shopify live sync
            </Link>
          ) : (
            <>Settings &gt; Shopify live sync (Superadmin)</>
          )}
          .
        </p>
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
                {ent.optIn ? (
                  <>
                    <p className="text-[11px] leading-relaxed text-gray-500">{ent.note}</p>
                    {busy && resyncProgress && (
                      <p className="mt-1.5 inline-flex items-center gap-1.5 text-[11px] font-medium text-gray-700">
                        <Loader2 className="w-3 h-3 animate-spin" />
                        Resyncing… {fmt(resyncProgress.done)}
                        {resyncProgress.total !== null ? ` of ${fmt(resyncProgress.total)}` : ''} mapped products
                      </p>
                    )}
                  </>
                ) : (
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
                )}

                {/* Last sweep result (the returned plan / outcome). */}
                {sweep && (
                  <div className="mt-3 rounded-lg border border-gray-100 bg-gray-50 p-2.5">
                    <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold text-gray-700">
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
                      {/* Honest breakdown: no-ops are NOT successes (OS-017). */}
                      {(() => {
                        const noop = Number((sweep.summary?.[ent.token]?.noop ?? 0) as number);
                        return noop > 0 ? (
                          <span
                            className="inline-flex items-center gap-1 text-gray-500"
                            title="Nothing was sent for these — their variants have no Shopify mapping (gid) yet, or no usable price"
                          >
                            <MinusCircle className="w-3 h-3" /> {fmt(noop)} no-op (not pushed)
                          </span>
                        ) : null;
                      })()}
                      {/* THE REFUSALS AND THE WITHHOLDINGS. The backend counts
                          them honestly; a screen that renders only the successes
                          turns that honesty back into "35 processed" over 25 that
                          went and 10 that never did. */}
                      {(() => {
                        const refused = Number(
                          (sweep.summary?.[ent.token]?.refused_no_photo ?? 0) as number,
                        );
                        return refused > 0 ? (
                          <span
                            className="inline-flex items-center gap-1 text-amber-700"
                            title="Refused: these products have no photograph. A listing with an empty grey box is never published — add a photo and press again."
                          >
                            · {fmt(refused)} refused (no photograph)
                          </span>
                        ) : null;
                      })()}
                      {(() => {
                        const withheld = Number(
                          (sweep.summary?.[ent.token]?.publish_withheld ?? 0) as number,
                        );
                        return withheld > 0 ? (
                          <span
                            className="inline-flex items-center gap-1 text-red-700"
                            title="These reached Shopify but were NOT made visible (no Online Store channel, the photograph did not attach, or the price could not be proved). They are still queued — fix the cause and press again."
                          >
                            <AlertTriangle className="w-3 h-3" /> {fmt(withheld)} NOT made visible
                          </span>
                        ) : null;
                      })()}
                      {(() => {
                        const oldPrice = Number(
                          (sweep.summary?.[ent.token]?.price_not_synced ?? 0) as number,
                        );
                        return oldPrice > 0 ? (
                          <span
                            className="inline-flex items-center gap-1 text-red-700"
                            title="Live on the website at the OLD price: the price change did not reach Shopify. They are still queued - press again, or the next scheduled sync retries them."
                          >
                            <AlertTriangle className="w-3 h-3" /> {fmt(oldPrice)} at the OLD price
                          </span>
                        ) : null;
                      })()}
                      {(() => {
                        const held = Number(
                          (sweep.summary?.[ent.token]?.taken_down_skipped ?? 0) as number,
                        );
                        return held > 0 ? (
                          <span
                            className="inline-flex items-center gap-1 text-gray-500"
                            title="Taken off the website by hand. A bulk sweep never puts one back — open the product and press Send to website when it is ready."
                          >
                            · {fmt(held)} skipped (taken down)
                          </span>
                        ) : null;
                      })()}
                      {(() => {
                        const archived = Number(
                          (sweep.summary?.[ent.token]?.archived_not_listed ?? 0) as number,
                        );
                        return archived > 0 ? (
                          <span
                            className="inline-flex items-center gap-1 text-gray-500"
                            title="Retired products: the update reached Shopify, but an archived product is not on the storefront — nobody can find it."
                          >
                            · {fmt(archived)} archived (not listed)
                          </span>
                        ) : null;
                      })()}
                      {/* Push-locked SKUs the sweep excluded (from summary.products). */}
                      {(() => {
                        const blocked = Number(
                          (sweep.summary?.[ent.token]?.blocked_skipped ?? 0) as number,
                        );
                        return blocked > 0 ? (
                          <span
                            className="inline-flex items-center gap-1 text-amber-700"
                            title="Push-locked SKUs skipped by this sweep"
                          >
                            · {fmt(blocked)} blocked (skipped)
                          </span>
                        ) : null;
                      })()}
                    </div>
                    {/* OS-046: a capped sweep must not read as complete. */}
                    {sweep.limit_reached && (
                      <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-amber-700">
                        <AlertTriangle className="w-3 h-3" />{' '}
                        {Number(sweep.pushed_count ?? 0) > 0
                          ? 'Stopped at the safety cap — run again to continue.'
                          : 'Stopped at the safety cap, but NOTHING went live this press — the lines above say why. Press again to try the products behind these.'}
                      </p>
                    )}
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
              label="Last sync (orders/stock)"
              value={health?.last_successful_shopify_sync_at ? new Date(health.last_successful_shopify_sync_at).toLocaleString('en-IN') : '—'}
            />
            <DiagRow
              label="Last LIVE catalog push"
              value={
                health?.catalog_push?.last_pushed_at
                  ? new Date(health.catalog_push.last_pushed_at).toLocaleString('en-IN')
                  : '—'
              }
            />
            <DiagRow
              label="Catalog mapped to Shopify"
              value={health?.online_configured ? 'yes' : 'no'}
              warn={health ? !health.online_configured : false}
            />
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

      {/* ---- Push history (read-only ONLINE_STORE_PUSH audit ledger) ------- */}
      <section className="mb-6 rounded-xl border border-gray-200 bg-white p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            <HistoryIcon className="w-4 h-4" /> Push history
            <span className="text-[11px] font-normal text-gray-400">
              (read-only — every push attempt, LIVE or dry-run, from the audit ledger)
            </span>
          </h2>
          <div className="inline-flex rounded-lg border border-gray-200 overflow-hidden">
            {(
              [
                { key: 'all', label: 'All' },
                { key: 'live', label: 'LIVE only' },
                { key: 'failed', label: 'Failures' },
              ] as const
            ).map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => {
                  setHistoryFilter(f.key);
                  void loadHistory(f.key);
                }}
                className={
                  'px-3 py-1 text-xs font-medium ' +
                  (historyFilter === f.key
                    ? 'bg-gray-900 text-white'
                    : 'bg-white text-gray-600 hover:bg-gray-50')
                }
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        {historyLoading ? (
          <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> loading history…
          </div>
        ) : !history || !history.available ? (
          <p className="text-[11px] text-gray-400">
            {history?.failure === 'forbidden'
              ? 'Push history is visible to ADMIN and SUPERADMIN only.'
              : 'Push history unavailable (endpoint not served by this deploy, or the audit store is unreachable).'}
          </p>
        ) : history.entries.length === 0 ? (
          <p className="text-[11px] text-gray-500">
            {historyFilter === 'failed'
              ? 'No failed pushes recorded.'
              : historyFilter === 'live'
                ? 'No LIVE pushes recorded yet.'
                : 'No pushes recorded yet.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-gray-400 border-b border-gray-100">
                  <th className="py-1.5 pr-3 font-medium">When</th>
                  <th className="py-1.5 pr-3 font-medium">Mode</th>
                  <th className="py-1.5 pr-3 font-medium">Entity</th>
                  <th className="py-1.5 pr-3 font-medium">Target</th>
                  <th className="py-1.5 pr-3 font-medium">Action</th>
                  <th className="py-1.5 pr-3 font-medium">By</th>
                  <th className="py-1.5 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {history.entries.map((h, i) => (
                  <tr key={(h.target_id ?? '') + i} className="border-b border-gray-50 last:border-0">
                    <td className="py-1.5 pr-3 whitespace-nowrap text-gray-600">
                      {h.timestamp ? new Date(h.timestamp).toLocaleString('en-IN') : '—'}
                    </td>
                    <td className="py-1.5 pr-3">
                      {h.mode === 'LIVE' ? (
                        <span className="inline-flex items-center gap-0.5 rounded-full bg-green-100 text-green-800 border border-green-200 px-1.5 py-0.5 font-semibold">
                          <Zap className="w-2.5 h-2.5" /> LIVE
                        </span>
                      ) : (
                        <span className="inline-flex items-center rounded-full bg-gray-100 text-gray-600 border border-gray-200 px-1.5 py-0.5 font-medium">
                          {h.mode || 'SIMULATED'}
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-gray-700">{h.entity || '—'}</td>
                    <td className="py-1.5 pr-3 max-w-[220px]">
                      <span className="block truncate text-gray-900" title={h.target_id ?? undefined}>
                        {h.name || h.sku || h.target_id || '—'}
                      </span>
                      {(h.name || h.sku) && h.sku && h.name && (
                        <span className="block truncate text-gray-400">{h.sku}</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-gray-600">{h.push_action || '—'}</td>
                    <td className="py-1.5 pr-3 text-gray-600 whitespace-nowrap">
                      {h.user_name || h.user_id || '—'}
                    </td>
                    <td className="py-1.5">
                      {/* ok + a code (PRICE_NOT_SYNCED: live at the OLD price) is
                          not a clean ok -- it renders like a failure, with the
                          message and the code, never a green tick. */}
                      {h.ok && !h.code ? (
                        <span className="inline-flex items-center gap-1 text-green-700">
                          <CheckCircle2 className="w-3 h-3" /> ok
                        </span>
                      ) : (
                        <span
                          className="inline-flex items-center gap-1 text-amber-700 max-w-[260px]"
                          title={h.error || h.reason || undefined}
                        >
                          <XCircle className="w-3 h-3 shrink-0" />
                          <span className="truncate">
                            {h.error || h.reason || 'failed'}
                            {h.code ? ` [${h.code}]` : ''}
                          </span>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
