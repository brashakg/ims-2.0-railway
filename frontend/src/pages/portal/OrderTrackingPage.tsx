// ============================================================================
// IMS 2.0 — Customer Order Tracking (public, tokenized link)
// ============================================================================
// Reached at `/track/:token`. No login: the long unguessable token in the URL
// is the credential (server-side checked). Shows a clean status timeline plus
// the order items as "Brand Category" lines and the store's contact details.
// Never shows price, cost, salesperson, or any internal field.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Loader2, PackageSearch, CheckCircle2, Circle, Clock, Phone, Glasses,
  AlertTriangle, RefreshCw,
} from 'lucide-react';
import { portalApi, type OrderTracking } from '../../services/api/portal';

// Canonical order lifecycle, in display order. We render every step and mark
// the ones that have happened (present in status_history) as complete, with
// the current status highlighted.
const FLOW: Array<{ key: string; label: string }> = [
  { key: 'DRAFT', label: 'Order placed' },
  { key: 'CONFIRMED', label: 'Confirmed' },
  { key: 'PROCESSING', label: 'In progress' },
  { key: 'READY', label: 'Ready for pickup' },
  { key: 'DELIVERED', label: 'Delivered' },
];

function formatDate(value: string | null | undefined): string {
  if (!value) return '';
  try {
    return new Date(value).toLocaleDateString('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric',
    });
  } catch {
    return String(value);
  }
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '';
  try {
    return new Date(value).toLocaleString('en-IN', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return String(value);
  }
}

export default function OrderTrackingPage() {
  const { token } = useParams<{ token: string }>();
  const [data, setData] = useState<OrderTracking | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await portalApi.trackOrder(token);
      setData(resp);
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      setError(
        status === 404
          ? 'We could not find an order for this link. Please check the link or contact your store.'
          : 'Something went wrong loading your order. Please try again.',
      );
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Build the set of statuses already reached + the timestamp for each.
  const reached = useMemo(() => {
    const map: Record<string, string | null> = {};
    for (const entry of data?.status_history ?? []) {
      if (entry.status) map[entry.status] = entry.timestamp;
    }
    return map;
  }, [data]);

  const isCancelled = data?.status === 'CANCELLED';
  const currentIndex = useMemo(() => {
    if (!data?.status) return -1;
    return FLOW.findIndex((s) => s.key === data.status);
  }, [data]);

  if (!token) {
    return <Shell><ErrorCard msg="This tracking link is missing its code." /></Shell>;
  }

  if (loading && !data) {
    return (
      <Shell>
        <div className="bg-white rounded-xl border border-gray-200 p-12 flex items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      </Shell>
    );
  }

  if (error || !data) {
    return <Shell><ErrorCard msg={error || 'Order not found.'} onRetry={reload} /></Shell>;
  }

  return (
    <Shell>
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {/* Summary */}
        <div className="p-6 border-b border-gray-100">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-mono uppercase tracking-wide text-gray-500">
                Order {data.order_number}
              </p>
              <h2 className="text-2xl font-semibold text-gray-900 mt-1">
                {data.customer_first_name ? `Hi ${data.customer_first_name}, ` : ''}
                here&apos;s your order status
              </h2>
            </div>
            <button
              type="button"
              onClick={reload}
              disabled={loading}
              className="shrink-0 flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              Refresh
            </button>
          </div>

          <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
            <div>
              <p className="text-gray-500">Status</p>
              <p className={`font-semibold ${isCancelled ? 'text-red-600' : 'text-gray-900'}`}>
                {isCancelled ? 'Cancelled' : (FLOW[currentIndex]?.label ?? data.status)}
              </p>
            </div>
            {data.expected_delivery && !isCancelled && (
              <div>
                <p className="text-gray-500">Expected by</p>
                <p className="font-semibold text-gray-900">{formatDate(data.expected_delivery)}</p>
              </div>
            )}
            <div>
              <p className="text-gray-500">Items</p>
              <p className="font-semibold text-gray-900">{data.item_count}</p>
            </div>
          </div>
        </div>

        {/* Timeline */}
        <div className="p-6 border-b border-gray-100">
          {isCancelled ? (
            <div className="flex items-center gap-3 text-red-600">
              <AlertTriangle className="w-5 h-5" />
              <p className="text-sm font-medium">
                This order was cancelled. Please contact your store for details.
              </p>
            </div>
          ) : (
            <ol className="space-y-0">
              {FLOW.map((step, i) => {
                const done = step.key in reached || (currentIndex >= 0 && i <= currentIndex);
                const isCurrent = i === currentIndex;
                const ts = reached[step.key];
                const isLast = i === FLOW.length - 1;
                return (
                  <li key={step.key} className="flex gap-3">
                    <div className="flex flex-col items-center">
                      {done ? (
                        <CheckCircle2 className={`w-6 h-6 ${isCurrent ? 'text-emerald-600' : 'text-emerald-500'}`} />
                      ) : (
                        <Circle className="w-6 h-6 text-gray-300" />
                      )}
                      {!isLast && (
                        <span className={`w-0.5 flex-1 min-h-[28px] ${done ? 'bg-emerald-300' : 'bg-gray-200'}`} />
                      )}
                    </div>
                    <div className={`pb-6 ${isLast ? 'pb-0' : ''}`}>
                      <p className={`text-sm font-medium ${done ? 'text-gray-900' : 'text-gray-400'}`}>
                        {step.label}
                        {isCurrent && (
                          <span className="ml-2 inline-flex items-center gap-1 text-xs font-semibold text-emerald-700">
                            <Clock className="w-3 h-3" /> current
                          </span>
                        )}
                      </p>
                      {ts && <p className="text-xs text-gray-500 mt-0.5">{formatDateTime(ts)}</p>}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        {/* Items */}
        {data.items.length > 0 && (
          <div className="p-6 border-b border-gray-100">
            <p className="text-sm font-medium text-gray-900 mb-3 flex items-center gap-2">
              <Glasses className="w-4 h-4 text-gray-500" /> Your items
            </p>
            <ul className="divide-y divide-gray-100">
              {data.items.map((it, idx) => (
                <li key={idx} className="flex items-center justify-between py-2 text-sm">
                  <span className="text-gray-800">{it.description}</span>
                  <span className="text-gray-500">Qty {it.quantity}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Store contact */}
        {(data.store_name || data.store_phone) && (
          <div className="p-6 bg-gray-50">
            <p className="text-sm font-medium text-gray-900">Need help with this order?</p>
            <div className="mt-1 text-sm text-gray-600">
              {data.store_name && <p>{data.store_name}</p>}
              {data.store_phone && (
                <a
                  href={`tel:${data.store_phone}`}
                  className="inline-flex items-center gap-1.5 mt-1 text-bv-red-600 hover:text-bv-red-700 font-medium"
                >
                  <Phone className="w-4 h-4" /> {data.store_phone}
                </a>
              )}
            </div>
          </div>
        )}
      </div>
    </Shell>
  );
}

// ----------------------------------------------------------------------------
// Layout primitives (public — no AppLayout chrome)
// ----------------------------------------------------------------------------

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-2xl mx-auto px-4 py-4 flex items-center gap-2">
          <PackageSearch className="w-5 h-5 text-bv-red-600" />
          <div>
            <p className="text-xs font-mono uppercase tracking-wide text-gray-500">
              Order Tracking · Better Vision
            </p>
          </div>
        </div>
      </header>
      <main className="max-w-2xl mx-auto px-4 py-6">{children}</main>
    </div>
  );
}

function ErrorCard({ msg, onRetry }: { msg: string; onRetry?: () => void }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-10 text-center">
      <AlertTriangle className="w-10 h-10 mx-auto mb-3 text-amber-500" />
      <p className="text-sm text-gray-700">{msg}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 btn-outline text-sm inline-flex items-center gap-2"
        >
          <RefreshCw className="w-4 h-4" /> Try again
        </button>
      )}
    </div>
  );
}
