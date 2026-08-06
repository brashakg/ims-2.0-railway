// ============================================================================
// IMS 2.0 - Online Store - Online Orders  (BVI Phase 3b)
// ============================================================================
// Surfaces the Shopify orders that have flowed into the IMS books. A Shopify
// ORDER webhook lands in `webhook_inbox` (HMAC-verified) and the Phase-3b mapper
// (drained by NEXUS) turns it into a CANONICAL IMS order tagged channel="ONLINE"
// so online sales reach Orders + Finance exactly once (count-once / idempotent on
// the Shopify order id). This screen lists those orders and gives a
// SUPERADMIN/ADMIN "Re-map" action for any Shopify order that FAILED to map (so
// an operator can retry after a fix).
//
// BACKEND IS THE SOURCE OF LIST TRUTH (online-screens audit wave 2, RC-H):
//   * FAILED/PENDING rows come from the backend (`failed` in the envelope):
//     webhook_inbox order payloads with no matching orders doc, each with an
//     honest map_error (OS-011 -- previously dead UI, failures were invisible).
//   * Re-map success is the backend's explicit `ok` verdict -- a mapper "skipped"
//     can no longer toast a false success (OS-011).
//   * Rx FLAG-AND-HOLD is visible (amber "Rx HOLD" chip + filter) and clearable
//     via a gated action once the prescription is captured (OS-012).
//   * HISTORICAL (pre-IMS import) orders wear a grey "Historical" badge instead
//     of the green "In books" (OS-042 -- they settled outside IMS books).
//   * Search runs server-side; the list pages through the real total instead of
//     silently capping at 500 rows (OS-044). Rows arrive server-projected -- no
//     raw line items / tax tables / payments in the browser (OS-063).
//
// This page calls the orders endpoints DIRECTLY via the shared axios client (the
// generic onlineStore.ts adapter is owned by a sibling change and its OnlineOrder
// shape drops the rx/hold/status fields this screen now renders).
//
// FAIL-SOFT: the read degrades to a friendly "coming online" note (never a white
// screen); actions toast the backend error. Gated SUPERADMIN / ADMIN /
// CATALOG_MANAGER / DESIGN_MANAGER at the route (App.tsx); Re-map + Clear-hold
// are further gated to SUPERADMIN / ADMIN (backend-matched). Light theme only.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ShoppingBag,
  ArrowLeft,
  RefreshCw,
  Loader2,
  Search,
  Info,
  User,
  AlertTriangle,
  CheckCircle2,
  Clock,
  History,
  RotateCw,
  Send,
  ShieldCheck,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import api from '../../services/api/client';
import { formatDateIST, formatTimeIST } from '../../utils/datetime';

// ---------------------------------------------------------------------------
// Row model + fetch (page-local; the backend list is server-projected truth).
// ---------------------------------------------------------------------------
type MapStatus = 'MAPPED' | 'FAILED' | 'PENDING';

interface OrderRow {
  id: string | null;
  order_number: string | null;
  shopify_order_id: string | null;
  shopify_order_name: string | null;
  customer_name: string | null;
  customer_phone: string | null;
  customer_email: string | null;
  items_count: number;
  grand_total: number | null;
  currency: string | null;
  order_status: string | null;
  payment_status: string | null;
  fulfillment_status: string | null;
  shopify_fulfillment_id: string | null;
  shopify_fulfillment_pushed_at: string | null;
  map_status: MapStatus;
  map_error: string | null;
  placed_at: string | null;
  /** Clinical FLAG-AND-HOLD: spectacle-lens order without a captured Rx. */
  rx_hold: boolean;
  rx_hold_cleared: boolean;
  rx_hold_reasons: string[];
}

function toRow(o: Record<string, any>): OrderRow {
  let mapStatus = (o.map_status ?? null) as MapStatus | null;
  if (mapStatus !== 'MAPPED' && mapStatus !== 'FAILED' && mapStatus !== 'PENDING') {
    mapStatus = o.order_id || o.order_number ? 'MAPPED' : o.map_error ? 'FAILED' : 'PENDING';
  }
  const reasons = Array.isArray(o.rx_hold_reasons)
    ? o.rx_hold_reasons.map((r: unknown) => String(r)).filter(Boolean)
    : [];
  if (!reasons.length && o.rx_hold_reason) reasons.push(String(o.rx_hold_reason));
  return {
    id: o.order_id != null ? String(o.order_id) : null,
    order_number: o.order_number ?? null,
    shopify_order_id: o.shopify_order_id != null ? String(o.shopify_order_id) : null,
    shopify_order_name: o.shopify_order_name ?? null,
    customer_name: o.customer_name ?? null,
    customer_phone: o.customer_phone ?? null,
    customer_email: o.customer_email ?? null,
    items_count: typeof o.items_count === 'number' ? o.items_count : 0,
    grand_total: typeof o.grand_total === 'number' ? o.grand_total : null,
    currency: o.currency ?? 'INR',
    order_status: o.status ?? o.order_status ?? null,
    payment_status: o.payment_status ?? null,
    fulfillment_status: o.fulfillment_status ?? null,
    shopify_fulfillment_id:
      o.shopify_fulfillment_id != null ? String(o.shopify_fulfillment_id) : null,
    shopify_fulfillment_pushed_at: o.shopify_fulfillment_pushed_at ?? null,
    map_status: mapStatus,
    map_error: o.map_error ?? null,
    placed_at: o.placed_at ?? o.created_at ?? null,
    rx_hold: !!(o.fulfillment_hold || o.rx_pending),
    rx_hold_cleared: !!o.rx_hold_cleared,
    rx_hold_reasons: reasons,
  };
}

const PAGE_SIZE = 100;

interface OrdersPage {
  rows: OrderRow[];
  failed: OrderRow[];
  failedCount: number;
  total: number;
  /** True count of orders with an active Rx hold across the caller's WHOLE
   *  scope (server-computed, honours status/date/search but not pagination or
   *  the rx_hold filter itself -- PR #947 follow-up 2). Never limited to the
   *  loaded page, unlike the old client-side count over `allRows`. */
  rxHoldCount: number;
  available: boolean;
}

/** Fetch one page. NEVER throws: any error (403 outside the backend gate, 404 on
 *  a stale deploy) resolves to an unavailable result so the screen always
 *  renders the friendly note instead of a white screen.
 *  `rxHoldOnly` requests the server-side ?rx_hold=true filter (PR #947 follow-up
 *  2) so switching to the Rx-hold tab sees every held order across the whole
 *  scope, not just whichever ones happened to already be on a loaded page. */
async function fetchOrders(
  offset: number,
  search: string,
  rxHoldOnly = false,
): Promise<OrdersPage> {
  try {
    const params: Record<string, string | number | boolean> = { limit: PAGE_SIZE, offset };
    const q = search.trim();
    if (q) params.search = q;
    if (rxHoldOnly) params.rx_hold = true;
    const res = await api.get('/online-store/orders', { params });
    const data = (res?.data ?? {}) as Record<string, any>;
    const rows = (Array.isArray(data.orders) ? data.orders : []).map(toRow);
    const failed = (Array.isArray(data.failed) ? data.failed : []).map(toRow);
    return {
      rows,
      failed,
      failedCount:
        typeof data.failed_count === 'number'
          ? data.failed_count
          : failed.filter((f: OrderRow) => f.map_status === 'FAILED').length,
      total: typeof data.total === 'number' ? data.total : rows.length,
      rxHoldCount:
        typeof data.rx_hold_count === 'number'
          ? data.rx_hold_count
          : rows.filter((r: OrderRow) => r.rx_hold).length,
      available: true,
    };
  } catch {
    return { rows: [], failed: [], failedCount: 0, total: 0, rxHoldCount: 0, available: false };
  }
}

// Re-map outcomes the backend counts as "the order is in the books". Fallback
// only -- the backend's explicit `ok` verdict wins when present.
const REMAP_OK = ['created', 'duplicate', 'replayed', 'status_synced'];

// ---------------------------------------------------------------------------
// Map-outcome presentation (drives the row badge + the Re-map affordance).
// ---------------------------------------------------------------------------
const MAP_META: Record<
  MapStatus,
  { label: string; chip: string; icon: typeof CheckCircle2 }
> = {
  MAPPED: {
    label: 'In books',
    chip: 'bg-green-100 text-green-800 border-green-200',
    icon: CheckCircle2,
  },
  FAILED: {
    label: 'Map failed',
    chip: 'bg-red-100 text-red-700 border-red-200',
    icon: AlertTriangle,
  },
  PENDING: {
    label: 'Pending',
    chip: 'bg-amber-100 text-amber-800 border-amber-200',
    icon: Clock,
  },
};

// Grey badge for pre-IMS imported orders: they are settled OUTSIDE the IMS books
// (finance excludes them), so the green "In books" would state the opposite of
// the truth (OS-042).
const HISTORICAL_META = {
  label: 'Historical (pre-IMS)',
  chip: 'bg-gray-100 text-gray-600 border-gray-200',
  icon: History,
};

type MapFilter = 'ALL' | MapStatus | 'RX_HOLD';

const MAP_FILTERS: { key: MapFilter; label: string }[] = [
  { key: 'ALL', label: 'All' },
  { key: 'MAPPED', label: 'In books' },
  { key: 'FAILED', label: 'Map failed' },
  { key: 'PENDING', label: 'Pending' },
  { key: 'RX_HOLD', label: 'Rx hold' },
];

function fmtMoney(amount: number | null | undefined, currency: string | null | undefined): string {
  if (amount === null || amount === undefined) return '—';
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: currency || 'INR',
      maximumFractionDigits: 0,
    }).format(Math.round(amount));
  } catch {
    return String(Math.round(amount));
  }
}

/** Humanise a raw status token (PAID / financial_status / fulfillment_status)
 *  into Title Case ("partially_refunded" -> "Partially refunded"). */
function humanise(s: string | null | undefined): string | null {
  if (!s) return null;
  const t = String(s).replace(/[_-]+/g, ' ').trim().toLowerCase();
  if (!t) return null;
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** Colour a payment/fulfillment token green when it reads as "done", amber for
 *  partial/pending, red for bad, neutral otherwise — purely cosmetic, fail-soft
 *  to neutral. ORDER MATTERS (OS-041 + review round): the NEGATIVE branches run
 *  FIRST — amber catches every partial/pending state ('partially_paid',
 *  'partially_fulfilled', 'unfulfilled') and red catches the bad ones
 *  ('unpaid', 'refunded', 'cancelled') — before the green branch, whose plain
 *  'paid'/'fulfilled' tokens would otherwise substring-match inside them and
 *  paint a not-fully-paid state success-green on a money-adjacent screen. */
function statusChipClass(s: string | null | undefined): string {
  const t = (s || '').toLowerCase();
  if (/partial|pending|authorized|unfulfilled/.test(t))
    return 'bg-amber-100 text-amber-800 border-amber-200';
  if (/refund|cancel|void|fail|unpaid/.test(t))
    return 'bg-red-100 text-red-700 border-red-200';
  if (/paid|fulfilled|complete|captured|success/.test(t))
    return 'bg-green-100 text-green-800 border-green-200';
  return 'bg-gray-100 text-gray-600 border-gray-200';
}

// ===========================================================================
// Page
// ===========================================================================
export default function OnlineOrdersPage() {
  const toast = useToast();
  const { hasRole } = useAuth();

  // Re-mapping a failed order re-runs ingestion into the books, and clearing an
  // Rx hold makes a clinical order deliverable -> SUPERADMIN / ADMIN only
  // (matches the backend route gates).
  const canAct = hasRole(['SUPERADMIN', 'ADMIN']);

  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [failedRows, setFailedRows] = useState<OrderRow[]>([]);
  const [failedCount, setFailedCount] = useState(0);
  const [total, setTotal] = useState(0);
  // True count of orders on an active Rx hold across the caller's WHOLE scope
  // (server-computed -- PR #947 follow-up 2). Updated on every load/loadMore so
  // the banner/chip never lag behind what's actually on the server, unlike the
  // old client-side count over only the rows the page happened to have loaded.
  const [rxHoldCount, setRxHoldCount] = useState(0);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [filter, setFilter] = useState<MapFilter>('ALL');
  const [search, setSearch] = useState('');
  // The search term actually sent to the backend (debounced, server-side).
  const [committedSearch, setCommittedSearch] = useState('');
  // shopify_order_id currently being re-mapped (disables that row's button).
  const [remappingId, setRemappingId] = useState<string | null>(null);
  // order_id currently having its Rx hold cleared.
  const [clearingId, setClearingId] = useState<string | null>(null);

  // Debounce the search box into the committed (server-side) term.
  useEffect(() => {
    const t = setTimeout(() => setCommittedSearch(search), 400);
    return () => clearTimeout(t);
  }, [search]);

  // Selecting the "Rx hold" tab asks the SERVER for ?rx_hold=true (PR #947
  // follow-up 2) instead of filtering whatever page happened to be loaded, so
  // the view is complete across the whole scope, not just loaded rows.
  const rxHoldOnly = filter === 'RX_HOLD';

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await fetchOrders(0, committedSearch, rxHoldOnly);
      setOrders(page.rows);
      setFailedRows(page.failed);
      setFailedCount(page.failedCount);
      setTotal(page.total);
      setRxHoldCount(page.rxHoldCount);
      setAvailable(page.available);
    } finally {
      setLoading(false);
    }
  }, [committedSearch, rxHoldOnly]);

  useEffect(() => {
    load();
  }, [load]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    try {
      const page = await fetchOrders(orders.length, committedSearch, rxHoldOnly);
      setOrders((prev) => {
        // De-dupe on append (PR #947 follow-up 5): offset-based pagination reads
        // `orders.length` as the next offset, but a webhook burst landing
        // between the initial load and this Load-more can insert new rows and
        // shift the created_at-desc ordering underneath that now-stale offset —
        // the row sitting at the boundary can be re-fetched and appended a
        // second time. Key on id (booked rows) or shopify_order_id (unbooked
        // rows never carry an id); a row with neither is kept as-is since
        // duplication can't be determined safely for it.
        const keyOf = (o: OrderRow) => o.id ?? (o.shopify_order_id ? `sid:${o.shopify_order_id}` : null);
        const seen = new Set(prev.map(keyOf).filter((k): k is string => k != null));
        const deduped = page.rows.filter((o) => {
          const key = keyOf(o);
          if (key == null) return true;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
        return [...prev, ...deduped];
      });
      setTotal(page.total);
      setRxHoldCount(page.rxHoldCount);
    } finally {
      setLoadingMore(false);
    }
  }, [orders.length, committedSearch, rxHoldOnly]);

  // Failed/pending (unbooked) rows first — they are the ones needing attention.
  const allRows = useMemo(() => [...failedRows, ...orders], [failedRows, orders]);

  const counts = useMemo(() => {
    const c: Record<MapFilter, number> = { ALL: allRows.length, MAPPED: 0, FAILED: 0, PENDING: 0, RX_HOLD: rxHoldCount };
    for (const o of allRows) {
      c[o.map_status] = (c[o.map_status] ?? 0) + 1;
    }
    return c;
  }, [allRows, rxHoldCount]);

  const visible = useMemo(() => {
    if (filter === 'ALL') return allRows;
    if (filter === 'RX_HOLD') return allRows.filter((o) => o.rx_hold);
    return allRows.filter((o) => o.map_status === filter);
  }, [allRows, filter]);

  const handleRemap = useCallback(
    async (order: OrderRow) => {
      const sid = order.shopify_order_id;
      if (!sid) {
        toast.error('No Shopify order id on this row to re-map.');
        return;
      }
      setRemappingId(sid);
      try {
        const res = await api.post(`/online-store/orders/remap/${encodeURIComponent(sid)}`);
        const data = (res?.data ?? {}) as Record<string, any>;
        const result = (data.result ?? {}) as Record<string, any>;
        // The backend's explicit verdict wins; the status-set check is only a
        // fallback for a stale deploy. A mapper "skipped" is NOT a success.
        const ok =
          typeof data.ok === 'boolean'
            ? data.ok
            : REMAP_OK.includes(String(result.status || ''));
        if (ok) {
          toast.success(
            `Order re-mapped into the books${result.invoice_number ? ` (invoice ${result.invoice_number})` : ''}.`,
          );
        } else {
          const why = data.map_error || result.error || result.reason || '';
          toast.warning(`Re-map did not book this order${why ? `: ${why}` : '.'}`);
        }
        await load();
      } catch (e) {
        const msg =
          (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
          (e as Error)?.message ||
          'Re-map failed';
        toast.error(typeof msg === 'string' ? msg : 'Re-map failed');
      } finally {
        setRemappingId(null);
      }
    },
    [toast, load],
  );

  const handleClearHold = useCallback(
    async (order: OrderRow) => {
      if (!order.id) return;
      const sure = window.confirm(
        'Clear the Rx hold on this order?\n\nConfirm only after the prescription has been captured or verified — the order then becomes deliverable.',
      );
      if (!sure) return;
      setClearingId(order.id);
      try {
        await api.post(`/online-store/orders/${encodeURIComponent(order.id)}/clear-rx-hold`);
        toast.success('Rx hold cleared — the order can now be fulfilled.');
        await load();
      } catch (e) {
        const msg =
          (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
          (e as Error)?.message ||
          'Could not clear the hold';
        toast.error(typeof msg === 'string' ? msg : 'Could not clear the hold');
      } finally {
        setClearingId(null);
      }
    },
    [toast, load],
  );

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header + breadcrumb */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
            <Link to="/online-store" className="inline-flex items-center gap-1 hover:text-gray-700">
              <ArrowLeft className="w-3.5 h-3.5" /> Online Store
            </Link>
            <span>/</span>
            <span className="text-gray-700">Orders</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <ShoppingBag className="w-5 h-5" /> Online orders
          </h1>
        </div>
        <button
          type="button"
          onClick={load}
          className="btn-outline inline-flex items-center gap-1.5 text-sm"
          title="Reload"
        >
          <RefreshCw className={'w-4 h-4 ' + (loading ? 'animate-spin' : '')} /> Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Orders placed on the storefront flow into the IMS books as they happen — each one becomes a
        regular order tagged <span className="font-medium text-gray-700">Online</span>, counted once.
        Anything that could not be matched shows here so you can fix it and re-map.
      </p>

      {/* Re-map queue banner — only when something failed. */}
      {failedCount > 0 && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-red-100 text-red-700 border border-red-200 px-2.5 py-1 text-xs font-semibold">
            <AlertTriangle className="w-3.5 h-3.5" /> {failedCount} order{failedCount !== 1 ? 's' : ''} not in the books
          </span>
          <span className="text-sm text-red-900">
            {canAct
              ? 'Re-map each one after fixing the cause (e.g. a missing product or customer).'
              : 'An admin can re-map these after fixing the cause.'}
          </span>
        </div>
      )}

      {/* Rx-hold banner — held spectacle-lens orders must not be dispensed. */}
      {counts.RX_HOLD > 0 && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 text-amber-800 border border-amber-200 px-2.5 py-1 text-xs font-semibold">
            <ShieldCheck className="w-3.5 h-3.5" /> {counts.RX_HOLD} order{counts.RX_HOLD !== 1 ? 's' : ''} on Rx hold
          </span>
          <span className="text-sm text-amber-900">
            Spectacle-lens orders without a captured prescription. Collect the Rx, then clear the
            hold to make them deliverable.
          </span>
        </div>
      )}

      {/* Map-outcome filter chip row */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {MAP_FILTERS.map((f) => {
          const active = filter === f.key;
          return (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={active ? 'ims-chip ims-chip--on' : 'ims-chip'}
            >
              {f.label}
              <span className="inline-flex items-center justify-center min-w-[1.25rem] rounded-full px-1 text-[11px] bg-gray-100 text-gray-600">
                {counts[f.key]}
              </span>
            </button>
          );
        })}
      </div>

      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by order, Shopify ref, or customer…"
            className="input-field w-full pl-9"
          />
        </div>
        {!loading && available && (
          <span className="text-xs text-gray-500">
            Showing {orders.length.toLocaleString('en-IN')} of {total.toLocaleString('en-IN')} booked
            order{total !== 1 ? 's' : ''}
            {failedRows.length > 0
              ? ` · ${failedRows.length.toLocaleString('en-IN')} unbooked`
              : ''}
          </span>
        )}
        {!canAct && (
          <span className="inline-flex items-center gap-1.5 text-xs text-gray-500">
            <Info className="w-3.5 h-3.5" />
            Re-mapping and clearing an Rx hold are limited to admins.
          </span>
        )}
      </div>

      {/* List */}
      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading online orders…
        </div>
      ) : !available ? (
        <div className="rounded-xl border border-blue-200 bg-blue-50 p-6 text-center">
          <ShoppingBag className="w-10 h-10 mx-auto mb-2 text-blue-400" />
          <p className="text-sm font-medium text-blue-900">Online orders are coming online</p>
          <p className="text-xs text-blue-700 mt-1 max-w-md mx-auto">
            Live online orders appear here once the order-ingestion service is deployed. The storefront
            keeps taking orders in the meantime.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center text-gray-500">
          <ShoppingBag className="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p className="text-sm">
            {search || filter !== 'ALL'
              ? 'No online orders match this view.'
              : 'No online orders yet. New storefront orders will show up here.'}
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100">
          {visible.map((order, idx) => {
            const isHistorical = (order.order_status || '').toUpperCase() === 'HISTORICAL';
            const meta = isHistorical ? HISTORICAL_META : MAP_META[order.map_status];
            const MapIcon = meta.icon;
            const placed = order.placed_at;
            const payLabel = humanise(order.payment_status);
            const fulLabel = humanise(order.fulfillment_status);
            const ref = order.shopify_order_name || (order.shopify_order_id ? `#${order.shopify_order_id}` : null);
            const isRemapping = !!order.shopify_order_id && remappingId === order.shopify_order_id;
            const isClearing = !!order.id && clearingId === order.id;
            return (
              <div
                key={order.id || order.shopify_order_id || idx}
                className="p-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
              >
                {/* Left: identity + customer + items */}
                <div className="flex items-start gap-3 min-w-0">
                  <span
                    className={
                      'inline-flex items-center justify-center w-10 h-10 rounded-lg shrink-0 border ' +
                      meta.chip
                    }
                  >
                    <MapIcon className="w-5 h-5" />
                  </span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <p className="font-medium text-gray-900 truncate">
                        {order.order_number || ref || 'Unmapped order'}
                      </p>
                      {ref && order.order_number && (
                        // Plain reference text: there is no in-app deep link to
                        // the Shopify admin, so no link affordance is signalled
                        // (OS-060 — the old ExternalLink icon promised a click
                        // that did nothing).
                        <span className="text-xs text-gray-500" title="Shopify order reference">
                          {ref}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 text-sm text-gray-500 mt-0.5 min-w-0">
                      <User className="w-3 h-3 shrink-0" />
                      <span className="truncate">
                        {order.customer_name || 'Guest shopper'}
                        {order.customer_phone ? ` · ${order.customer_phone}` : ''}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5">
                      {placed ? `${formatDateIST(placed)} at ${formatTimeIST(placed)}` : '—'}
                      {' · '}
                      {order.items_count ?? 0} item{(order.items_count ?? 0) !== 1 ? 's' : ''}
                    </p>
                    {order.map_status === 'FAILED' && order.map_error && (
                      <p className="text-xs text-red-600 mt-1 max-w-md">{order.map_error}</p>
                    )}
                  </div>
                </div>

                {/* Right: money + statuses + actions */}
                <div className="flex flex-col items-start sm:items-end gap-2 shrink-0 pl-[3.25rem] sm:pl-0">
                  <p className="font-bold text-gray-900">{fmtMoney(order.grand_total, order.currency)}</p>
                  <div className="flex flex-wrap items-center gap-1.5 sm:justify-end">
                    <span
                      className={
                        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ' +
                        meta.chip
                      }
                      title={
                        isHistorical
                          ? 'Imported pre-IMS order — settled outside the IMS books (excluded from finance)'
                          : undefined
                      }
                    >
                      {meta.label}
                    </span>
                    {order.rx_hold && (
                      <span
                        className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold bg-amber-100 text-amber-800 border-amber-200"
                        title={
                          order.rx_hold_reasons.length
                            ? order.rx_hold_reasons.join(' · ')
                            : 'Spectacle-lens order held: no valid prescription captured yet'
                        }
                      >
                        <ShieldCheck className="w-3 h-3" /> Rx HOLD
                      </span>
                    )}
                    {payLabel && (
                      <span
                        className={
                          'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ' +
                          statusChipClass(order.payment_status)
                        }
                        title="Payment status"
                      >
                        {payLabel}
                      </span>
                    )}
                    {fulLabel && (
                      <span
                        className={
                          'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ' +
                          statusChipClass(order.fulfillment_status)
                        }
                        title="What Shopify reports back to IMS (inbound fulfillment status)"
                      >
                        Shopify says: {fulLabel}
                      </span>
                    )}
                    {/* Outbound axis: did IMS tell Shopify this order shipped?
                        Gated on the OUTBOUND push stamp only — an inbound
                        fulfillments/create webhook also writes the fulfillment
                        id, which is Shopify telling IMS, not the reverse
                        (OS-043). */}
                    {(() => {
                      const pushedAt = order.shopify_fulfillment_pushed_at;
                      if (pushedAt) {
                        return (
                          <span
                            className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium bg-green-100 text-green-800 border-green-200"
                            title={`IMS notified the website at ${formatDateIST(pushedAt)} ${formatTimeIST(pushedAt)}`}
                          >
                            <Send className="w-3 h-3" /> Website notified
                            {` · ${formatTimeIST(pushedAt)}`}
                          </span>
                        );
                      }
                      if (order.shopify_fulfillment_id) {
                        return (
                          <span
                            className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium bg-gray-100 text-gray-600 border-gray-200"
                            title="The website reported this order fulfilled (inbound webhook). IMS did not send this update."
                          >
                            Fulfilled on website
                          </span>
                        );
                      }
                      return (
                        <span
                          className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium bg-gray-100 text-gray-500 border-gray-200"
                          title="IMS has not sent a fulfillment update to the website (not shipped yet, or the live gates are dark)"
                        >
                          Website · —
                        </span>
                      );
                    })()}
                  </div>
                  <div className="flex items-center gap-2">
                    {order.map_status === 'FAILED' && canAct && order.shopify_order_id && (
                      <button
                        type="button"
                        onClick={() => handleRemap(order)}
                        disabled={isRemapping}
                        className="btn-outline inline-flex items-center gap-1.5 text-xs disabled:opacity-60"
                        title="Re-run ingestion for this Shopify order"
                      >
                        {isRemapping ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <RotateCw className="w-3.5 h-3.5" />
                        )}
                        Re-map
                      </button>
                    )}
                    {order.rx_hold && canAct && order.id && (
                      <button
                        type="button"
                        onClick={() => handleClearHold(order)}
                        disabled={isClearing}
                        className="btn-outline inline-flex items-center gap-1.5 text-xs disabled:opacity-60"
                        title="Release the Rx hold after the prescription is captured/verified"
                      >
                        {isClearing ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="w-3.5 h-3.5" />
                        )}
                        Clear hold
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination: page through the real total instead of capping silently. */}
      {!loading && available && orders.length < total && (
        <div className="mt-4 flex justify-center">
          <button
            type="button"
            onClick={loadMore}
            disabled={loadingMore}
            className="btn-outline inline-flex items-center gap-1.5 text-sm disabled:opacity-60"
          >
            {loadingMore ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Load more ({(total - orders.length).toLocaleString('en-IN')} remaining)
          </button>
        </div>
      )}

      <p className="mt-6 text-xs text-gray-400">
        Online Store module · Orders flow in from the storefront, counted once into the IMS books.
      </p>
    </div>
  );
}
