// ============================================================================
// IMS 2.0 - Accountant Reconciliation Console (Purchase S6 / Phase 3 rework)
// ============================================================================
// Council-ruled rework: ONE filterable queue of purchase invoices replaces the
// fragmented surfaces. Each row carries:
//   - the 4 per-invoice attestation ticks (Reconciled / Tally / GST / Paid)
//     via POST /vendors/purchase-invoices/{id}/recon  (per-document writes)
//   - an optional short attestation note (same endpoint accepts `note`)
//   - "NO PO LINKED" red badge when the invoice has no PO, no GRN and no DCs
//   - "AUTO-MATCHED" green chip when the 3-way match passed (match_status
//     MATCHED) -> these rows are batch-confirmable: "Confirm N selected" loops
//     the EXISTING per-invoice recon endpoint sequentially (reconciled tick
//     ONLY -- Tally / GST / Paid stay strictly per-invoice).
//   - "ON HOLD" amber chip + Review action for ON_HOLD_EXCEPTION invoices
//     (existing POST /{id}/approve-exception, reason >= 10 chars).
//
// Filter chips: All / Needs review / On hold (exceptions) / Unlinked /
// Auto-matched / Settled. Existing search + status filters preserved.
// Sort: needs-attention first (on hold, then unlinked, then in-progress),
// then newest.
//
// The four legacy worklists (stock yet to receive / vendor returns / scheme
// CNs / return CNs) hold NON-invoice documents, so they cannot be rows of the
// invoice queue -- they remain reachable below as the secondary "Documents
// pending" panel with every action intact (incl. mark-scheme-CN-received).
//
// Route: /purchase/recon-console      Roles: ACCOUNTANT / ADMIN / SUPERADMIN
// API: purchaseRecon.ts + purchaseInvoicesApi from vendorAp.ts -- imported
// DIRECTLY (not via the barrel) to avoid TS2614.

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  CheckSquare, Square, Loader2, RefreshCw, ChevronDown, ChevronRight,
  Package, RotateCcw, FileText, AlertTriangle, ClipboardCheck, StickyNote,
  ShieldCheck, X, ListChecks,
} from 'lucide-react';
import { purchaseReconApi, type ReconBlock, type ReconWorklists } from '../../services/api/purchaseRecon';
import { purchaseInvoicesApi, type PurchaseInvoice, type MatchStatus, type ExceptionOverride } from '../../services/api/vendorAp';
import { PurchaseStatusChip } from '../../components/purchase/PurchaseStatusChip';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import type { UserRole } from '../../types';

// ---- helpers ---------------------------------------------------------------

const inr = (n?: number) =>
  n == null ? '-' : `₹${Math.round(Number(n) || 0).toLocaleString('en-IN')}`;

function fmtDate(iso?: string) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function fmtDateTime(iso?: string) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 16);
  return d.toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

type ReconFlag = 'reconciled' | 'entered_tally' | 'filed_gst' | 'payment_settled';

const FLAG_LABELS: Record<ReconFlag, string> = {
  reconciled: 'Reconciled',
  entered_tally: 'Tally',
  filed_gst: 'GST Filed',
  payment_settled: 'Paid',
};

const FLAG_TITLES: Record<ReconFlag, string> = {
  reconciled: 'Physically reconciled with vendor statement',
  entered_tally: 'Entered into Tally',
  filed_gst: 'Included in GST return (GSTR-2B / GSTR-3B)',
  payment_settled: 'Payment scheduled or made',
};

const FLAGS: ReconFlag[] = ['reconciled', 'entered_tally', 'filed_gst', 'payment_settled'];

const DEFAULT_RECON: ReconBlock = {
  reconciled: false,
  entered_tally: false,
  filed_gst: false,
  payment_settled: false,
};

export function invoiceRowId(inv: PurchaseInvoice): string {
  return inv.purchase_invoice_id ?? inv.bill_id ?? '';
}

// ---- Queue classification (exported for tests) ------------------------------

export type QueueChipId =
  | 'all'
  | 'needs_review'
  | 'on_hold'
  | 'unlinked'
  | 'auto_matched'
  | 'settled';

export const QUEUE_CHIPS: { id: QueueChipId; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'needs_review', label: 'Needs review' },
  { id: 'on_hold', label: 'On hold (exceptions)' },
  { id: 'unlinked', label: 'Unlinked' },
  { id: 'auto_matched', label: 'Auto-matched' },
  { id: 'settled', label: 'Settled' },
];

/** An invoice with no PO, no GRN and no linked Delivery Challans -- nothing
 *  upstream to match it against. The council flagged that these looked
 *  identical to normal rows; they now carry the red NO PO LINKED badge. */
export function isUnlinked(inv: PurchaseInvoice): boolean {
  const dcs = inv.linked_dc_ids ?? [];
  return !inv.po_id && !inv.grn_id && dcs.length === 0;
}

/** All 4 accountant attestations ticked. */
export function isSettled(recon: ReconBlock): boolean {
  return FLAGS.every((f) => !!recon[f]);
}

/** Does this invoice belong under the given filter chip? Chips are filters,
 *  not partitions -- a row may match several (e.g. unlinked AND needs review). */
export function chipMatches(chip: QueueChipId, inv: PurchaseInvoice, recon: ReconBlock): boolean {
  switch (chip) {
    case 'all': return true;
    case 'needs_review': return !isSettled(recon);
    case 'on_hold': return inv.match_status === 'ON_HOLD_EXCEPTION';
    case 'unlinked': return isUnlinked(inv);
    case 'auto_matched': return inv.match_status === 'MATCHED';
    case 'settled': return isSettled(recon);
    default: return true;
  }
}

/** Sort key: needs-attention first. 0 = on hold, 1 = unlinked, 2 = other
 *  unsettled, 3 = fully settled. Ties broken by newest date (in the caller). */
export function attentionRank(inv: PurchaseInvoice, recon: ReconBlock): number {
  if (isSettled(recon)) return 3;
  if (inv.match_status === 'ON_HOLD_EXCEPTION') return 0;
  if (isUnlinked(inv)) return 1;
  return 2;
}

/** Auto-matched rows that still need the Reconciled attestation are the ONLY
 *  batch-confirmable rows (3-way match already passed mechanically; the human
 *  attestation is still written per-document on the server). */
export function isBatchEligible(inv: PurchaseInvoice, recon: ReconBlock): boolean {
  return inv.match_status === 'MATCHED' && !recon.reconciled;
}

// ---- Recon Checkbox ----------------------------------------------------------

interface ReconCheckboxProps {
  flag: ReconFlag;
  recon: ReconBlock;
  saving: boolean;
  onToggle: (flag: ReconFlag, value: boolean) => void;
}

function ReconCheckbox({ flag, recon, saving, onToggle }: ReconCheckboxProps) {
  const checked = !!recon[flag];
  const by = recon[`${flag}_by` as keyof ReconBlock] as string | undefined;
  const at = recon[`${flag}_at` as keyof ReconBlock] as string | undefined;

  const tooltip = checked && (by || at)
    ? `${by ? `By: ${by}` : ''}${by && at ? '\n' : ''}${at ? `At: ${fmtDateTime(at)}` : ''}`
    : FLAG_TITLES[flag];

  return (
    <div className="flex flex-col items-center gap-0.5">
      <button
        type="button"
        onClick={() => !saving && onToggle(flag, !checked)}
        disabled={saving}
        title={tooltip}
        aria-label={`${FLAG_LABELS[flag]}: ${checked ? 'ticked' : 'unticked'}`}
        className={[
          'flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors',
          saving ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:opacity-80',
          checked
            ? 'bg-green-100 text-green-800 border border-green-300'
            : 'bg-gray-50 text-gray-500 border border-gray-200 hover:border-gray-400',
        ].join(' ')}
      >
        {checked
          ? <CheckSquare size={13} className="text-green-600 shrink-0" />
          : <Square size={13} className="text-gray-400 shrink-0" />}
        <span>{FLAG_LABELS[flag]}</span>
      </button>
      {checked && at && (
        <span className="text-[10px] text-gray-400 text-center leading-tight">
          {fmtDate(at)}
        </span>
      )}
    </div>
  );
}

// ---- Attestation note popover ------------------------------------------------
// The recon endpoint accepts an optional free-text `note` (single note per
// invoice, stamped note_by / note_at server-side). Saved via the SAME
// per-invoice POST as the ticks -- no new endpoint.

function NoteControl({
  note,
  saving,
  onSave,
}: {
  note?: string;
  saving: boolean;
  onSave: (text: string) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(note ?? '');

  useEffect(() => { setText(note ?? ''); }, [note]);

  return (
    <div className="relative shrink-0 self-center">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={note ? `Note: ${note}` : 'Add attestation note (optional)'}
        aria-label={note ? 'Edit attestation note' : 'Add attestation note'}
        className={[
          'p-1.5 rounded border transition-colors',
          note
            ? 'bg-amber-50 text-amber-600 border-amber-200 hover:border-amber-400'
            : 'bg-gray-50 text-gray-400 border-gray-200 hover:border-gray-400',
        ].join(' ')}
      >
        <StickyNote size={13} />
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-20 w-64 bg-white border border-gray-200 rounded-lg shadow-lg p-2.5">
          <label className="block text-[11px] font-medium text-gray-500 mb-1">
            Attestation note (optional)
          </label>
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder='e.g. "Paid via NEFT ref 5401"'
            className="input-field text-xs py-1.5 px-2 w-full"
            autoFocus
          />
          <div className="flex justify-end gap-1.5 mt-2">
            <button
              type="button"
              onClick={() => { setOpen(false); setText(note ?? ''); }}
              className="text-xs px-2 py-1 rounded text-gray-500 hover:bg-gray-100"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => { void onSave(text.trim()); setOpen(false); }}
              className="text-xs font-medium px-2.5 py-1 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Save note
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Approve-exception modal ---------------------------------------------------
// Same server contract as the Purchase Invoices tab: POST /{id}/approve-exception
// with a mandatory reason (>= 10 chars, written to the immutable audit log).

const EXCEPTION_APPROVE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];

function ApproveExceptionModal({
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
  const reasons: string[] = Array.from(
    new Set((invoice.match_detail?.exceptions ?? []) as string[]),
  );

  const confirm = async () => {
    if (tooShort) { toast.error('A reason of at least 10 characters is required'); return; }
    setSaving(true);
    try {
      const res = await purchaseInvoicesApi.approveException(
        invoiceRowId(invoice),
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

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white w-full max-w-lg rounded-lg shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-amber-600" />
            Approve match exception
          </h3>
          <button type="button" onClick={onClose} title="Close" aria-label="Close" className="text-gray-400 hover:text-gray-700">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div className="text-sm text-gray-700">
            <div className="font-medium text-gray-900">
              {invoice.vendor_name || invoice.vendor_id}
              <span className="text-gray-500 font-normal ml-2">· {invoice.vendor_invoice_no}</span>
            </div>
            <div className="text-xs text-gray-500 mt-0.5">
              {(invoice.vendor_invoice_date || '').slice(0, 10)}
              {invoice.po_number && <span className="ml-2">PO {invoice.po_number}</span>}
              {invoice.grn_number && <span className="ml-2">GRN {invoice.grn_number}</span>}
              <span className="ml-2 font-medium text-gray-700">{inr(invoice.total_amount)}</span>
            </div>
          </div>

          {reasons.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-xs font-semibold text-amber-800 mb-1.5">Why this invoice is on hold</p>
              <ul className="space-y-1">
                {reasons.map((r, i) => (
                  <li key={i} className="text-xs text-amber-700 flex items-start gap-1.5">
                    <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

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

          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className="btn sm">Cancel</button>
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

// ---- Queue row ------------------------------------------------------------------

interface QueueRowProps {
  invoice: PurchaseInvoice;
  recon: ReconBlock;
  saving: boolean;
  expanded: boolean;
  selectable: boolean;
  selected: boolean;
  batchError?: string;
  canApprove: boolean;
  onToggleFlag: (flag: ReconFlag, value: boolean) => void;
  onToggleSelect: () => void;
  onExpand: () => void;
  onSaveNote: (text: string) => void | Promise<void>;
  onReview: () => void;
}

function QueueRow({
  invoice, recon, saving, expanded, selectable, selected, batchError,
  canApprove, onToggleFlag, onToggleSelect, onExpand, onSaveNote, onReview,
}: QueueRowProps) {
  const unlinked = isUnlinked(invoice);
  const settled = isSettled(recon);
  const dcCount = (invoice.linked_dc_ids ?? []).length;
  const exceptionReasons: string[] = Array.from(
    new Set((invoice.match_detail?.exceptions ?? []) as string[]),
  );

  return (
    <Fragment>
      <tr className={[
        'border-b border-gray-50 hover:bg-gray-50 align-top',
        settled ? 'bg-green-50/30' : '',
      ].join(' ')}>
        {/* Batch-select checkbox: auto-matched rows awaiting Reconciled only */}
        <td className="py-2.5 pl-3 pr-1 w-8">
          {selectable ? (
            <input
              type="checkbox"
              checked={selected}
              onChange={onToggleSelect}
              disabled={saving}
              aria-label={`Select ${invoice.vendor_invoice_no || invoiceRowId(invoice)} for batch confirm`}
              className="mt-1 accent-blue-600"
            />
          ) : recon.reconciled && invoice.match_status === 'MATCHED' ? (
            <CheckSquare size={14} className="mt-1 text-green-500" aria-hidden />
          ) : null}
        </td>

        {/* Vendor / invoice identity + badges */}
        <td className="py-2.5 pr-3">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-semibold text-gray-900 text-sm">
              {invoice.vendor_invoice_no || invoice.vendor_name || '-'}
            </span>
            {invoice.vendor_name && invoice.vendor_invoice_no && (
              <span className="text-gray-500 text-xs">{invoice.vendor_name}</span>
            )}
            {unlinked && (
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-700 border border-red-300"
                title="No purchase order, GRN or delivery challan is linked to this invoice"
              >
                NO PO LINKED
              </span>
            )}
            {invoice.match_status === 'MATCHED' && (
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-green-100 text-green-700 border border-green-300"
                title="3-way match passed automatically (PO vs GRN vs invoice within tolerance)"
              >
                AUTO-MATCHED
              </span>
            )}
            {invoice.match_status === 'ON_HOLD_EXCEPTION' && (
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-300"
                title="3-way match variance outside tolerance — blocked for payment until reviewed"
              >
                ON HOLD
              </span>
            )}
            {invoice.match_status === 'MATCHED_OVERRIDE' && (
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-600 border border-gray-200"
                title={`Variance exception approved${invoice.exception_override?.approved_by ? ` by ${invoice.exception_override.approved_by}` : ''}`}
              >
                OVERRIDE APPROVED
              </span>
            )}
            {batchError && (
              <span
                className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-50 text-red-600 border border-red-200"
                title={batchError}
              >
                CONFIRM FAILED
              </span>
            )}
          </div>
          {invoice.match_status === 'ON_HOLD_EXCEPTION' && canApprove && (
            <button
              type="button"
              onClick={onReview}
              className="mt-1 text-xs font-medium text-amber-700 hover:text-amber-900 underline"
            >
              Review variance / approve
            </button>
          )}
        </td>

        {/* Date */}
        <td className="py-2.5 pr-3 text-xs text-gray-500 whitespace-nowrap">
          {fmtDate(invoice.vendor_invoice_date)}
        </td>

        {/* Document links */}
        <td className="py-2.5 pr-3 text-xs">
          <div className="flex flex-col gap-0.5">
            {(invoice.po_number || invoice.po_id) && (
              <Link to="/purchase?tab=purchase-orders" className="text-blue-600 hover:underline">
                PO {invoice.po_number ?? invoice.po_id}
              </Link>
            )}
            {(invoice.grn_number || invoice.grn_id) && (
              <Link to="/purchase/grn" className="text-blue-600 hover:underline">
                GRN {invoice.grn_number ?? invoice.grn_id}
              </Link>
            )}
            {dcCount > 0 && (
              <Link to="/purchase?tab=purchase-invoices" className="text-blue-600 hover:underline">
                {dcCount} DC{dcCount !== 1 ? 's' : ''} linked
              </Link>
            )}
            {unlinked && <span className="text-gray-400">Manual</span>}
          </div>
        </td>

        {/* Amount */}
        <td className="py-2.5 pr-3 text-right text-sm font-medium text-gray-700 whitespace-nowrap">
          {inr(invoice.total_amount ?? (invoice as { total?: number }).total)}
        </td>

        {/* Invoice payment status (accountant terminology, NOT the owner vocab) */}
        <td className="py-2.5 pr-3">
          <span className={[
            'text-xs px-1.5 py-0.5 rounded whitespace-nowrap',
            invoice.status === 'PAID'
              ? 'bg-green-100 text-green-700'
              : invoice.status === 'OUTSTANDING'
              ? 'bg-amber-100 text-amber-700'
              : 'bg-gray-100 text-gray-600',
          ].join(' ')}>
            {invoice.status ?? 'OUTSTANDING'}
          </span>
        </td>

        {/* 4 attestation ticks + note + saving spinner */}
        <td className="py-2.5 pr-2">
          <div className="flex flex-wrap gap-1 items-start">
            {FLAGS.map((flag) => (
              <ReconCheckbox
                key={flag}
                flag={flag}
                recon={recon}
                saving={saving}
                onToggle={onToggleFlag}
              />
            ))}
            <NoteControl note={recon.note} saving={saving} onSave={onSaveNote} />
            {saving && <Loader2 size={14} className="animate-spin text-gray-400 self-center" />}
          </div>
        </td>

        {/* Expand */}
        <td className="py-2.5 pr-3 w-8">
          <button
            onClick={onExpand}
            className="text-gray-400 hover:text-gray-600"
            aria-label={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </button>
        </td>
      </tr>

      {expanded && (
        <tr className="border-b border-gray-100">
          <td colSpan={8} className="px-4 py-3 bg-gray-50 text-xs text-gray-600">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div><span className="text-gray-400">Invoice #</span><br />{invoice.vendor_invoice_no || '-'}</div>
              <div><span className="text-gray-400">Date</span><br />{fmtDate(invoice.vendor_invoice_date)}</div>
              <div><span className="text-gray-400">Taxable</span><br />{inr(invoice.taxable_amount)}</div>
              <div>
                <span className="text-gray-400">Tax</span><br />
                {invoice.is_interstate
                  ? `IGST ${inr(invoice.igst)}`
                  : `CGST ${inr(invoice.cgst)} + SGST ${inr(invoice.sgst)}`}
              </div>
            </div>
            {exceptionReasons.length > 0 && (
              <div className="mt-2 p-2 bg-amber-50 border border-amber-200 rounded">
                <span className="text-amber-700 font-medium">Match exceptions:</span>
                <ul className="mt-1 space-y-0.5">
                  {exceptionReasons.map((r, i) => (
                    <li key={i} className="flex items-start gap-1.5 text-amber-800">
                      <AlertTriangle size={11} className="mt-0.5 shrink-0 text-amber-600" />
                      <span>{r}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {recon.note && (
              <div className="mt-2 p-2 bg-amber-50 border border-amber-200 rounded">
                <span className="text-amber-700 font-medium">Note: </span>
                <span className="text-gray-700">{recon.note}</span>
                {recon.note_at && (
                  <span className="text-gray-400 ml-2">({fmtDateTime(recon.note_at)})</span>
                )}
              </div>
            )}
            {recon.last_updated_at && (
              <div className="mt-1.5 text-gray-400">
                Last updated: {fmtDateTime(recon.last_updated_at)}
                {recon.last_updated_by ? ` by ${recon.last_updated_by}` : ''}
              </div>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

// ---- Documents-pending worklists (secondary panel) ---------------------------
// These four lists hold NON-invoice documents (open POs, vendor returns, credit
// notes), so they cannot merge into the invoice queue -- kept intact below it.

type WorklistTab =
  | 'stock_yet_to_receive'
  | 'vendor_returns'
  | 'pending_credit_notes_scheme'
  | 'pending_credit_notes_return';

const WORKLIST_TABS: { id: WorklistTab; label: string; icon: typeof Package }[] = [
  { id: 'stock_yet_to_receive', label: 'Stock to Receive', icon: Package },
  { id: 'vendor_returns', label: 'Vendor Returns', icon: RotateCcw },
  { id: 'pending_credit_notes_scheme', label: 'Scheme CNs', icon: FileText },
  { id: 'pending_credit_notes_return', label: 'Return CNs', icon: ClipboardCheck },
];

interface WorklistsProps {
  lists: ReconWorklists;
  onMarkCnReceived: (creditNoteNumber: string) => void | Promise<void>;
}

function Worklists({ lists, onMarkCnReceived }: WorklistsProps) {
  const [activeTab, setActiveTab] = useState<WorklistTab>('stock_yet_to_receive');

  const counts: Record<WorklistTab, number> = {
    stock_yet_to_receive: lists.stock_yet_to_receive.length,
    vendor_returns: lists.vendor_returns.length,
    pending_credit_notes_scheme: lists.pending_credit_notes_scheme.length,
    pending_credit_notes_return: lists.pending_credit_notes_return.length,
  };

  return (
    <div className="card">
      <div className="px-4 pt-4 pb-2 border-b border-gray-100">
        <h2 className="font-semibold text-gray-800 mb-1">Documents pending</h2>
        <p className="text-xs text-gray-500 mb-3">
          Non-invoice documents that still need action — open POs, returns and credit notes.
        </p>
        <div className="flex flex-wrap gap-1">
          {WORKLIST_TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={[
                'flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors',
                activeTab === id
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200',
              ].join(' ')}
            >
              <Icon size={13} />
              {label}
              {counts[id] > 0 && (
                <span className={[
                  'ml-1 text-xs px-1.5 py-0.5 rounded-full',
                  activeTab === id ? 'bg-white/20 text-white' : 'bg-amber-200 text-amber-800',
                ].join(' ')}>
                  {counts[id]}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="p-4">
        {activeTab === 'stock_yet_to_receive' && (
          <StockYetToReceiveTable rows={lists.stock_yet_to_receive} />
        )}
        {activeTab === 'vendor_returns' && (
          <VendorReturnsTable rows={lists.vendor_returns} />
        )}
        {activeTab === 'pending_credit_notes_scheme' && (
          <SchemeCnTable
            rows={lists.pending_credit_notes_scheme}
            onMarkReceived={onMarkCnReceived}
          />
        )}
        {activeTab === 'pending_credit_notes_return' && (
          <ReturnCnTable rows={lists.pending_credit_notes_return} />
        )}
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="text-center py-10 text-gray-400">
      <CheckSquare size={28} className="mx-auto mb-2 text-green-400" />
      <p className="text-sm">{message}</p>
    </div>
  );
}

function StockYetToReceiveTable({ rows }: { rows: ReconWorklists['stock_yet_to_receive'] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  if (!rows.length) return <EmptyState message="Nothing pending — all PO lines received." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="pb-2 pr-4 font-medium">PO #</th>
            <th className="pb-2 pr-4 font-medium">Status</th>
            <th className="pb-2 pr-4 font-medium">Expected</th>
            <th className="pb-2 pr-4 font-medium text-right">Pending Qty</th>
            <th className="pb-2 font-medium">Lines</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const key = row.po_id ?? String(i);
            const isOpen = expanded.has(key);
            return (
              <Fragment key={key}>
                <tr className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="py-2 pr-4">
                    <span className="font-medium text-gray-800">{row.po_number ?? row.po_id ?? '-'}</span>
                  </td>
                  <td className="py-2 pr-4">
                    {/* Owner-vocabulary chip for PO statuses (council Phase 2 rollout) */}
                    <PurchaseStatusChip status={row.status} kind="po" />
                  </td>
                  <td className="py-2 pr-4 text-gray-500">{fmtDate(row.expected_date)}</td>
                  <td className="py-2 pr-4 text-right font-medium text-amber-700">
                    {row.total_pending_qty}
                  </td>
                  <td className="py-2">
                    <button
                      onClick={() => {
                        const next = new Set(expanded);
                        if (isOpen) next.delete(key); else next.add(key);
                        setExpanded(next);
                      }}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      {isOpen ? 'Hide' : `View ${row.open_lines.length} line${row.open_lines.length !== 1 ? 's' : ''}`}
                    </button>
                  </td>
                </tr>
                {isOpen && (
                  <tr>
                    <td colSpan={5} className="pb-2 pt-0">
                      <div className="bg-gray-50 rounded p-2 text-xs text-gray-600">
                        <table className="w-full">
                          <thead>
                            <tr className="text-gray-400">
                              <th className="text-left pb-1 font-medium">Product</th>
                              <th className="text-right pb-1 font-medium">Ordered</th>
                              <th className="text-right pb-1 font-medium">Received</th>
                              <th className="text-right pb-1 font-medium text-amber-700">Pending</th>
                            </tr>
                          </thead>
                          <tbody>
                            {row.open_lines.map((ln, j) => (
                              <tr key={j} className="border-t border-gray-100">
                                <td className="py-1">{ln.product_name ?? ln.product_id ?? '-'}</td>
                                <td className="py-1 text-right">{ln.ordered_qty}</td>
                                <td className="py-1 text-right">{ln.received_qty}</td>
                                <td className="py-1 text-right text-amber-700 font-medium">{ln.pending_qty}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function VendorReturnsTable({ rows }: { rows: ReconWorklists['vendor_returns'] }) {
  if (!rows.length) return <EmptyState message="Nothing pending — no open vendor returns." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="pb-2 pr-4 font-medium">Return #</th>
            <th className="pb-2 pr-4 font-medium">Vendor</th>
            <th className="pb-2 pr-4 font-medium">Type</th>
            <th className="pb-2 pr-4 font-medium">Status</th>
            <th className="pb-2 pr-4 font-medium text-right">Value</th>
            <th className="pb-2 font-medium">CN #</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.return_id ?? i} className="border-b border-gray-50 hover:bg-gray-50">
              <td className="py-2 pr-4 font-medium text-gray-800">{row.return_id ?? '-'}</td>
              <td className="py-2 pr-4 text-gray-600">{row.vendor_name ?? row.vendor_id ?? '-'}</td>
              <td className="py-2 pr-4">
                <span className="text-xs px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">
                  {row.return_type ?? '-'}
                </span>
              </td>
              <td className="py-2 pr-4">
                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                  {row.status ?? '-'}
                </span>
              </td>
              <td className="py-2 pr-4 text-right">{inr(row.total_value)}</td>
              <td className="py-2 text-gray-500 text-xs">{row.credit_note_number ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SchemeCnTable({
  rows,
  onMarkReceived,
}: {
  rows: ReconWorklists['pending_credit_notes_scheme'];
  onMarkReceived: (creditNoteNumber: string) => void | Promise<void>;
}) {
  const [saving, setSaving] = useState<Set<string>>(new Set());
  if (!rows.length) return <EmptyState message="Nothing pending — all scheme credit notes received." />;

  const mark = async (cn: string) => {
    setSaving((prev) => new Set([...prev, cn]));
    try {
      await onMarkReceived(cn);
    } finally {
      setSaving((prev) => {
        const next = new Set(prev);
        next.delete(cn);
        return next;
      });
    }
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="pb-2 pr-4 font-medium">CN #</th>
            <th className="pb-2 pr-4 font-medium">Vendor</th>
            <th className="pb-2 pr-4 font-medium">Rebate #</th>
            <th className="pb-2 pr-4 font-medium text-right">Amount</th>
            <th className="pb-2 pr-4 font-medium">Raised</th>
            <th className="pb-2 font-medium text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const cn = row.credit_note_number;
            const busy = cn ? saving.has(cn) : false;
            return (
              <tr key={cn ?? i} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-2 pr-4 font-medium text-gray-800">{cn ?? '-'}</td>
                <td className="py-2 pr-4 text-gray-600">{row.vendor_name ?? row.vendor_id ?? '-'}</td>
                <td className="py-2 pr-4 text-gray-500 text-xs">{row.rebate_id ?? '-'}</td>
                <td className="py-2 pr-4 text-right text-green-700 font-medium">
                  {row.amount != null ? inr(row.amount) : row.amount_paise != null ? inr(row.amount_paise / 100) : '-'}
                </td>
                <td className="py-2 pr-4 text-gray-500">{fmtDate(row.created_at)}</td>
                <td className="py-2 text-right">
                  {cn ? (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void mark(cn)}
                      className="text-xs font-medium px-2.5 py-1 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                      title="Mark this scheme credit note as physically received"
                    >
                      {busy ? 'Saving…' : 'Mark received'}
                    </button>
                  ) : (
                    <span className="text-xs text-gray-400">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ReturnCnTable({ rows }: { rows: ReconWorklists['pending_credit_notes_return'] }) {
  if (!rows.length) return <EmptyState message="Nothing pending — all return credit notes issued." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-100">
            <th className="pb-2 pr-4 font-medium">Return #</th>
            <th className="pb-2 pr-4 font-medium">Vendor</th>
            <th className="pb-2 pr-4 font-medium">Status</th>
            <th className="pb-2 pr-4 font-medium text-right">Value</th>
            <th className="pb-2 font-medium">CN #</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.return_id ?? i} className="border-b border-gray-50 hover:bg-gray-50">
              <td className="py-2 pr-4 font-medium text-gray-800">{row.return_id ?? '-'}</td>
              <td className="py-2 pr-4 text-gray-600">{row.vendor_name ?? row.vendor_id ?? '-'}</td>
              <td className="py-2 pr-4">
                <span className="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">
                  {row.status ?? '-'}
                </span>
              </td>
              <td className="py-2 pr-4 text-right">{inr(row.total_value)}</td>
              <td className="py-2 text-gray-500 text-xs">{row.credit_note_number ?? 'Not issued'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- Main page ---------------------------------------------------------------

export default function ReconConsole() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const storeId = user?.activeStoreId;
  const canApprove = hasRole(EXCEPTION_APPROVE_ROLES);

  // Queue: invoices + their recon blocks
  const [invoices, setInvoices] = useState<PurchaseInvoice[]>([]);
  const [reconMap, setReconMap] = useState<Record<string, ReconBlock>>({});
  const [savingIds, setSavingIds] = useState<Set<string>>(new Set());
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [loadingBills, setLoadingBills] = useState(true);

  // Filters
  const [activeChip, setActiveChip] = useState<QueueChipId>('all');
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [searchText, setSearchText] = useState('');

  // Batch confirm (auto-matched rows only)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchDone, setBatchDone] = useState(0);
  const [batchTotal, setBatchTotal] = useState(0);
  const [batchErrors, setBatchErrors] = useState<Record<string, string>>({});

  // Exception review modal target
  const [approveTarget, setApproveTarget] = useState<PurchaseInvoice | null>(null);

  // Worklists (secondary "Documents pending" panel)
  const [worklists, setWorklists] = useState<ReconWorklists>({
    stock_yet_to_receive: [],
    vendor_returns: [],
    pending_credit_notes_scheme: [],
    pending_credit_notes_return: [],
  });
  const [loadingWorklists, setLoadingWorklists] = useState(true);

  // ---- Load invoices ---------------------------------------------------------

  const loadInvoices = useCallback(async () => {
    setLoadingBills(true);
    try {
      const { purchase_invoices: rows } = await purchaseInvoicesApi.list({
        ...(storeId ? { store_id: storeId } : {}),
        ...(statusFilter ? { status: statusFilter } : {}),
      });
      setInvoices(rows);
      // The list rows are full docs, so most already embed the recon block --
      // seed from those and only fetch the stragglers (fail-soft) instead of
      // the old N+1 GET per invoice.
      const map: Record<string, ReconBlock> = {};
      const missing: PurchaseInvoice[] = [];
      rows.forEach((inv) => {
        const id = invoiceRowId(inv);
        if (inv.recon) map[id] = { ...DEFAULT_RECON, ...inv.recon };
        else missing.push(inv);
      });
      if (missing.length > 0) {
        const blocks = await Promise.all(
          missing.map((inv) => purchaseReconApi.getRecon(invoiceRowId(inv)))
        );
        missing.forEach((inv, i) => {
          map[invoiceRowId(inv)] = blocks[i]?.recon ?? { ...DEFAULT_RECON };
        });
      }
      setReconMap(map);
      setSelectedIds(new Set());
      setBatchErrors({});
    } catch (err) {
      toast.error('Failed to load purchase invoices');
      console.error(err);
    } finally {
      setLoadingBills(false);
    }
  }, [storeId, statusFilter, toast]);

  // ---- Load worklists ----------------------------------------------------------

  const loadWorklists = useCallback(async () => {
    setLoadingWorklists(true);
    const data = await purchaseReconApi.getWorklists(
      storeId ? { store_id: storeId } : undefined
    );
    setWorklists(data);
    setLoadingWorklists(false);
  }, [storeId]);

  useEffect(() => { void loadInvoices(); }, [loadInvoices]);
  useEffect(() => { void loadWorklists(); }, [loadWorklists]);

  // ---- Single-flag toggle (unchanged per-invoice write) -------------------------

  const handleToggle = useCallback(async (invoiceId: string, flag: ReconFlag, value: boolean) => {
    setReconMap((prev) => ({
      ...prev,
      [invoiceId]: { ...(prev[invoiceId] ?? DEFAULT_RECON), [flag]: value },
    }));
    setSavingIds((prev) => new Set([...prev, invoiceId]));
    try {
      const result = await purchaseReconApi.upsertRecon(invoiceId, { [flag]: value });
      setReconMap((prev) => ({ ...prev, [invoiceId]: result.recon }));
      toast.success(`${FLAG_LABELS[flag]} ${value ? 'ticked' : 'unticked'}`);
    } catch (err) {
      setReconMap((prev) => ({
        ...prev,
        [invoiceId]: { ...(prev[invoiceId] ?? DEFAULT_RECON), [flag]: !value },
      }));
      toast.error(`Failed to update ${FLAG_LABELS[flag]}`);
      console.error(err);
    } finally {
      setSavingIds((prev) => {
        const next = new Set(prev);
        next.delete(invoiceId);
        return next;
      });
    }
  }, [toast]);

  // ---- Attestation note (same per-invoice endpoint, `note` field) ----------------

  const handleSaveNote = useCallback(async (invoiceId: string, text: string) => {
    setSavingIds((prev) => new Set([...prev, invoiceId]));
    try {
      const result = await purchaseReconApi.upsertRecon(invoiceId, { note: text });
      setReconMap((prev) => ({ ...prev, [invoiceId]: result.recon }));
      toast.success('Note saved');
    } catch (err) {
      toast.error('Failed to save note');
      console.error(err);
    } finally {
      setSavingIds((prev) => {
        const next = new Set(prev);
        next.delete(invoiceId);
        return next;
      });
    }
  }, [toast]);

  // ---- Batch confirm: sequential loop of the EXISTING per-invoice endpoint -------
  // Only the Reconciled attestation is batchable (auto-matched rows). Each write
  // is an individual POST /purchase-invoices/{id}/recon so the server-side
  // per-document attestation (who/when per invoice) is fully preserved.
  // Continue-on-error: failures are collected + surfaced per-row; the rest
  // still confirm.

  const runBatchConfirm = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0 || batchRunning) return;
    setBatchRunning(true);
    setBatchTotal(ids.length);
    setBatchDone(0);
    setBatchErrors({});
    const failures: Record<string, string> = {};
    for (const id of ids) {
      setSavingIds((prev) => new Set([...prev, id]));
      try {
        const result = await purchaseReconApi.upsertRecon(id, { reconciled: true });
        setReconMap((prev) => ({ ...prev, [id]: result.recon }));
        setSelectedIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      } catch (err) {
        failures[id] =
          err && typeof err === 'object' && 'response' in err
            ? String((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Failed')
            : err instanceof Error ? err.message : 'Failed';
        console.error(err);
      } finally {
        setSavingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        setBatchDone((d) => d + 1);
      }
    }
    setBatchErrors(failures);
    const failed = Object.keys(failures).length;
    const ok = ids.length - failed;
    if (failed === 0) {
      toast.success(`Confirmed ${ok} invoice${ok !== 1 ? 's' : ''} as Reconciled`);
    } else {
      toast.error(`${ok} confirmed, ${failed} failed — failed rows stay selected`);
    }
    setBatchRunning(false);
  }, [selectedIds, batchRunning, toast]);

  // ---- Mark a scheme credit-note received ------------------------------------

  const handleMarkCnReceived = useCallback(async (creditNoteNumber: string) => {
    try {
      await purchaseReconApi.markSchemeCnReceived(creditNoteNumber);
      setWorklists((prev) => ({
        ...prev,
        pending_credit_notes_scheme: prev.pending_credit_notes_scheme.filter(
          (r) => r.credit_note_number !== creditNoteNumber,
        ),
      }));
      toast.success(`Credit note ${creditNoteNumber} marked received`);
    } catch (err) {
      toast.error(`Failed to mark ${creditNoteNumber} received`);
      console.error(err);
    }
  }, [toast]);

  // ---- Derived: filtered + attention-sorted queue -----------------------------

  const reconFor = useCallback(
    (inv: PurchaseInvoice): ReconBlock => reconMap[invoiceRowId(inv)] ?? DEFAULT_RECON,
    [reconMap]
  );

  const filteredInvoices = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    const rows = invoices.filter((inv) => {
      if (!chipMatches(activeChip, inv, reconFor(inv))) return false;
      if (!q) return true;
      return (
        (inv.vendor_name ?? '').toLowerCase().includes(q) ||
        (inv.vendor_invoice_no ?? '').toLowerCase().includes(q) ||
        (inv.vendor_id ?? '').toLowerCase().includes(q)
      );
    });
    // Needs-attention first, then newest.
    return [...rows].sort((a, b) => {
      const ra = attentionRank(a, reconFor(a));
      const rb = attentionRank(b, reconFor(b));
      if (ra !== rb) return ra - rb;
      const da = a.vendor_invoice_date || a.created_at || '';
      const db = b.vendor_invoice_date || b.created_at || '';
      return db.localeCompare(da);
    });
  }, [invoices, searchText, activeChip, reconFor]);

  const chipCounts = useMemo(() => {
    const counts = {} as Record<QueueChipId, number>;
    for (const { id } of QUEUE_CHIPS) {
      counts[id] = invoices.filter((inv) => chipMatches(id, inv, reconFor(inv))).length;
    }
    return counts;
  }, [invoices, reconFor]);

  const allDoneCount = chipCounts.settled ?? 0;

  // Visible rows that can still be batch-selected (auto-matched, not reconciled)
  const visibleEligibleIds = useMemo(
    () => filteredInvoices.filter((inv) => isBatchEligible(inv, reconFor(inv))).map(invoiceRowId),
    [filteredInvoices, reconFor]
  );
  const allVisibleSelected =
    visibleEligibleIds.length > 0 && visibleEligibleIds.every((id) => selectedIds.has(id));

  const toggleSelectAllVisible = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) visibleEligibleIds.forEach((id) => next.delete(id));
      else visibleEligibleIds.forEach((id) => next.add(id));
      return next;
    });
  };

  // ---- Render -----------------------------------------------------------------

  return (
    <div className="p-4 max-w-6xl mx-auto space-y-6">
      {/* Page header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Recon Console</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            One queue of purchase invoices — attest each (Reconciled / Tally / GST / Paid), batch-confirm auto-matched ones
          </p>
        </div>
        <div className="flex items-center gap-2">
          {invoices.length > 0 && (
            <span className="text-xs text-gray-500 bg-gray-100 px-2.5 py-1 rounded-full">
              {allDoneCount}/{invoices.length} fully reconciled
            </span>
          )}
          <Link
            to="/purchase?tab=purchase-invoices"
            className="text-xs font-medium text-blue-600 hover:underline whitespace-nowrap"
            title="Book invoices, match Delivery Challans in bulk, and view 3-way match detail"
          >
            Invoices &amp; DC matching
          </Link>
          <button
            onClick={() => { void loadInvoices(); void loadWorklists(); }}
            className="btn-primary flex items-center gap-1.5 text-sm"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>
      </div>

      {/* Alert if there are any pending worklist items */}
      {!loadingWorklists && (
        worklists.stock_yet_to_receive.length > 0 ||
        worklists.vendor_returns.length > 0 ||
        worklists.pending_credit_notes_scheme.length > 0 ||
        worklists.pending_credit_notes_return.length > 0
      ) && (
        <div className="flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
          <AlertTriangle size={16} className="shrink-0 mt-0.5 text-amber-500" />
          <span>
            Pending documents below —
            {worklists.stock_yet_to_receive.length > 0 && ` ${worklists.stock_yet_to_receive.length} PO(s) with unreceived stock,`}
            {worklists.vendor_returns.length > 0 && ` ${worklists.vendor_returns.length} open vendor return(s),`}
            {worklists.pending_credit_notes_scheme.length > 0 && ` ${worklists.pending_credit_notes_scheme.length} scheme CN(s) pending,`}
            {worklists.pending_credit_notes_return.length > 0 && ` ${worklists.pending_credit_notes_return.length} return CN(s) not issued.`}
          </span>
        </div>
      )}

      {/* ---- Unified invoice queue ---- */}
      <section>
        <div className="flex flex-wrap items-center gap-3 mb-2">
          <h2 className="font-semibold text-gray-800 text-base">Invoice Queue</h2>
          <input
            type="text"
            placeholder="Search vendor or invoice #..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="input-field text-sm py-1.5 px-3 w-56"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="input-field text-sm py-1.5 px-3"
            aria-label="Payment status filter"
          >
            <option value="">All statuses</option>
            <option value="OUTSTANDING">Outstanding</option>
            <option value="PARTIAL">Partial</option>
            <option value="PAID">Paid</option>
          </select>
        </div>

        {/* Filter chips */}
        <div className="flex flex-wrap gap-1 mb-3" role="tablist" aria-label="Queue filters">
          {QUEUE_CHIPS.map(({ id, label }) => (
            <button
              key={id}
              role="tab"
              aria-selected={activeChip === id}
              onClick={() => setActiveChip(id)}
              className={[
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors border',
                activeChip === id
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50',
              ].join(' ')}
            >
              {label}
              <span className={[
                'text-[10px] px-1.5 py-0.5 rounded-full',
                activeChip === id ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-500',
              ].join(' ')}>
                {chipCounts[id] ?? 0}
              </span>
            </button>
          ))}
        </div>

        {loadingBills ? (
          <div className="flex items-center gap-2 py-10 justify-center text-gray-400">
            <Loader2 size={20} className="animate-spin" />
            <span className="text-sm">Loading invoices...</span>
          </div>
        ) : filteredInvoices.length === 0 ? (
          <div className="card p-8 text-center text-gray-400">
            <FileText size={28} className="mx-auto mb-2" />
            <p className="text-sm">
              {invoices.length === 0
                ? 'No purchase invoices found for this store.'
                : 'No invoices match this filter.'}
            </p>
          </div>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="text-xs text-gray-500 border-b border-gray-100 bg-gray-50">
                  <th className="py-2 pl-3 pr-1 w-8">
                    {visibleEligibleIds.length > 0 && (
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={toggleSelectAllVisible}
                        disabled={batchRunning}
                        aria-label="Select all auto-matched invoices in view"
                        className="accent-blue-600"
                        title="Select all auto-matched invoices in view"
                      />
                    )}
                  </th>
                  <th className="py-2 pr-3 font-medium">Vendor / Invoice</th>
                  <th className="py-2 pr-3 font-medium">Date</th>
                  <th className="py-2 pr-3 font-medium">Documents</th>
                  <th className="py-2 pr-3 font-medium text-right">Amount</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-2 font-medium">Attestations</th>
                  <th className="py-2 pr-3 w-8"></th>
                </tr>
              </thead>
              <tbody>
                {filteredInvoices.map((inv) => {
                  const id = invoiceRowId(inv);
                  const recon = reconFor(inv);
                  return (
                    <QueueRow
                      key={id}
                      invoice={inv}
                      recon={recon}
                      saving={savingIds.has(id)}
                      expanded={expandedIds.has(id)}
                      selectable={isBatchEligible(inv, recon) && !batchRunning}
                      selected={selectedIds.has(id)}
                      batchError={batchErrors[id]}
                      canApprove={canApprove}
                      onToggleFlag={(flag, value) => handleToggle(id, flag, value)}
                      onToggleSelect={() => {
                        setSelectedIds((prev) => {
                          const next = new Set(prev);
                          if (next.has(id)) next.delete(id); else next.add(id);
                          return next;
                        });
                      }}
                      onExpand={() => {
                        setExpandedIds((prev) => {
                          const next = new Set(prev);
                          if (next.has(id)) next.delete(id); else next.add(id);
                          return next;
                        });
                      }}
                      onSaveNote={(text) => handleSaveNote(id, text)}
                      onReview={() => setApproveTarget(inv)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Batch confirm bar: appears when auto-matched rows are checked */}
        {(selectedIds.size > 0 || batchRunning) && (
          <div className="sticky bottom-3 mt-3 flex flex-wrap items-center justify-between gap-3 bg-white border border-blue-200 rounded-lg shadow-lg px-4 py-2.5">
            <div className="flex items-center gap-2 text-sm text-gray-700">
              <ListChecks size={16} className="text-blue-600" />
              {batchRunning ? (
                <span>Confirming {batchDone}/{batchTotal}… (one invoice at a time)</span>
              ) : (
                <span>
                  <span className="font-semibold">{selectedIds.size}</span> auto-matched invoice{selectedIds.size !== 1 ? 's' : ''} selected
                  <span className="text-gray-400 ml-2 text-xs">Only the Reconciled tick is batchable — Tally / GST / Paid stay per-invoice</span>
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {!batchRunning && (
                <button
                  type="button"
                  onClick={() => setSelectedIds(new Set())}
                  className="text-xs text-gray-500 hover:text-gray-700 px-2 py-1"
                >
                  Clear
                </button>
              )}
              <button
                type="button"
                disabled={batchRunning || selectedIds.size === 0}
                onClick={() => void runBatchConfirm()}
                className="btn-primary flex items-center gap-1.5 text-sm disabled:opacity-60"
              >
                {batchRunning
                  ? <Loader2 size={14} className="animate-spin" />
                  : <CheckSquare size={14} />}
                Confirm {selectedIds.size} selected
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ---- Documents pending (secondary worklists) ---- */}
      <section>
        {loadingWorklists ? (
          <div className="card p-8 flex items-center gap-2 justify-center text-gray-400">
            <Loader2 size={20} className="animate-spin" />
            <span className="text-sm">Loading worklists...</span>
          </div>
        ) : (
          <Worklists lists={worklists} onMarkCnReceived={handleMarkCnReceived} />
        )}
      </section>

      {/* Exception review modal */}
      {approveTarget && (
        <ApproveExceptionModal
          invoice={approveTarget}
          onClose={() => setApproveTarget(null)}
          onApproved={(updated) => {
            const targetId = invoiceRowId(approveTarget);
            setInvoices((prev) =>
              prev.map((p) => (invoiceRowId(p) === targetId ? { ...p, ...updated } : p)),
            );
            setApproveTarget(null);
          }}
        />
      )}
    </div>
  );
}
