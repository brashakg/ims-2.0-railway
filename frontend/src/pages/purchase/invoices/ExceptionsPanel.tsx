// ============================================================================
// Purchase Invoices - P3 variance-approval panel + quick-approve modal.
// MOVED verbatim out of ../PurchaseInvoicesTab.tsx by the Wave 6 file diet.
// ============================================================================

import { useState } from 'react';
import { X, Loader2, AlertTriangle, ShieldCheck, ShieldAlert } from 'lucide-react';
import {
  purchaseInvoicesApi,
  type PurchaseInvoice,
  type MatchStatus,
  type ExceptionOverride,
} from '../../../services/api/vendorAp';
import { useToast } from '../../../context/ToastContext';
import { useAuth } from '../../../context/AuthContext';
import { inr, APPROVE_ROLES } from './shared';

// ============================================================================
// P3: Variance-Approval panel
// ============================================================================
// A dedicated "Exceptions" section at the top of the Purchase Invoices tab.
// Filters the already-loaded invoice list to ON_HOLD_EXCEPTION entries and
// presents each with its per-line variance reasons + a quick-approve action.
// Gated to ACCOUNTANT / ADMIN / SUPERADMIN (mirrors the backend _AP_ROLES gate).
// When no exceptions exist, renders nothing so the normal list is uncluttered.
// ============================================================================

// Quick-approve modal: reason textarea (required, >=10 chars), confirm -> POST.
// The Recon Console's Review action opens this same modal.
export function ApproveModal({
  invoice,
  onClose,
  onApproved,
}: {
  invoice: PurchaseInvoice;
  onClose: () => void;
  onApproved: (updated: { match_status: MatchStatus; exception_override?: ExceptionOverride }) => void;
}) {
  const toast = useToast();
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);

  const tooShort = reason.trim().length < 10;

  const confirm = async () => {
    if (tooShort) { toast.error('A reason of at least 10 characters is required'); return; }
    setSaving(true);
    try {
      const res = await purchaseInvoicesApi.approveException(
        invoice.purchase_invoice_id,
        { reason: reason.trim() },
      );
      onApproved({
        match_status: res.match_status ?? 'MATCHED_OVERRIDE',
        exception_override: res.exception_override,
      });
      toast.success('Exception approved — invoice released for payment');
    } catch (e) {
      const msg =
        e && typeof e === 'object' && 'response' in e
          ? ((e as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '')
          : e instanceof Error ? e.message : '';
      toast.error(msg || 'Failed to approve the exception');
    } finally {
      setSaving(false);
    }
  };

  const exceptions: string[] = Array.from(
    new Set((invoice.match_detail?.exceptions ?? []) as string[]),
  );

  return (
    <div
      className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white w-full max-w-lg rounded-lg shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-amber-600" />
            Approve match exception
          </h3>
          <button
            type="button"
            onClick={onClose}
            title="Close"
            aria-label="Close"
            className="text-gray-400 hover:text-gray-700"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Invoice identity */}
          <div className="text-sm text-gray-700">
            <div className="font-medium text-gray-900">
              {invoice.vendor_name || invoice.vendor_id}
              <span className="text-gray-500 font-normal ml-2">
                · {invoice.vendor_invoice_no}
              </span>
            </div>
            <div className="text-xs text-gray-500 mt-0.5">
              {(invoice.vendor_invoice_date || '').slice(0, 10)}
              {invoice.po_number && <span className="ml-2">PO {invoice.po_number}</span>}
              {invoice.grn_number && <span className="ml-2">GRN {invoice.grn_number}</span>}
              <span className="ml-2 font-medium text-gray-700">{inr(invoice.total_amount)}</span>
            </div>
          </div>

          {/* Exception reasons */}
          {exceptions.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-xs font-semibold text-amber-800 mb-1.5">Why this invoice is on hold</p>
              <ul className="space-y-1">
                {exceptions.map((r, i) => (
                  <li key={i} className="text-xs text-amber-700 flex items-start gap-1.5">
                    <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Reason textarea */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Reason for approval <span className="text-red-500">*</span>
              <span className="font-normal text-gray-400 ml-1">(required, written to the immutable audit log)</span>
            </label>
            <textarea
              className="border border-gray-300 rounded px-3 py-2 text-sm w-full h-24 resize-none focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why release this invoice for payment despite the variance? (min 10 chars)"
            />
            <p className={`text-[11px] mt-1 ${tooShort && reason.length > 0 ? 'text-amber-600' : 'text-gray-400'}`}>
              {reason.trim().length}/10 characters minimum
            </p>
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="btn sm"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={confirm}
              disabled={saving || tooShort}
              className="btn sm primary disabled:opacity-60"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              <ShieldCheck className="w-4 h-4" />
              Approve exception
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// The main exceptions panel — rendered at the top of PurchaseInvoicesTab when
// there are any ON_HOLD_EXCEPTION invoices the current user can act on.
export function ExceptionsPanel({
  invoices,
  onApproved,
  onViewDetail,
}: {
  invoices: PurchaseInvoice[];
  onApproved: (invoiceId: string, updated: { match_status: MatchStatus; exception_override?: ExceptionOverride }) => void;
  onViewDetail: (invoice: PurchaseInvoice) => void;
}) {
  const { hasRole } = useAuth();
  const [approveTarget, setApproveTarget] = useState<PurchaseInvoice | null>(null);

  const canApprove = hasRole(APPROVE_ROLES);
  const exceptions = invoices.filter((pi) => pi.match_status === 'ON_HOLD_EXCEPTION');

  if (exceptions.length === 0) return null;

  return (
    <>
      <div className="rounded-lg border border-amber-300 bg-amber-50 overflow-hidden">
        {/* Panel header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-amber-200 bg-amber-100/60">
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-amber-700" />
            <span className="text-sm font-semibold text-amber-900">
              {exceptions.length} invoice{exceptions.length !== 1 ? 's' : ''} on hold — variance exception
            </span>
          </div>
          <p className="text-xs text-amber-700">
            {canApprove
              ? 'Review each variance and approve or open the full detail drawer.'
              : 'These invoices are blocked for payment until an Admin or Accountant approves the variance.'}
          </p>
        </div>

        {/* Per-invoice rows */}
        <div className="divide-y divide-amber-200">
          {exceptions.map((pi) => {
            const reasons: string[] = Array.from(
              new Set((pi.match_detail?.exceptions ?? []) as string[]),
            );
            return (
              <div
                key={pi.purchase_invoice_id}
                className="px-4 py-3 flex flex-wrap items-start gap-3"
              >
                {/* Left: invoice identity + reasons */}
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                    <span className="text-sm font-medium text-gray-900">
                      {pi.vendor_name || pi.vendor_id}
                    </span>
                    <span className="text-xs text-gray-500">{pi.vendor_invoice_no}</span>
                    {pi.po_number && (
                      <span className="text-xs text-gray-400">PO {pi.po_number}</span>
                    )}
                    <span className="text-xs font-semibold text-gray-700">
                      {inr(pi.total_amount)}
                    </span>
                    <span className="text-xs text-gray-400">
                      {(pi.vendor_invoice_date || '').slice(0, 10)}
                    </span>
                  </div>

                  {reasons.length > 0 && (
                    <ul className="mt-1.5 space-y-0.5">
                      {reasons.map((r, i) => (
                        <li key={i} className="text-xs text-amber-800 flex items-start gap-1.5">
                          <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0 text-amber-600" />
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* Right: actions */}
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button
                    type="button"
                    onClick={() => onViewDetail(pi)}
                    className="text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-white border border-gray-200 rounded-lg px-3 py-1.5"
                  >
                    View detail
                  </button>
                  {canApprove && (
                    <button
                      type="button"
                      onClick={() => setApproveTarget(pi)}
                      className="btn sm primary text-xs"
                    >
                      <ShieldCheck className="w-3.5 h-3.5" />
                      Approve
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Quick-approve modal */}
      {approveTarget && (
        <ApproveModal
          invoice={approveTarget}
          onClose={() => setApproveTarget(null)}
          onApproved={(updated) => {
            onApproved(approveTarget.purchase_invoice_id, updated);
            setApproveTarget(null);
          }}
        />
      )}
    </>
  );
}
