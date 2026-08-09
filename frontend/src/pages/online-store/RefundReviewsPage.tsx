// ============================================================================
// IMS 2.0 - Online Store - Refund reviews  (Shopify refund -> GST credit note)
// ============================================================================
// The ACCOUNTANT-facing consumer for the Shopify refund review queue. A Shopify
// `refunds/create` webhook is turned into a proposed GST credit note + restock
// and, by DEFAULT, parked here for an accountant to CONFIRM (post the credit note
// + restock, reusing the same in-store returns machinery) or REJECT. Without this
// screen those rows were an invisible dead letter -> no GST reversal, no restock.
//
// FAIL-SOFT + HONEST (RC-E): the list read never throws, and the failure reason
// is rendered truthfully — a 403 says "no permission", a 500/network blip says
// "couldn't load" + Retry; ONLY a 404/501 (router genuinely not deployed) shows
// the "coming online" note. Confirm/reject toast the backend result. Gated
// SUPERADMIN / ADMIN / ACCOUNTANT at the route (App.tsx) and in the backend.
// Light theme only. No emojis in code paths that touch Python (this is TSX).

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ReceiptText,
  ArrowLeft,
  RefreshCw,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  Clock,
  EyeOff,
  XCircle,
  User,
  Store,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  refundReviewsApi,
  type OnlineStoreLoadFailure,
  type RefundReview,
} from '../../services/api/onlineStore';
import { formatDateIST } from '../../utils/datetime';

// Status presentation. PENDING/DISCREPANCY/CREDIT_FAILED/NO_CUSTOMER are open;
// POSTED/REJECTED are resolved; UNMATCHED is awaiting its order.
const STATUS_META: Record<string, { label: string; chip: string }> = {
  PENDING: { label: 'Awaiting review', chip: 'bg-amber-100 text-amber-800 border-amber-200' },
  DISCREPANCY: { label: 'Amount mismatch', chip: 'bg-red-100 text-red-700 border-red-200' },
  CREDIT_FAILED: { label: 'Credit failed', chip: 'bg-red-100 text-red-700 border-red-200' },
  NO_CUSTOMER: { label: 'No customer', chip: 'bg-red-100 text-red-700 border-red-200' },
  UNMATCHED: { label: 'Order not found', chip: 'bg-gray-100 text-gray-600 border-gray-200' },
  POSTED: { label: 'Posted', chip: 'bg-green-100 text-green-800 border-green-200' },
  REJECTED: { label: 'Rejected', chip: 'bg-gray-100 text-gray-500 border-gray-200' },
};

const OPEN_STATUSES = ['PENDING', 'DISCREPANCY', 'CREDIT_FAILED', 'NO_CUSTOMER'];

/** Neutral presentation for a status this build doesn't know (OS-062): before,
 *  an unrecognised value borrowed PENDING's amber 'Awaiting review' chip while
 *  the row offered no actions — claiming work was waiting that could not be
 *  done. Render the raw token neutrally instead. */
function metaFor(s: string): { label: string; chip: string; known: boolean } {
  const meta = STATUS_META[s];
  if (meta) return { ...meta, known: true };
  const label = s
    ? s.replace(/[_-]+/g, ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase())
    : 'Unknown status';
  return { label, chip: 'bg-gray-100 text-gray-600 border-gray-200', known: false };
}

type Filter = 'OPEN' | 'PENDING' | 'DISCREPANCY' | 'UNMATCHED' | 'RESOLVED' | 'ALL';
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'OPEN', label: 'Open' },
  { key: 'DISCREPANCY', label: 'Mismatches' },
  { key: 'UNMATCHED', label: 'Order not found' },
  { key: 'RESOLVED', label: 'Resolved' },
  { key: 'ALL', label: 'All' },
];

function fmtMoney(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return String(n);
  }
}

export default function RefundReviewsPage() {
  const toast = useToast();
  const [reviews, setReviews] = useState<RefundReview[]>([]);
  const [total, setTotal] = useState(0);
  const [available, setAvailable] = useState(true);
  // Why the read failed (null when it worked) — mirrors OnlineStorePage RC-E:
  // 'forbidden' / 'error' must NOT render the "coming online" placeholder.
  const [failure, setFailure] = useState<OnlineStoreLoadFailure | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>('OPEN');
  const [actingId, setActingId] = useState<string | null>(null);
  // Refunds confirmed in THIS session whose credit note posted but whose stock
  // did NOT go back. A toast disappears; real goods on the counter with no
  // stock row must stay on screen until the accountant has dealt with them.
  const [restockGaps, setRestockGaps] = useState<{ ref: string; order: string }[]>([]);
  // OS-013 / PR #947 follow-up 5: which reach the backend actually applied
  // ('all-stores' | 'store-scoped' | null before the first load) -- drives the
  // scope hint below instead of a static sentence that could go stale.
  const [scope, setScope] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await refundReviewsApi.list({ limit: 500 });
      setReviews(res.reviews);
      setTotal(res.total);
      setAvailable(res.available);
      setFailure(res.available ? null : (res.reason ?? 'unavailable'));
      setScope(res.scope ?? null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(() => {
    return reviews.filter((r) => {
      const s = (r.status || '').toUpperCase();
      switch (filter) {
        case 'ALL':
          return true;
        case 'OPEN':
          return OPEN_STATUSES.includes(s);
        case 'RESOLVED':
          return s === 'POSTED' || s === 'REJECTED' || !!r.resolved;
        default:
          return s === filter;
      }
    });
  }, [reviews, filter]);

  const openCount = useMemo(
    () => reviews.filter((r) => OPEN_STATUSES.includes((r.status || '').toUpperCase())).length,
    [reviews],
  );

  const act = useCallback(
    async (review: RefundReview, action: 'confirm' | 'reject') => {
      setActingId(review.review_id);
      try {
        if (action === 'confirm') {
          const res = await refundReviewsApi.confirm(review.review_id);
          // HONEST OUTCOME: the credit note posting and the stock restock are
          // two different things and either can fail on its own. This used to
          // toast "…and stock restocked" unconditionally, so an accountant
          // closed the row believing the frame was back on a shelf when the
          // backend had restocked NOTHING (an online order with no fulfilment
          // stamp resolves to no physical store and mints nothing on purpose).
          const result = (res?.result ?? {}) as {
            restock_applied?: boolean;
            restock_store_id?: string | null;
            restock_store_ids?: string[] | null;
            return_id?: string | null;
          };
          const landed =
            result.restock_store_ids && result.restock_store_ids.length > 0
              ? result.restock_store_ids.join(', ')
              : result.restock_store_id || '';
          if (result.restock_applied) {
            toast.success(
              landed
                ? `Credit note posted. Stock put back at ${landed}.`
                : 'Credit note posted and stock restocked.',
            );
          } else {
            const ref = result.return_id || review.review_id;
            toast.warning(
              `Credit note posted, but the returned items were NOT put back into stock (${ref}). ` +
                'A task has been raised — add them at the receiving shop.',
            );
            setRestockGaps((prev) =>
              prev.some((g) => g.ref === ref)
                ? prev
                : [
                    ...prev,
                    {
                      ref,
                      order: review.order_number || review.shopify_order_id || ref,
                    },
                  ],
            );
          }
        } else {
          await refundReviewsApi.reject(review.review_id);
          toast.success('Refund review rejected.');
        }
        await load();
      } catch (e) {
        const msg =
          (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
          (e as Error)?.message ||
          'Action failed';
        toast.error(typeof msg === 'string' ? msg : 'Action failed');
      } finally {
        setActingId(null);
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
            <span className="text-gray-700">Refund reviews</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <ReceiptText className="w-5 h-5" /> Refund reviews
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
        Every online refund from the storefront lands here as a proposed GST credit note. Confirm to
        post the credit note and put the returned stock back, or reject if it should not be booked.
        Nothing hits the books until you confirm.
      </p>
      {/* Scope hint (OS-013 / PR #947 follow-up 5): driven by the envelope's
          `scope` field, not a static sentence — a role the backend actually
          store-bounds (anything but ACCOUNTANT/ADMIN/SUPERADMIN) must not be
          told "spans all stores" when its queue was silently narrowed. */}
      {!loading && available && (
        <p className="-mt-2 mb-4 text-xs text-gray-400 max-w-3xl">
          {scope === 'store-scoped'
            ? 'This queue is limited to your active store — an empty list may mean another store has refunds waiting.'
            : 'This queue spans all stores — online refunds bill under the online store, so nothing here is hidden by your active store.'}
        </p>
      )}

      {/* Stock that did NOT come back. The credit note posted (money is settled)
          but the units were not restocked anywhere, so the goods are physically
          held with no stock row. A deduped task is raised backend-side; this
          keeps it in front of the accountant who just confirmed it too. */}
      {restockGaps.length > 0 && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3">
          <p className="inline-flex items-center gap-1.5 text-sm font-semibold text-red-800">
            <AlertTriangle className="w-4 h-4" /> Stock not put back on{' '}
            {restockGaps.length} refund{restockGaps.length !== 1 ? 's' : ''}
          </p>
          <p className="text-sm text-red-900 mt-1">
            The credit note posted and the customer is refunded, but the returned items could
            not be booked into any shop&apos;s stock — so they are physically with whoever
            received them and IMS has no record of them. Add them at the receiving shop.
          </p>
          <ul className="mt-1.5 text-xs text-red-800 list-disc pl-5">
            {restockGaps.map((g) => (
              <li key={g.ref}>
                {g.order} · {g.ref}
              </li>
            ))}
          </ul>
        </div>
      )}

      {openCount > 0 && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 text-amber-800 border border-amber-200 px-2.5 py-1 text-xs font-semibold">
            <Clock className="w-3.5 h-3.5" /> {openCount} refund{openCount !== 1 ? 's' : ''} awaiting review
          </span>
          <span className="text-sm text-amber-900">
            Confirm to post the credit note + restock, or reject to decline.
          </span>
        </div>
      )}

      {/* Filter chips */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setFilter(f.key)}
            className={filter === f.key ? 'ims-chip ims-chip--on' : 'ims-chip'}
          >
            {f.label}
          </button>
        ))}
        {!loading && available && total > reviews.length && (
          // OS-044 (refund half): the fetch is capped at 500 — say so instead
          // of letting the surplus silently vanish.
          <span className="text-xs text-amber-700">
            Showing the newest {reviews.length.toLocaleString('en-IN')} of{' '}
            {total.toLocaleString('en-IN')} reviews.
          </span>
        )}
      </div>

      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading refund reviews…
        </div>
      ) : failure === 'forbidden' ? (
        // RC-E: a 403 is a PERMISSION state, never "coming soon".
        <div className="rounded-xl border border-gray-200 bg-gray-50 p-6 text-center">
          <EyeOff className="w-10 h-10 mx-auto mb-2 text-gray-400" />
          <p className="text-sm font-medium text-gray-700">No permission for this view</p>
          <p className="text-xs text-gray-500 mt-1 max-w-md mx-auto">
            Your role can't read the refund review queue. The feature itself is running — ask an
            admin if you need access.
          </p>
        </div>
      ) : failure === 'error' ? (
        // RC-E: a real failure gets an honest error + Retry, not a fake state.
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center">
          <AlertTriangle className="w-10 h-10 mx-auto mb-2 text-red-400" />
          <p className="text-sm font-medium text-red-900">Couldn't load refund reviews</p>
          <p className="text-xs text-red-700 mt-1 max-w-md mx-auto">
            The read failed — refunds may be waiting that aren't shown right now.
          </p>
          <button
            type="button"
            onClick={load}
            className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-red-700 hover:text-red-900"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Retry
          </button>
        </div>
      ) : !available ? (
        // ONLY reason==='unavailable' (404/501 — router genuinely not deployed).
        <div className="rounded-xl border border-blue-200 bg-blue-50 p-6 text-center">
          <ReceiptText className="w-10 h-10 mx-auto mb-2 text-blue-400" />
          <p className="text-sm font-medium text-blue-900">Refund reviews are coming online</p>
          <p className="text-xs text-blue-700 mt-1 max-w-md mx-auto">
            Online refunds appear here for review once the refund service is deployed.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center text-gray-500">
          <CheckCircle2 className="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p className="text-sm">
            {filter === 'OPEN' ? 'No refunds awaiting review. Nice and clear.' : 'No refund reviews match this view.'}
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100">
          {visible.map((r) => {
            const s = (r.status || 'PENDING').toUpperCase();
            const meta = metaFor(s);
            const isOpen = OPEN_STATUSES.includes(s);
            const acting = actingId === r.review_id;
            // OS-061: prefer the display name the backend resolves from the
            // stores registry; fall back to the raw code.
            const restockLabel =
              (r as RefundReview & { restock_store_name?: string | null }).restock_store_name ||
              r.restock_store_id;
            const gst = (r.credit_note?.gst_breakup ?? {}) as Record<string, any>;
            const mismatch =
              s === 'DISCREPANCY' &&
              typeof r.shopify_refunded_amount === 'number' &&
              typeof r.gross_refund === 'number';
            return (
              <div
                key={r.review_id}
                className="p-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span
                      className={
                        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ' +
                        meta.chip
                      }
                    >
                      {meta.label}
                    </span>
                    <p className="font-medium text-gray-900 truncate">
                      {r.order_number || (r.shopify_order_id ? `#${r.shopify_order_id}` : 'Refund')}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5 text-sm text-gray-500 mt-1 min-w-0">
                    <User className="w-3 h-3 shrink-0" />
                    <span className="truncate">{r.customer_name || 'Guest shopper'}</span>
                    {r.restock_store_id && (
                      <>
                        <span className="text-gray-300">·</span>
                        <Store className="w-3 h-3 shrink-0" />
                        {/* PROPOSED, not decided: the shop each unit actually
                            goes back to is resolved per unit at confirm time
                            from how the order was fulfilled. Saying "restock
                            <shop>" here read as a settled fact. */}
                        <span
                          className="truncate"
                          title={`Expected to restock into ${r.restock_store_id} (confirmed per unit when you post)`}
                        >
                          likely restock {restockLabel}
                        </span>
                      </>
                    )}
                  </div>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {r.created_at ? formatDateIST(r.created_at) : '—'}
                    {typeof gst.tax === 'number' ? ` · GST ${fmtMoney(gst.tax)}` : ''}
                  </p>
                  {r.note && <p className="text-xs text-gray-500 mt-1 max-w-lg">{r.note}</p>}
                  {mismatch && (
                    <p className="text-xs text-red-600 mt-1">
                      Shopify refunded {fmtMoney(r.shopify_refunded_amount)} but the computed credit
                      note is {fmtMoney(r.gross_refund)} — reconcile before posting.
                    </p>
                  )}
                </div>

                <div className="flex flex-col items-start sm:items-end gap-2 shrink-0">
                  <p className="font-bold text-gray-900">{fmtMoney(r.gross_refund)}</p>
                  {isOpen ? (
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => act(r, 'reject')}
                        disabled={acting}
                        className="btn-outline inline-flex items-center gap-1.5 text-xs disabled:opacity-60"
                        title="Decline this refund (nothing is booked)"
                      >
                        {acting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <XCircle className="w-3.5 h-3.5" />}
                        Reject
                      </button>
                      <button
                        type="button"
                        onClick={() => act(r, 'confirm')}
                        disabled={acting}
                        className="btn-primary inline-flex items-center gap-1.5 text-xs disabled:opacity-60"
                        title="Post the credit note and restock"
                      >
                        {acting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                        Confirm
                      </button>
                    </div>
                  ) : s === 'UNMATCHED' ? (
                    <span className="inline-flex items-center gap-1 text-xs text-gray-500">
                      <AlertTriangle className="w-3.5 h-3.5" /> Awaiting order
                    </span>
                  ) : meta.known ? (
                    <span className="text-xs text-gray-400">Resolved</span>
                  ) : (
                    // OS-062: an unrecognised status is not "resolved" — say
                    // plainly that this build has no action for it.
                    <span className="text-xs text-gray-400">No action available</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <p className="mt-6 text-xs text-gray-400">
        Online Store module · Online refunds become GST credit notes only after an accountant confirms.
      </p>
    </div>
  );
}
