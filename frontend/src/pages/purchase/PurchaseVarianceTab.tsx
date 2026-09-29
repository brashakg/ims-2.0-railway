// ============================================================================
// IMS 2.0 - F8: PO vs GRN variance / backorder tab
// ============================================================================
// Read-mostly accountability surface. Every open/partial PO line whose ACCEPTED
// received qty trails the ordered qty is surfaced with its open qty, days
// overdue, and an explicit aging status (ON_TIME / OVERDUE / CRITICALLY_OVERDUE
// -- a status enum, NOT a colour flag; colour is only the semantic accent for
// the label). An ADMIN / ACCOUNTANT can dismiss a line with a mandatory
// justification; when the dismissal involves an over-billed invoice the
// response prompts (only prompts) a debit note.

import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, Loader2, RefreshCw, PackageX, X, Receipt,
} from 'lucide-react';
import { vendorsApi, type VarianceLine } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';

function errMsg(e: unknown, fb: string): string {
  if (e && typeof e === 'object' && 'response' in e) {
    const r = (e as { response?: { data?: { detail?: string } } }).response;
    if (r?.data?.detail) return String(r.data.detail);
  }
  return e instanceof Error ? e.message : fb;
}

const inr = (n?: number | null) =>
  n == null ? '-' : `₹${(Math.round(n * 100) / 100).toLocaleString('en-IN')}`;

// Aging label + restrained semantic accent (text + subtle border, no fill).
const AGING: Record<VarianceLine['aging_status'], { label: string; cls: string }> = {
  ON_TIME: { label: 'On time', cls: 'text-gray-600 border-gray-200' },
  OVERDUE: { label: 'Overdue', cls: 'text-amber-700 border-amber-200 bg-amber-50' },
  CRITICALLY_OVERDUE: { label: 'Critically overdue', cls: 'text-red-700 border-red-200 bg-red-50' },
};

const VARIANCE: Record<VarianceLine['variance_status'], string> = {
  SHORT: 'text-amber-700',
  OVER: 'text-purple-700',
  EXACT: 'text-gray-500',
  UNMATCHED: 'text-gray-400',
};

export function PurchaseVarianceTab() {
  const toast = useToast();
  const { user, hasRole } = useAuth();
  const [rows, setRows] = useState<VarianceLine[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dismissRow, setDismissRow] = useState<VarianceLine | null>(null);

  const canDismiss = hasRole(['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const storeId = user?.activeStoreId;
      const res = await vendorsApi.getVarianceReport(storeId ? { store_id: storeId } : {});
      setRows(res?.lines ?? []);
    } catch (e) {
      setError(errMsg(e, 'Failed to load the variance report'));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [user?.activeStoreId]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-gray-500">
          Open purchase orders whose received quantity trails what was ordered. Aged lines are
          surfaced so a short shipment becomes an accountable follow-up, never a silent loss.
        </p>
        <button
          type="button"
          onClick={load}
          className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5"
        >
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2">
          <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-900">Failed to load variance report</p>
            <p className="text-xs text-red-700 mt-1">{error}</p>
          </div>
          <button type="button" onClick={load} className="text-xs font-medium text-red-700 hover:text-red-900 underline">Retry</button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
        </div>
      ) : rows.length === 0 ? (
        <div className="text-center py-12 bg-white border border-gray-200 rounded-lg">
          <PackageX className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-700 font-medium">No open variances</p>
          <p className="text-sm text-gray-500 mt-1">
            Every open purchase order has been received in full, or there are none outstanding.
          </p>
        </div>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs">
              <tr>
                <th className="text-left px-3 py-2">PO / Vendor</th>
                <th className="text-left px-3 py-2">Product</th>
                <th className="text-right px-3 py-2">Ordered</th>
                <th className="text-right px-3 py-2">Received</th>
                <th className="text-right px-3 py-2">Open</th>
                <th className="text-right px-3 py-2">Rejected</th>
                <th className="text-center px-3 py-2">Variance</th>
                <th className="text-right px-3 py-2">Days overdue</th>
                <th className="text-center px-3 py-2">Aging</th>
                {canDismiss && <th className="px-2 py-2"></th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r) => {
                const aging = AGING[r.aging_status] ?? AGING.ON_TIME;
                return (
                  <tr key={`${r.po_id}-${r.product_id}`} className="hover:bg-gray-50">
                    <td className="px-3 py-2">
                      <div className="font-medium text-gray-900">{r.po_number || r.po_id}</div>
                      <div className="text-xs text-gray-500">{r.vendor_name || r.vendor_id}</div>
                    </td>
                    <td className="px-3 py-2 text-gray-700">{r.product_name || r.product_id}</td>
                    <td className="px-3 py-2 text-right text-gray-700">{r.ordered_qty}</td>
                    <td className="px-3 py-2 text-right text-gray-700">{r.accepted_qty}</td>
                    <td className="px-3 py-2 text-right font-semibold text-gray-900">{r.open_qty}</td>
                    <td className="px-3 py-2 text-right text-gray-500">{r.rejected_qty || '-'}</td>
                    <td className="px-3 py-2 text-center">
                      <span className={`text-xs font-medium ${VARIANCE[r.variance_status] ?? 'text-gray-500'}`}>
                        {r.variance_status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-gray-700">{r.days_overdue || '-'}</td>
                    <td className="px-3 py-2 text-center">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${aging.cls}`}>
                        {aging.label}
                      </span>
                    </td>
                    {canDismiss && (
                      <td className="px-2 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => setDismissRow(r)}
                          className="text-xs font-medium text-gray-500 hover:text-gray-900 hover:bg-gray-100 rounded px-2 py-1"
                        >
                          Dismiss
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {dismissRow && (
        <DismissModal
          row={dismissRow}
          onClose={() => setDismissRow(null)}
          onDismissed={() => { setDismissRow(null); load(); toast.success('Variance dismissed'); }}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Dismiss modal: mandatory reason (>= 10 chars), confirm -> POST. On success,
// if the response prompts a debit note, surface an info strip linking to the
// supplier debit-note flow (prompt only -- the operator can ignore it).
// ----------------------------------------------------------------------------
function DismissModal({
  row, onClose, onDismissed,
}: {
  row: VarianceLine;
  onClose: () => void;
  onDismissed: () => void;
}) {
  const toast = useToast();
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [suggestion, setSuggestion] = useState<{ amount?: number | null } | null>(null);

  const tooShort = reason.trim().length < 10;

  const confirm = async () => {
    if (tooShort) { toast.error('A reason of at least 10 characters is required'); return; }
    setSaving(true);
    try {
      const res = await vendorsApi.dismissVariance(row.po_id, {
        product_id: row.product_id,
        reason: reason.trim(),
        // Carry the server-resolved GRN + booked-invoice links so the backend
        // can compare accepted vs billed qty and suggest a debit note when the
        // invoice over-bills (without both ids the prompt never fires).
        grn_id: row.latest_accepted_grn_id ?? undefined,
        bill_id: row.booked_bill_id ?? undefined,
      });
      if (res.debit_note_suggested) {
        // Keep the modal open to show the debit-note prompt; the dismissal has
        // already been recorded, so the parent list will refresh on close.
        setSuggestion({ amount: res.suggested_amount });
      } else {
        onDismissed();
      }
    } catch (e) {
      toast.error(errMsg(e, 'Failed to dismiss the variance'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white w-full max-w-md rounded-lg shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <PackageX className="w-5 h-5" /> Dismiss variance
          </h3>
          <button type="button" onClick={onClose} title="Close" aria-label="Close" className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-5 space-y-4">
          <div className="text-sm text-gray-600">
            <div className="font-medium text-gray-900">{row.product_name || row.product_id}</div>
            <div className="text-xs text-gray-500 mt-0.5">
              PO {row.po_number || row.po_id} · {row.open_qty} unit(s) open · {row.vendor_name || row.vendor_id}
            </div>
          </div>

          {!suggestion ? (
            <>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Reason (required, audited)
                </label>
                <textarea
                  className="border border-gray-300 rounded px-2 py-1.5 text-sm w-full h-24 resize-none"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Why is this open line no longer being chased? (e.g. vendor short-closed the order)"
                />
                <p className={`text-[11px] mt-1 ${tooShort ? 'text-amber-600' : 'text-gray-400'}`}>
                  At least 10 characters. This is written to the immutable audit log.
                </p>
              </div>
              <div className="flex justify-end gap-2">
                <button type="button" onClick={onClose} className="btn sm">Cancel</button>
                <button
                  type="button"
                  onClick={confirm}
                  disabled={saving || tooShort}
                  className="btn sm primary disabled:opacity-60"
                >
                  {saving && <Loader2 className="w-4 h-4 animate-spin" />} Confirm dismiss
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 flex items-start gap-2">
                <Receipt className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
                <div className="flex-1 text-sm text-blue-900">
                  <p className="font-medium">A debit note may be appropriate</p>
                  <p className="text-xs text-blue-800 mt-1">
                    The supplier&apos;s invoice billed more than was accepted for this product.
                    A debit note of {inr(suggestion.amount)} could be raised against the vendor.
                    Open the <span className="font-medium">Suppliers</span> tab to create one.
                  </p>
                </div>
              </div>
              <div className="flex justify-end">
                <button type="button" onClick={onDismissed} className="btn sm primary">Done</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
