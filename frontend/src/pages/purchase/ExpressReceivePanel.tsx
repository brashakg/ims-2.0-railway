// ============================================================================
// IMS 2.0 - Express Receive Panel  (procurement Phase 2 — deliveries inbox)
// ============================================================================
// Guided 3-step receive for ONE box against ONE PO. Council ruling: express
// receive for ALL receiving staff; strict no-paper-no-stock.
//
//   STEP 1 — BILL FIRST: the vendor invoice upload (shared AttachmentZone) +
//            invoice number/date. Cannot proceed without BOTH the file and the
//            invoice number (the server enforces the same gate).
//   STEP 2 — CHECK ITEMS: lines prefilled from the PO residuals with
//            received = accepted = residual. One primary button confirms a
//            clean box; ANY edit that breaks clean (rejected > 0, i.e.
//            accepted != received) flips the flow to the EXISTING two-step
//            create+accept path via onFallbackToTwoStep.
//   STEP 3 — ONE CONFIRM: POST /vendors/grn/express.
//            201                 -> success panel (GRN no, units, PO chip,
//                                   "Sent to accounts", invoice total)
//            EXPRESS_NOT_CLEAN   -> auto fallback to the two-step path
//            EXPRESS_PARTIAL     -> bold recovery banner -> pending receipts
//            ATTACHMENT_*        -> back to step 1 with the toast

import { useMemo, useRef, useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
  Loader2,
  Package,
  Upload,
  X,
} from 'lucide-react';
import { grnCockpitApi } from '../../services/api/grnCockpit';
import type {
  CockpitOpenPO,
  ExpressReceiveItemInput,
  ExpressReceiveResult,
  UploadDocResult,
} from '../../services/api/grnCockpit';
import { useToast } from '../../context/ToastContext';
import { PurchaseStatusChip } from '../../components/purchase/PurchaseStatusChip';
import { AttachmentZone } from './AttachmentZone';

// ---- Two-step fallback contract (cockpit opens its classic form with this) --

export interface TwoStepPrefillLine {
  product_id: string;
  product_name: string;
  sku: string | null;
  ordered_qty: number;
  received_qty: number;
  accepted_qty: number;
  rejected_qty: number;
  batch_code: string;
  expiry_date: string;
  unit_price: number | null;
}

export interface TwoStepPrefill {
  /** Why express could not apply — 'edited' (user broke clean in step 2) or
   *  'server-rejected' (backend answered EXPRESS_NOT_CLEAN / STANDARD_ONLY). */
  reason: 'edited' | 'server-rejected';
  vendorInvoiceNo: string;
  upload: UploadDocResult | null;
  lines: TwoStepPrefillLine[];
}

// ---- Local line state --------------------------------------------------------

interface PanelLine {
  product_id: string;
  product_name: string;
  sku: string | null;
  ordered_qty: number;
  /** PO residual (ordered - already received) — the prefill quantity. */
  residual_qty: number;
  received_qty: number;
  rejected_qty: number;
  batch_code: string;
  expiry_date: string;
  unit_price: number | null;
  expanded: boolean;
  touched: boolean;
}

type PanelStage =
  | { kind: 'steps'; step: 1 | 2 | 3 }
  | { kind: 'success'; result: ExpressReceiveResult }
  | { kind: 'partial'; grnNumber: string; message: string };

interface ExpressReceivePanelProps {
  po: CockpitOpenPO;
  vendorName?: string;
  onCancel: () => void;
  /** 201 success acknowledged — cockpit closes the panel and refreshes. */
  onReceived: () => void;
  /** Flow broke clean — cockpit opens the EXISTING two-step receive form
   *  prefilled with this state (and shows the differences note). */
  onFallbackToTwoStep: (prefill: TwoStepPrefill) => void;
  /** EXPRESS_PARTIAL recovery — cockpit closes the panel and surfaces the
   *  pending-receipts panel (it already handles accept/void). */
  onOpenPendingReceipts: (grnNumber: string) => void;
}

// ---- Helpers -----------------------------------------------------------------

const fmtMoney = (n: number | null | undefined) =>
  n == null
    ? null
    : `₹${Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

const todayISO = () => new Date().toISOString().split('T')[0];

/** Pull the FastAPI error detail (object or string) out of an axios error. */
function apiErrorDetail(err: unknown): unknown {
  return (err as { response?: { data?: { detail?: unknown } } })?.response?.data
    ?.detail;
}

function StepIndicator({ step }: { step: 1 | 2 | 3 }) {
  const items: Array<{ n: 1 | 2 | 3; label: string }> = [
    { n: 1, label: 'Bill' },
    { n: 2, label: 'Items' },
    { n: 3, label: 'Confirm' },
  ];
  return (
    <div className="flex items-center gap-2" aria-label={`Step ${step} of 3`}>
      {items.map((it, i) => (
        <div key={it.n} className="flex items-center gap-2">
          {i > 0 && (
            <div
              style={{ width: 24, height: 1.5, background: 'var(--line)' }}
              aria-hidden
            />
          )}
          <span
            className="inline-flex items-center gap-1.5 text-xs font-medium"
            style={{ color: it.n === step ? 'var(--ink)' : 'var(--ink-4)' }}
          >
            <span
              className="inline-flex items-center justify-center rounded-full mono"
              style={{
                width: 20,
                height: 20,
                fontSize: 11,
                border: '1.5px solid',
                borderColor: it.n <= step ? 'var(--bv)' : 'var(--line)',
                background: it.n < step ? 'var(--bv)' : 'transparent',
                color: it.n < step ? '#fff' : it.n === step ? 'var(--bv)' : 'var(--ink-4)',
              }}
            >
              {it.n}
            </span>
            {it.label}
          </span>
        </div>
      ))}
    </div>
  );
}

// ============================================================================
// Panel
// ============================================================================

export function ExpressReceivePanel({
  po,
  vendorName,
  onCancel,
  onReceived,
  onFallbackToTwoStep,
  onOpenPendingReceipts,
}: ExpressReceivePanelProps) {
  const toast = useToast();

  const [stage, setStage] = useState<PanelStage>({ kind: 'steps', step: 1 });

  // ---- Step 1: bill first ----------------------------------------------------
  const [upload, setUpload] = useState<UploadDocResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [invoiceNo, setInvoiceNo] = useState('');
  const [invoiceDate, setInvoiceDate] = useState(todayISO());

  // ---- Step 2: lines prefilled from the PO residuals --------------------------
  const [lines, setLines] = useState<PanelLine[]>(() =>
    po.lines
      .filter((l) => l.pending_qty > 0)
      .map((l) => ({
        product_id: l.product_id ?? '',
        product_name: l.product_name ?? l.product_id ?? '',
        sku: l.sku,
        ordered_qty: l.ordered_qty,
        residual_qty: l.pending_qty,
        received_qty: l.pending_qty,
        rejected_qty: 0,
        batch_code: '',
        expiry_date: '',
        unit_price: l.unit_price ?? null,
        expanded: false,
        touched: false,
      })),
  );

  const [submitting, setSubmitting] = useState(false);
  // The fallback must fire exactly once (an edit can re-trigger onChange).
  const fallbackFired = useRef(false);

  const anyTouched = lines.some((l) => l.touched);
  const totalUnits = useMemo(
    () => lines.reduce((s, l) => s + l.received_qty, 0),
    [lines],
  );
  const includedLines = useMemo(
    () => lines.filter((l) => l.received_qty > 0),
    [lines],
  );

  const step1Ready = upload !== null && !uploading && invoiceNo.trim().length > 0;

  // ---- Handlers ---------------------------------------------------------------

  const handleFileSelect = async (file: File) => {
    setUploading(true);
    setUpload(null);
    try {
      const result = await grnCockpitApi.uploadDoc(file);
      if (!result.file_id) {
        toast.warning(
          'File uploaded but could not be persisted to the file store — ' +
            'express receive needs the saved bill; you may be asked to re-upload.',
        );
      }
      setUpload(result);
      toast.success(`Uploaded: ${result.filename}`);
    } catch (err: unknown) {
      const detail = apiErrorDetail(err);
      toast.error(typeof detail === 'string' ? detail : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const buildPrefill = (
    ls: PanelLine[],
    reason: TwoStepPrefill['reason'],
  ): TwoStepPrefill => ({
    reason,
    vendorInvoiceNo: invoiceNo,
    upload,
    lines: ls.map((l) => ({
      product_id: l.product_id,
      product_name: l.product_name,
      sku: l.sku,
      ordered_qty: l.ordered_qty,
      received_qty: l.received_qty,
      accepted_qty: Math.max(0, l.received_qty - l.rejected_qty),
      rejected_qty: l.rejected_qty,
      batch_code: l.batch_code,
      expiry_date: l.expiry_date,
      unit_price: l.unit_price,
    })),
  });

  const fireFallback = (ls: PanelLine[], reason: TwoStepPrefill['reason']) => {
    if (fallbackFired.current) return;
    fallbackFired.current = true;
    onFallbackToTwoStep(buildPrefill(ls, reason));
  };

  const toggleExpand = (idx: number) =>
    setLines((prev) =>
      prev.map((l, i) => (i === idx ? { ...l, expanded: !l.expanded } : l)),
    );

  const applyLineEdit = (
    idx: number,
    field: 'received_qty' | 'rejected_qty' | 'batch_code' | 'expiry_date',
    value: number | string,
  ) => {
    const next = lines.map((l, i) => {
      if (i !== idx) return l;
      const u: PanelLine = { ...l, touched: true };
      if (field === 'received_qty') {
        u.received_qty = Math.max(0, Number(value) || 0);
        u.rejected_qty = Math.min(u.rejected_qty, u.received_qty);
      } else if (field === 'rejected_qty') {
        u.rejected_qty = Math.max(0, Math.min(Number(value) || 0, u.received_qty));
      } else if (field === 'batch_code') {
        u.batch_code = String(value);
      } else {
        u.expiry_date = String(value);
      }
      return u;
    });
    setLines(next);
    // ANY edit that breaks clean (rejected>0 <=> accepted != received) flips
    // the flow to the existing two-step receive — automatically.
    if (next.some((l) => l.rejected_qty > 0)) {
      fireFallback(next, 'edited');
    }
  };

  const handleConfirm = async () => {
    const items: ExpressReceiveItemInput[] = includedLines.map((l) => ({
      product_id: l.product_id,
      received_qty: l.received_qty,
      accepted_qty: l.received_qty - l.rejected_qty,
      rejected_qty: l.rejected_qty,
      batch_code: l.batch_code.trim() || undefined,
      expiry_date: l.expiry_date.trim() || undefined,
    }));
    if (items.length === 0) {
      toast.error('Nothing to receive — every line is at 0 units');
      return;
    }
    setSubmitting(true);
    try {
      const result = await grnCockpitApi.expressReceive({
        po_id: po.po_id,
        vendor_invoice_no: invoiceNo.trim(),
        vendor_invoice_date: invoiceDate || undefined,
        items,
        attachment_file_id: upload?.file_id ?? '',
        attachment_filename: upload?.filename,
        attachment_mime: upload?.mime,
      });
      setStage({ kind: 'success', result });
    } catch (err: unknown) {
      const detail = apiErrorDetail(err);
      const d = (detail && typeof detail === 'object' ? detail : {}) as {
        code?: string;
        message?: string;
        grn_number?: string;
      };
      if (d.code === 'EXPRESS_NOT_CLEAN' || d.code === 'EXPRESS_STANDARD_ONLY') {
        // Server disagrees this is a clean express receipt — hand the exact
        // same state to the existing two-step path (it carries the full
        // discrepancy controls).
        fireFallback(lines, 'server-rejected');
      } else if (d.code === 'EXPRESS_PARTIAL') {
        // The GRN row EXISTS but was not (fully) accepted — never leave it
        // stranded invisibly: point straight at the pending-receipts panel.
        setStage({
          kind: 'partial',
          grnNumber: d.grn_number ?? '',
          message:
            d.message ??
            'The receipt was created but not accepted — finish it from the pending receipts panel.',
        });
      } else if (d.code === 'ATTACHMENT_REQUIRED' || d.code === 'ATTACHMENT_INVALID') {
        setUpload(null);
        setStage({ kind: 'steps', step: 1 });
        toast.error(
          d.message ?? 'The bill attachment did not go through — upload it again.',
        );
      } else {
        toast.error(
          d.message ??
            (typeof detail === 'string'
              ? detail
              : 'Express receive failed — try again or use the step-by-step receive.'),
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  // ---- Render: EXPRESS_PARTIAL recovery ---------------------------------------

  if (stage.kind === 'partial') {
    return (
      <div className="card" style={{ padding: 20 }}>
        <div
          className="rounded-lg px-4 py-4"
          style={{
            border: '2px solid var(--warn)',
            background: 'rgba(245,158,11,0.08)',
          }}
          role="alert"
        >
          <p className="font-bold text-sm mb-1" style={{ color: 'var(--ink)' }}>
            Receipt {stage.grnNumber} was saved but is NOT on the shelf yet
          </p>
          <p className="text-sm mb-3" style={{ color: 'var(--ink-2)' }}>
            {stage.message}
          </p>
          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              className="btn accent"
              onClick={() => onOpenPendingReceipts(stage.grnNumber)}
            >
              <Package className="w-4 h-4" />
              Open pending receipts
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---- Render: success ---------------------------------------------------------

  if (stage.kind === 'success') {
    const r = stage.result;
    const total = r.invoice_draft?.totals?.total ?? null;
    return (
      <div className="card" style={{ padding: 24 }}>
        <div className="flex items-start gap-3">
          <CheckCircle2
            className="w-8 h-8 flex-shrink-0"
            style={{ color: 'var(--ok)' }}
          />
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold mb-1" style={{ color: 'var(--ink)' }}>
              Box received and put on shelf.
            </h3>
            <p className="text-sm mb-3" style={{ color: 'var(--ink-3)' }}>
              Goods receipt <span className="mono">{r.grn_number}</span>
              {r.accepted_units != null && (
                <> &middot; {r.accepted_units} unit{r.accepted_units === 1 ? '' : 's'} added to stock</>
              )}
            </p>
            <div className="flex items-center gap-2 mb-3 flex-wrap">
              <span className="text-xs" style={{ color: 'var(--ink-4)' }}>
                PO {po.po_number}
              </span>
              <PurchaseStatusChip status={r.po_status} kind="po" />
            </div>
            {r.match_preview && (
              <p className="text-sm mb-1" style={{ color: 'var(--ink-2)' }}>
                Sent to accounts: <span className="font-medium">{r.match_preview.match_status}</span>
                {r.match_preview.exception_count > 0 &&
                  ` (${r.match_preview.exception_count} exception${r.match_preview.exception_count === 1 ? '' : 's'})`}
              </p>
            )}
            {total != null && (
              <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
                Bill total: <span className="font-medium">{fmtMoney(total)}</span>
              </p>
            )}
            <div className="flex gap-3 mt-4">
              <button type="button" className="btn accent" onClick={onReceived}>
                Done
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ---- Render: steps -------------------------------------------------------------

  const step = stage.step;

  return (
    <div className="space-y-4">
      {/* Panel header */}
      <div className="card">
        <div className="card-head">
          <h3 className="flex items-center gap-2">
            <Package className="w-4 h-4" />
            Receiving box against <span className="mono">{po.po_number}</span>
            {vendorName && (
              <span className="text-xs font-normal" style={{ color: 'var(--ink-4)' }}>
                {vendorName}
              </span>
            )}
          </h3>
          <div className="flex items-center gap-3">
            <StepIndicator step={step} />
            <button type="button" className="btn sm" onClick={onCancel}>
              <X className="w-3.5 h-3.5" /> Cancel
            </button>
          </div>
        </div>

        {/* STEP 1 — BILL FIRST */}
        {step === 1 && (
          <div className="card-body space-y-4">
            <p className="text-sm" style={{ color: 'var(--ink-3)' }}>
              <span className="font-medium" style={{ color: 'var(--ink)' }}>
                Bill first.
              </span>{' '}
              No paper, no stock — upload the vendor&apos;s invoice and enter its
              number before touching the box.
            </p>
            <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
              <AttachmentZone
                uploaded={upload}
                uploading={uploading}
                onSelect={handleFileSelect}
                onRemove={() => setUpload(null)}
              />
              <div className="space-y-3">
                <div>
                  <label
                    className="block text-xs font-medium mb-1"
                    style={{ color: 'var(--ink-4)' }}
                  >
                    Vendor invoice number *
                  </label>
                  <input
                    type="text"
                    value={invoiceNo}
                    onChange={(e) => setInvoiceNo(e.target.value)}
                    placeholder="e.g. JJ/26/05/2240"
                    className="input w-full"
                    aria-label="Vendor invoice number"
                  />
                </div>
                <div>
                  <label
                    className="block text-xs font-medium mb-1"
                    style={{ color: 'var(--ink-4)' }}
                  >
                    Invoice date
                  </label>
                  <input
                    type="date"
                    value={invoiceDate}
                    onChange={(e) => setInvoiceDate(e.target.value)}
                    className="input w-full"
                    aria-label="Vendor invoice date"
                  />
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="text-xs" style={{ color: 'var(--err)' }}>
                {!upload && !uploading && 'Upload the vendor invoice / challan first'}
                {upload && !invoiceNo.trim() && 'Enter the vendor invoice number'}
              </div>
              <button
                type="button"
                className="btn accent"
                disabled={!step1Ready}
                onClick={() => setStage({ kind: 'steps', step: 2 })}
              >
                <Upload className="w-4 h-4" />
                Continue — check items
              </button>
            </div>
          </div>
        )}

        {/* STEP 2 — CHECK ITEMS */}
        {step === 2 && (
          <div className="card-body space-y-3">
            <p className="text-sm" style={{ color: 'var(--ink-3)' }}>
              Check the box against the order. Quantities are prefilled with what
              is still due on <span className="mono">{po.po_number}</span> — tap a
              line only if something is different (short, damaged, batch/expiry).
            </p>
            <div className="space-y-2">
              {lines.map((l, idx) => (
                <div
                  key={`${l.product_id}-${idx}`}
                  className="rounded-lg"
                  style={{ border: '1px solid var(--line)', background: 'var(--surface)' }}
                >
                  <button
                    type="button"
                    className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left"
                    onClick={() => toggleExpand(idx)}
                    aria-label={`Edit line ${l.product_name}`}
                  >
                    <div className="min-w-0 flex-1">
                      <p
                        className="font-medium text-sm truncate"
                        style={{ color: 'var(--ink)' }}
                      >
                        {l.product_name}
                      </p>
                      {l.sku && (
                        <p className="mono text-xs" style={{ color: 'var(--ink-4)' }}>
                          {l.sku}
                        </p>
                      )}
                    </div>
                    <div className="text-right flex-shrink-0">
                      <p className="text-sm mono" style={{ color: 'var(--ink)' }}>
                        {l.received_qty}
                        <span className="text-xs" style={{ color: 'var(--ink-4)' }}>
                          {' '}/ {l.residual_qty} due
                        </span>
                      </p>
                      {l.rejected_qty > 0 && (
                        <p className="text-xs" style={{ color: 'var(--err)' }}>
                          {l.rejected_qty} rejected
                        </p>
                      )}
                    </div>
                    {l.expanded ? (
                      <ChevronUp className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--ink-4)' }} />
                    ) : (
                      <ChevronDown className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--ink-4)' }} />
                    )}
                  </button>
                  {l.expanded && (
                    <div
                      className="px-3 pb-3 grid grid-cols-2 tablet:grid-cols-4 gap-2"
                      style={{ borderTop: '1px solid var(--line)', paddingTop: 10 }}
                    >
                      <div>
                        <label className="block text-xs mb-1" style={{ color: 'var(--ink-4)' }}>
                          Received
                        </label>
                        <input
                          type="number"
                          min={0}
                          value={l.received_qty}
                          onChange={(e) =>
                            applyLineEdit(idx, 'received_qty', parseInt(e.target.value) || 0)
                          }
                          className="input w-full"
                          aria-label={`Received qty for ${l.product_name}`}
                        />
                      </div>
                      <div>
                        <label className="block text-xs mb-1" style={{ color: 'var(--ink-4)' }}>
                          Rejected
                        </label>
                        <input
                          type="number"
                          min={0}
                          max={l.received_qty}
                          value={l.rejected_qty}
                          onChange={(e) =>
                            applyLineEdit(idx, 'rejected_qty', parseInt(e.target.value) || 0)
                          }
                          className="input w-full"
                          aria-label={`Rejected qty for ${l.product_name}`}
                        />
                      </div>
                      <div>
                        <label className="block text-xs mb-1" style={{ color: 'var(--ink-4)' }}>
                          Batch / lot
                        </label>
                        <input
                          type="text"
                          value={l.batch_code}
                          onChange={(e) => applyLineEdit(idx, 'batch_code', e.target.value)}
                          className="input w-full"
                          aria-label={`Batch / lot for ${l.product_name}`}
                        />
                      </div>
                      <div>
                        <label className="block text-xs mb-1" style={{ color: 'var(--ink-4)' }}>
                          Expiry
                        </label>
                        <input
                          type="date"
                          value={l.expiry_date}
                          onChange={(e) => applyLineEdit(idx, 'expiry_date', e.target.value)}
                          className="input w-full"
                          aria-label={`Expiry date for ${l.product_name}`}
                        />
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <button
                type="button"
                className="btn"
                onClick={() => setStage({ kind: 'steps', step: 1 })}
              >
                Back
              </button>
              <button
                type="button"
                className="btn accent"
                disabled={totalUnits === 0}
                onClick={() => setStage({ kind: 'steps', step: 3 })}
              >
                <CheckCircle2 className="w-4 h-4" />
                {anyTouched
                  ? `Continue with these quantities (${totalUnits} units)`
                  : 'Everything arrived as ordered'}
              </button>
            </div>
          </div>
        )}

        {/* STEP 3 — ONE CONFIRM */}
        {step === 3 && (
          <div className="card-body space-y-4">
            <div
              className="rounded-lg px-4 py-3 space-y-1"
              style={{ border: '1px solid var(--line)', background: 'var(--surface)' }}
            >
              <p className="text-sm" style={{ color: 'var(--ink)' }}>
                <FileText className="w-3.5 h-3.5 inline mr-1.5" style={{ verticalAlign: -2 }} />
                Invoice <span className="mono">{invoiceNo.trim()}</span>
                {invoiceDate && <> &middot; {invoiceDate}</>}
                {upload && <> &middot; {upload.filename}</>}
              </p>
              <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
                {includedLines.length} line{includedLines.length === 1 ? '' : 's'} &middot;{' '}
                {totalUnits} unit{totalUnits === 1 ? '' : 's'} — all accepted, nothing
                rejected.
              </p>
            </div>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <button
                type="button"
                className="btn"
                onClick={() => setStage({ kind: 'steps', step: 2 })}
                disabled={submitting}
              >
                Back
              </button>
              <button
                type="button"
                className="btn accent"
                onClick={handleConfirm}
                disabled={submitting}
              >
                {submitting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <CheckCircle2 className="w-4 h-4" />
                )}
                Put on shelf &amp; send to accounts
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default ExpressReceivePanel;
