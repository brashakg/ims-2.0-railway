// ============================================================================
// IMS 2.0 - Online Store - Online Orders  (BVI Phase 3b)
// ============================================================================
// Surfaces the Shopify orders that have flowed into the IMS books. A Shopify
// ORDER webhook lands in `webhook_inbox` (HMAC-verified) and the Phase-3b mapper
// (drained by NEXUS) turns it into a CANONICAL IMS order tagged channel="ONLINE"
// so online sales reach Orders + Finance exactly once (count-once / idempotent on
// the Shopify order id). This read-only screen lists those orders and gives a
// SUPERADMIN/ADMIN "Re-map" action for any Shopify order that FAILED to map (so
// an operator can retry after a fix).
//
// Why a dedicated screen (not a tab on the POS Orders page): online orders are
// channel-scoped (not store-scoped like POS), carry Shopify provenance + a map
// outcome, and the Re-map retry is integration plumbing that doesn't belong on
// the cashier's Orders list. It lives in the Online Store module next to the rest
// of the BVI merge surfaces.
//
// FAIL-SOFT: the Phase-3b backend router may not be deployed yet. The list read
// degrades to a friendly "coming online" note (never a white screen); the re-map
// write toasts the backend error. Gated SUPERADMIN / ADMIN / CATALOG_MANAGER /
// DESIGN_MANAGER at the route (App.tsx); the in-page Re-map action is further
// gated to SUPERADMIN / ADMIN (matches the backend remap route). Light theme only.

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
  ExternalLink,
  AlertTriangle,
  CheckCircle2,
  Clock,
  RotateCw,
  Send,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import {
  ordersApi,
  type OnlineOrder,
  type OnlineOrderMapStatus,
} from '../../services/api/onlineStore';
import { formatDateIST, formatTimeIST } from '../../utils/datetime';

// ---------------------------------------------------------------------------
// Map-outcome presentation (drives the row badge + the Re-map affordance).
// ---------------------------------------------------------------------------
const MAP_META: Record<
  OnlineOrderMapStatus,
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

type MapFilter = 'ALL' | OnlineOrderMapStatus;

const MAP_FILTERS: MapFilter[] = ['ALL', 'MAPPED', 'FAILED', 'PENDING'];

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
 *  partial, neutral otherwise — purely cosmetic, fail-soft to neutral. */
function statusChipClass(s: string | null | undefined): string {
  const t = (s || '').toLowerCase();
  if (/paid|fulfilled|complete|captured|success/.test(t))
    return 'bg-green-100 text-green-800 border-green-200';
  if (/partial|pending|authorized|unfulfilled/.test(t))
    return 'bg-amber-100 text-amber-800 border-amber-200';
  if (/refund|cancel|void|fail|unpaid/.test(t))
    return 'bg-red-100 text-red-700 border-red-200';
  return 'bg-gray-100 text-gray-600 border-gray-200';
}

// ===========================================================================
// Page
// ===========================================================================
export default function OnlineOrdersPage() {
  const toast = useToast();
  const { hasRole } = useAuth();

  // Re-mapping a failed order re-runs ingestion into the books -> SUPERADMIN /
  // ADMIN only (matches the backend remap route gate).
  const canRemap = hasRole(['SUPERADMIN', 'ADMIN']);

  const [orders, setOrders] = useState<OnlineOrder[]>([]);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<MapFilter>('ALL');
  const [search, setSearch] = useState('');
  // shopify_order_id currently being re-mapped (disables that row's button).
  const [remappingId, setRemappingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Load the whole list once; map-outcome filtering + search are client-side
      // so the chip-row counts stay live without re-fetching per chip.
      const res = await ordersApi.listOnline({ limit: 500 });
      setOrders(res.orders);
      setAvailable(res.available);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Search over the most useful identifiers.
  const searchFiltered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return orders;
    return orders.filter((o) =>
      [
        o.order_number,
        o.shopify_order_name,
        o.shopify_order_id,
        o.customer_name,
        o.customer_phone,
        o.customer_email,
      ]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    );
  }, [orders, search]);

  const counts = useMemo(() => {
    const c: Record<MapFilter, number> = { ALL: searchFiltered.length, MAPPED: 0, FAILED: 0, PENDING: 0 };
    for (const o of searchFiltered) {
      const ms = o.map_status ?? 'PENDING';
      c[ms] = (c[ms] ?? 0) + 1;
    }
    return c;
  }, [searchFiltered]);

  const visible = useMemo(
    () => (filter === 'ALL' ? searchFiltered : searchFiltered.filter((o) => (o.map_status ?? 'PENDING') === filter)),
    [searchFiltered, filter],
  );

  const handleRemap = useCallback(
    async (order: OnlineOrder) => {
      const sid = order.shopify_order_id;
      if (!sid) {
        toast.error('No Shopify order id on this row to re-map.');
        return;
      }
      setRemappingId(sid);
      try {
        const updated = await ordersApi.remap(sid);
        if (updated.map_status === 'FAILED') {
          toast.warning(
            `Re-map still failed${updated.map_error ? `: ${updated.map_error}` : ''}.`,
          );
        } else {
          toast.success(
            `Order re-mapped into the books${updated.order_number ? ` (${updated.order_number})` : ''}.`,
          );
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

  const failedCount = counts.FAILED;

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
            {canRemap
              ? 'Re-map each one after fixing the cause (e.g. a missing product or customer).'
              : 'An admin can re-map these after fixing the cause.'}
          </span>
        </div>
      )}

      {/* Map-outcome filter chip row */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {MAP_FILTERS.map((f) => {
          const active = filter === f;
          const label = f === 'ALL' ? 'All' : MAP_META[f].label;
          return (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={active ? 'ims-chip ims-chip--on' : 'ims-chip'}
            >
              {label}
              <span className="inline-flex items-center justify-center min-w-[1.25rem] rounded-full px-1 text-[11px] bg-gray-100 text-gray-600">
                {counts[f]}
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
        {!canRemap && (
          <span className="inline-flex items-center gap-1.5 text-xs text-gray-500">
            <Info className="w-3.5 h-3.5" />
            Re-mapping a failed order is limited to admins.
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
            const ms = order.map_status ?? 'PENDING';
            const meta = MAP_META[ms];
            const MapIcon = meta.icon;
            const placed = order.placed_at;
            const payLabel = humanise(order.payment_status);
            const fulLabel = humanise(order.fulfillment_status);
            const ref = order.shopify_order_name || (order.shopify_order_id ? `#${order.shopify_order_id}` : null);
            const isRemapping = !!order.shopify_order_id && remappingId === order.shopify_order_id;
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
                        <span className="inline-flex items-center gap-1 text-xs text-gray-500">
                          <ExternalLink className="w-3 h-3" /> {ref}
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
                    {ms === 'FAILED' && order.map_error && (
                      <p className="text-xs text-red-600 mt-1 max-w-md">{order.map_error}</p>
                    )}
                  </div>
                </div>

                {/* Right: money + statuses + (re-map) */}
                <div className="flex flex-col items-start sm:items-end gap-2 shrink-0 pl-[3.25rem] sm:pl-0">
                  <p className="font-bold text-gray-900">{fmtMoney(order.grand_total, order.currency)}</p>
                  <div className="flex flex-wrap items-center gap-1.5 sm:justify-end">
                    <span
                      className={
                        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ' +
                        meta.chip
                      }
                    >
                      {meta.label}
                    </span>
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
                    {/* Outbound: did IMS tell Shopify this order shipped? (PR #933
                        stamps). Fail-soft: absent renders a grey em-dash so this
                        merges safely before or after #933. */}
                    {(() => {
                      const pushedAt = order.shopify_fulfillment_pushed_at;
                      const notified = !!pushedAt || !!order.shopify_fulfillment_id;
                      if (notified) {
                        return (
                          <span
                            className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium bg-green-100 text-green-800 border-green-200"
                            title={
                              pushedAt
                                ? `IMS notified the website at ${formatDateIST(pushedAt)} ${formatTimeIST(pushedAt)}`
                                : 'The website knows this order is fulfilled'
                            }
                          >
                            <Send className="w-3 h-3" /> Website notified
                            {pushedAt ? ` · ${formatTimeIST(pushedAt)}` : ''}
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
                  {ms === 'FAILED' && canRemap && order.shopify_order_id && (
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
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p className="mt-6 text-xs text-gray-400">
        Online Store module · Orders flow in from the storefront, counted once into the IMS books.
      </p>
    </div>
  );
}
