// ============================================================================
// IMS 2.0 - Online Store - Refund reviews  (Shopify refund -> GST credit note)
// ============================================================================
// The ACCOUNTANT-facing consumer for the Shopify refund review queue. A Shopify
// `refunds/create` webhook is turned into a proposed GST credit note + restock
// and, by DEFAULT, parked here for an accountant to CONFIRM (post the credit note
// + restock, reusing the same in-store returns machinery) or REJECT. Without this
// screen those rows were an invisible dead letter -> no GST reversal, no restock.
//
// FAIL-SOFT: the backend router may not be deployed yet; the list degrades to a
// friendly "coming online" note. Confirm/reject toast the backend result. Gated
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
  XCircle,
  User,
  Store,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { refundReviewsApi, type RefundReview } from '../../services/api/onlineStore';
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
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>('OPEN');
  const [actingId, setActingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await refundReviewsApi.list({ limit: 500 });
      setReviews(res.reviews);
      setAvailable(res.available);
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
          await refundReviewsApi.confirm(review.review_id);
          toast.success('Credit note posted and stock restocked.');
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
      </div>

      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading refund reviews…
        </div>
      ) : !available ? (
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
            const meta = STATUS_META[s] || STATUS_META.PENDING;
            const isOpen = OPEN_STATUSES.includes(s);
            const acting = actingId === r.review_id;
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
                        <span className="truncate">restock {r.restock_store_id}</span>
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
                  ) : (
                    <span className="text-xs text-gray-400">Resolved</span>
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
