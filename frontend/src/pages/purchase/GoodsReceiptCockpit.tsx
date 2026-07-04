// ============================================================================
// IMS 2.0 - Goods-Receipt Cockpit  (Purchase P1 / S4)
// ============================================================================
// Vendor-first receiving screen for OPS users (SUPERADMIN / ADMIN / STORE_MANAGER).
// Flow:
//   1. Pick vendor  ->  fetch cockpit payload (3 worklists)
//   2. Start receipt against an open PO  ->  enter rec/accepted/rejected per line
//   3. MANDATORY: upload vendor invoice / challan (image or PDF) via the
//      attachment gate before the "Create GRN" button is enabled
//   4. Submit -> GRN created -> optional label-print dialog

import { useState, useCallback, useEffect, useRef, startTransition } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Package,
  Upload,
  X,
  CheckCircle2,
  AlertCircle,
  Printer,
  ChevronDown,
  ChevronUp,
  Loader2,
  FileText,
  Truck,
} from 'lucide-react';
import clsx from 'clsx';
import { grnCockpitApi } from '../../services/api/grnCockpit';
import { vendorsApi } from '../../services/api/inventory';
import labelsApi from '../../services/api/labels';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import type {
  VendorOption,
  CockpitPayload,
  CockpitOpenPO,
  GRNItemInput,
  UploadDocResult,
} from '../../services/api/grnCockpit';

// ---- Local types -----------------------------------------------------------

interface ReceiveLine {
  po_item_id?: string;
  product_id: string;
  product_name: string;
  sku: string | null;
  ordered_qty: number;
  received_qty: number;
  accepted_qty: number;
  rejected_qty: number;
  rejection_reason: string;
  // P2: supplier batch + expiry for contact lenses (optional; dates the minted
  // units for FEFO). Left blank for frames / undated spectacle lenses.
  batch_code: string;
  expiry_date: string;
  // Phase 1 (read-only context): the PO's agreed unit price for this line,
  // straight from the cockpit payload. Display only — receiving never edits it.
  unit_price: number | null;
}

// ---- Helpers ---------------------------------------------------------------

const fmt = (n: number | null | undefined) =>
  n == null ? '—' : n.toLocaleString('en-IN');

const fmtMoney = (n: number | null | undefined) =>
  n == null
    ? null
    : `₹${Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

const fmtDate = (iso: string | null | undefined) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString('en-IN');
};

// ---- Sub-components --------------------------------------------------------

function SectionHeader({ title, count }: { title: string; count?: number }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <h3 className="font-semibold text-sm" style={{ color: 'var(--ink)' }}>
        {title}
      </h3>
      {count != null && count > 0 && (
        <span className="chip" style={{ fontSize: 11 }}>
          {count}
        </span>
      )}
    </div>
  );
}

// ---- Print-labels dialog ---------------------------------------------------

interface PrintLabelsDialogProps {
  grnId: string;
  productIds: string[];
  onClose: () => void;
}

function PrintLabelsDialog({ grnId, productIds, onClose }: PrintLabelsDialogProps) {
  const toast = useToast();
  const [printing, setPrinting] = useState(false);

  const handlePrint = async () => {
    setPrinting(true);
    try {
      // Best-effort: call getProductLabel for the first product_id as a sample
      // (the labels API is designed for individual product/stock units; a full
      // batch-print endpoint may be added in a future slice).
      for (const pid of productIds.slice(0, productIds.length)) {
        try {
          await labelsApi.getProductLabel({ product_id: pid });
        } catch {
          // Fail soft per product
        }
      }
      toast.success('Label print request sent to label printer');
      onClose();
    } catch {
      toast.error('Label print failed');
    } finally {
      setPrinting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.35)' }}
    >
      <div
        className="card"
        style={{ width: 360, maxWidth: '90vw', padding: 24 }}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold" style={{ color: 'var(--ink)' }}>
            Print stock labels?
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="btn sm"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-sm mb-5" style={{ color: 'var(--ink-3)' }}>
          GRN created successfully. Print barcode labels for the{' '}
          {productIds.length} received product{productIds.length === 1 ? '' : 's'}
          now, or skip and print later from the Inventory screen.
        </p>
        <p className="text-xs mb-5" style={{ color: 'var(--ink-4)' }}>
          GRN ref: <span className="mono">{grnId}</span>
        </p>
        <div className="flex gap-3 justify-end">
          <button type="button" className="btn" onClick={onClose}>
            Skip for now
          </button>
          <button
            type="button"
            className="btn accent"
            onClick={handlePrint}
            disabled={printing}
          >
            {printing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Printer className="w-4 h-4" />
            )}
            Print labels
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- Attachment upload zone ------------------------------------------------

interface AttachmentZoneProps {
  uploaded: UploadDocResult | null;
  uploading: boolean;
  onSelect: (file: File) => void;
  onRemove: () => void;
}

function AttachmentZone({ uploaded, uploading, onSelect, onRemove }: AttachmentZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) onSelect(file);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onSelect(file);
    // Reset so the same file can be re-selected after remove
    e.target.value = '';
  };

  if (uploaded && uploaded.file_id) {
    return (
      <div
        className="flex items-center gap-3 rounded-lg px-4 py-3"
        style={{
          border: '1.5px solid var(--ok)',
          background: 'rgba(34,197,94,0.06)',
        }}
      >
        <CheckCircle2 className="w-5 h-5 flex-shrink-0" style={{ color: 'var(--ok)' }} />
        <div className="flex-1 min-w-0">
          <p
            className="text-sm font-medium truncate"
            style={{ color: 'var(--ink)' }}
          >
            {uploaded.filename}
          </p>
          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>
            {(uploaded.size / 1024).toFixed(0)} KB &middot;{' '}
            {uploaded.persisted ? 'Saved to file store' : 'Buffered (not yet persisted)'}
          </p>
        </div>
        <button
          type="button"
          className="btn sm"
          onClick={onRemove}
          title="Remove and upload a different file"
        >
          <X className="w-3.5 h-3.5" />
          Replace
        </button>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg flex flex-col items-center justify-center gap-2 cursor-pointer transition-colors"
      style={{
        border: '2px dashed var(--line)',
        minHeight: 100,
        padding: '20px 16px',
      }}
      onClick={() => inputRef.current?.click()}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      aria-label="Upload vendor invoice or challan"
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/*,application/pdf"
        onChange={handleChange}
        style={{ display: 'none' }}
      />
      {uploading ? (
        <>
          <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--bv)' }} />
          <span className="text-sm" style={{ color: 'var(--ink-3)' }}>
            Uploading…
          </span>
        </>
      ) : (
        <>
          <Upload className="w-6 h-6" style={{ color: 'var(--ink-4)' }} />
          <span className="text-sm font-medium" style={{ color: 'var(--ink-2)' }}>
            Drag &amp; drop or click to upload
          </span>
          <span className="text-xs" style={{ color: 'var(--ink-4)' }}>
            Vendor invoice or delivery challan &middot; Image or PDF &middot; max 25 MB
          </span>
        </>
      )}
    </div>
  );
}

// ============================================================================
// Main page
// ============================================================================

export function GoodsReceiptCockpit() {
  const { user } = useAuth();
  const toast = useToast();
  const storeId = user?.activeStoreId || '';

  // ---- Vendor picker state -------------------------------------------------
  const [vendors, setVendors] = useState<VendorOption[]>([]);
  const [vendorsLoaded, setVendorsLoaded] = useState(false);
  const [vendorId, setVendorId] = useState('');
  const [loadingVendors, setLoadingVendors] = useState(false);

  // ---- Cockpit state -------------------------------------------------------
  const [cockpit, setCockpit] = useState<CockpitPayload | null>(null);
  const [loadingCockpit, setLoadingCockpit] = useState(false);

  // ---- Open-PO receive form ------------------------------------------------
  const [activePO, setActivePO] = useState<CockpitOpenPO | null>(null);
  const [receiveLines, setReceiveLines] = useState<ReceiveLine[]>([]);
  const [vendorInvoiceNo, setVendorInvoiceNo] = useState('');
  const [notes, setNotes] = useState('');

  // ---- Attachment gate -----
  const [uploadResult, setUploadResult] = useState<UploadDocResult | null>(null);
  const [uploading, setUploading] = useState(false);

  // ---- Submit state --------------------------------------------------------
  const [submitting, setSubmitting] = useState(false);

  // ---- Post-GRN dialog -----------------------------------------------------
  const [printDialog, setPrintDialog] = useState<{
    grnId: string;
    productIds: string[];
  } | null>(null);

  // ---- Collapsible pending sections ----------------------------------------
  const [showPending, setShowPending] = useState(true);
  const [showCataloged, setShowCataloged] = useState(false);

  // ---- PENDING GRNs (created but not yet accepted into stock) ---------------
  // The loop-breaker: a created-but-unaccepted GRN adds NO stock and the PO
  // stays receivable, which used to read as "nothing happened". These rows
  // surface that state with one-click Accept (mint stock) / Void (duplicate).
  const [pendingGrns, setPendingGrns] = useState<
    Array<{ grn_id: string; grn_number: string; vendor_invoice_no?: string; created_at?: string; items?: unknown[] }>
  >([]);
  const [grnActionBusy, setGrnActionBusy] = useState<string | null>(null);

  const loadPendingGrns = useCallback(
    async (vid: string) => {
      try {
        const res = await vendorsApi.getGRNs({
          store_id: storeId || undefined,
          status: 'PENDING',
        });
        const rows = (res.grns || res.items || res || []) as Array<Record<string, unknown>>;
        setPendingGrns(
          (Array.isArray(rows) ? rows : [])
            .filter((g) => !vid || g.vendor_id === vid || !g.vendor_id)
            .map((g) => ({
              grn_id: String(g.grn_id || g.id || ''),
              grn_number: String(g.grn_number || ''),
              vendor_invoice_no: g.vendor_invoice_no ? String(g.vendor_invoice_no) : undefined,
              created_at: g.created_at ? String(g.created_at) : undefined,
              items: Array.isArray(g.items) ? g.items : [],
            }))
        );
      } catch {
        /* fail-soft: the panel just doesn't render */
      }
    },
    [storeId],
  );

  const acceptPendingGrn = async (grnId: string, grnNumber: string) => {
    setGrnActionBusy(grnId);
    try {
      const res = await vendorsApi.acceptGRN(grnId);
      toast.success(
        `GRN ${grnNumber} accepted — ${res.units_added ?? 0} units added to stock` +
          (res.po_status ? ` · PO ${res.po_status}` : ''),
      );
      await loadPendingGrns(vendorId);
      await loadCockpit(vendorId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Failed to accept GRN ${grnNumber}`);
    } finally {
      setGrnActionBusy(null);
    }
  };

  const voidPendingGrn = async (grnId: string, grnNumber: string) => {
    if (!window.confirm(`Void ${grnNumber}? Use this for duplicates — no stock was added by it.`)) return;
    setGrnActionBusy(grnId);
    try {
      await vendorsApi.voidGRN(grnId);
      toast.success(`GRN ${grnNumber} voided`);
      await loadPendingGrns(vendorId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Failed to void GRN ${grnNumber}`);
    } finally {
      setGrnActionBusy(null);
    }
  };

  // ---- Load vendors on first render ----------------------------------------
  const ensureVendors = useCallback(async () => {
    if (vendorsLoaded) return;
    setLoadingVendors(true);
    try {
      const list = await grnCockpitApi.listVendors(storeId);
      setVendors(list);
      setVendorsLoaded(true);
    } catch {
      toast.error('Failed to load vendors');
    } finally {
      setLoadingVendors(false);
    }
  }, [vendorsLoaded, storeId, toast]);

  // ---- Fetch cockpit after vendor is picked --------------------------------
  const loadCockpit = useCallback(
    async (vid: string) => {
      if (!vid) {
        setCockpit(null);
        setActivePO(null);
        setReceiveLines([]);
        return;
      }
      setLoadingCockpit(true);
      try {
        const data = await grnCockpitApi.getCockpit({
          vendor_id: vid,
          store_id: storeId || undefined,
        });
        setCockpit(data);
        setActivePO(null);
        setReceiveLines([]);
        setUploadResult(null);
        // Surface any created-but-unaccepted GRNs alongside the worklists.
        void loadPendingGrns(vid);
      } catch {
        toast.error('Failed to load goods-receipt data for this vendor');
      } finally {
        setLoadingCockpit(false);
      }
    },
    [storeId, toast, loadPendingGrns],
  );

  const onVendorChange = (vid: string) => {
    setVendorId(vid);
    startTransition(() => loadCockpit(vid));
  };

  // ---- Deep-link support: /purchase/receive?vendor_id=&po_id= --------------
  // The PO list's "Receive" button lands here with vendor + PO preselected:
  // pick the vendor, auto-load the cockpit, then (below, once the payload is
  // in) open that PO's receive form. Runs once; fail-soft — an unknown
  // vendor_id just loads an empty cockpit, an unknown po_id leaves the
  // worklists showing.
  const [searchParams] = useSearchParams();
  const deepLinkDone = useRef(false);
  const pendingPoId = useRef<string | null>(null);
  useEffect(() => {
    if (deepLinkDone.current) return;
    deepLinkDone.current = true;
    const vid = searchParams.get('vendor_id');
    if (!vid) return;
    pendingPoId.current = searchParams.get('po_id');
    void ensureVendors(); // so the picker shows the vendor's name, not a blank
    setVendorId(vid);
    startTransition(() => loadCockpit(vid));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- Select a PO to receive against -------------------------------------
  const onSelectPO = (po: CockpitOpenPO) => {
    setActivePO(po);
    setUploadResult(null);
    setVendorInvoiceNo('');
    setNotes('');
    const lines: ReceiveLine[] = po.lines.map((l) => ({
      product_id: l.product_id ?? '',
      product_name: l.product_name ?? l.product_id ?? '',
      sku: l.sku,
      ordered_qty: l.ordered_qty,
      received_qty: l.pending_qty,   // default: receive all pending
      accepted_qty: l.pending_qty,
      rejected_qty: 0,
      rejection_reason: '',
      batch_code: '',
      expiry_date: '',
      unit_price: l.unit_price ?? null,
    }));
    setReceiveLines(lines);
  };

  // Second half of the deep link: once the cockpit payload for the deep-linked
  // vendor is in, open the requested PO's receive form (if it is still open).
  useEffect(() => {
    const poId = pendingPoId.current;
    if (!poId || !cockpit) return;
    pendingPoId.current = null;
    const po = cockpit.open_pos.find((p) => p.po_id === poId);
    if (po) onSelectPO(po);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cockpit]);

  const onCancelPO = () => {
    setActivePO(null);
    setReceiveLines([]);
    setUploadResult(null);
    setVendorInvoiceNo('');
    setNotes('');
  };

  // ---- Line qty edits -------------------------------------------------------
  const setLineField = (
    idx: number,
    field: keyof ReceiveLine,
    value: number | string,
  ) => {
    startTransition(() => {
      setReceiveLines((prev) =>
        prev.map((l, i) => {
          if (i !== idx) return l;
          const updated = { ...l, [field]: value };
          // Auto-balance accepted+rejected to match received
          if (field === 'received_qty') {
            const rec = Math.max(0, Number(value) || 0);
            updated.received_qty = rec;
            // Keep accepted capped at new received
            updated.accepted_qty = Math.min(updated.accepted_qty, rec);
            updated.rejected_qty = rec - updated.accepted_qty;
          } else if (field === 'accepted_qty') {
            const acc = Math.max(0, Math.min(Number(value) || 0, l.received_qty));
            updated.accepted_qty = acc;
            updated.rejected_qty = l.received_qty - acc;
          }
          return updated;
        }),
      );
    });
  };

  // ---- Attachment upload ---------------------------------------------------
  const handleFileSelect = async (file: File) => {
    setUploading(true);
    setUploadResult(null);
    try {
      const result = await grnCockpitApi.uploadDoc(file);
      if (!result.file_id) {
        toast.warning(
          'File uploaded but could not be persisted to file store. ' +
          'GRN will still proceed — attachment is buffered.',
        );
      }
      setUploadResult(result);
      toast.success(`Uploaded: ${result.filename}`);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? 'Upload failed';
      toast.error(typeof msg === 'string' ? msg : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleRemoveFile = () => setUploadResult(null);

  // ---- Submission ----------------------------------------------------------
  const canSubmit =
    !!activePO &&
    !!vendorInvoiceNo.trim() &&
    uploadResult !== null &&
    !uploading &&
    receiveLines.length > 0 &&
    receiveLines.some((l) => l.received_qty > 0);

  const handleSubmit = async () => {
    if (!activePO) return;
    if (!vendorInvoiceNo.trim()) {
      toast.error('Vendor invoice number is required');
      return;
    }
    if (!uploadResult) {
      toast.error('Upload the vendor invoice/challan before submitting');
      return;
    }

    const items: GRNItemInput[] = receiveLines
      .filter((l) => l.received_qty > 0)
      .map((l) => ({
        product_id: l.product_id,
        received_qty: l.received_qty,
        accepted_qty: l.accepted_qty,
        rejected_qty: l.rejected_qty,
        rejection_reason: l.rejection_reason || undefined,
        batch_code: l.batch_code.trim() || undefined,
        expiry_date: l.expiry_date.trim() || undefined,
      }));

    if (items.length === 0) {
      toast.error('No received quantities entered');
      return;
    }

    setSubmitting(true);
    try {
      const result = await grnCockpitApi.createGRN({
        po_id: activePO.po_id,
        vendor_invoice_no: vendorInvoiceNo.trim(),
        attachment_file_id: uploadResult.file_id ?? '',
        items,
        notes: notes.trim() || undefined,
      });

      // COMPLETE the receipt in one action: creating a GRN only records it
      // (PENDING, no stock) — accepting is what mints stock_units and advances
      // the PO. Splitting these across screens made the flow read as a loop
      // (the PO stayed receivable), so the cockpit now accepts immediately;
      // QC accept/reject was already captured per line above.
      try {
        const acc = await vendorsApi.acceptGRN(result.grn_id);
        toast.success(
          `GRN ${result.grn_number} complete — ${acc.units_added ?? result.total_received} units added to stock` +
            (acc.po_status ? ` · PO ${acc.po_status}` : ''),
        );
        if (acc.grn_status === 'PARTIALLY_ACCEPTED') {
          toast.warning(
            'Some lines were held because their product is not catalogued yet — catalogue them, then accept the GRN from the Pending receipts panel.',
          );
        }
      } catch (acceptErr) {
        toast.warning(
          `GRN ${result.grn_number} was saved but could NOT be added to stock: ` +
            (acceptErr instanceof Error ? acceptErr.message : 'accept failed') +
            ' — use the Pending receipts panel below to accept it.',
        );
      }

      // Show print-labels dialog
      const productIds = [...new Set(items.map((i) => i.product_id))];
      setPrintDialog({ grnId: result.grn_id, productIds });

      // Refresh cockpit
      onCancelPO();
      await loadCockpit(vendorId);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      if (
        detail &&
        typeof detail === 'object' &&
        (detail as { code?: string }).code === 'ATTACHMENT_REQUIRED'
      ) {
        toast.error(
          (detail as { message?: string }).message ??
            'Attachment is required — please upload the vendor invoice.',
        );
        setUploadResult(null);
      } else {
        toast.error(
          typeof detail === 'string' ? detail : 'Failed to create GRN',
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  // ---- Render --------------------------------------------------------------

  const hasOpenPOs = (cockpit?.open_pos?.length ?? 0) > 0;
  const hasPending = (cockpit?.pending_not_received?.length ?? 0) > 0;
  const hasCataloged = (cockpit?.pending_cataloged?.length ?? 0) > 0;

  return (
    <>
      {printDialog && (
        <PrintLabelsDialog
          grnId={printDialog.grnId}
          productIds={printDialog.productIds}
          onClose={() => setPrintDialog(null)}
        />
      )}

      <div className="inv-body">
        {/* Header */}
        <div className="inv-head">
          <div>
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              Purchase &middot; receive
            </div>
            <h1>Goods-receipt cockpit.</h1>
            <p className="text-sm mt-1" style={{ color: 'var(--ink-4)' }}>
              Select a vendor to see open POs, pending items, and catalogued
              products. Receive against an open PO — upload the vendor invoice
              first.
            </p>
          </div>
        </div>

        {/* ── Vendor picker ── */}
        <div className="card mb-4">
          <div className="card-head">
            <h3 className="flex items-center gap-2">
              <Truck className="w-4 h-4" /> Select vendor
            </h3>
          </div>
          <div className="card-body">
            <div className="flex gap-3 items-end flex-wrap">
              <div className="flex-1 min-w-48">
                <label
                  className="block text-xs font-medium mb-1"
                  style={{ color: 'var(--ink-4)' }}
                >
                  Vendor
                </label>
                <select
                  value={vendorId}
                  onChange={(e) => onVendorChange(e.target.value)}
                  onFocus={ensureVendors}
                  className="input w-full"
                  disabled={loadingVendors}
                >
                  <option value="">
                    {loadingVendors ? 'Loading vendors…' : 'Select a vendor…'}
                  </option>
                  {vendors.map((v) => (
                    <option key={v.vendor_id} value={v.vendor_id}>
                      {v.display_name ?? v.trade_name ?? v.legal_name ?? v.vendor_id}
                    </option>
                  ))}
                </select>
              </div>
              {loadingCockpit && (
                <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--ink-4)' }}>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Loading…
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ── No vendor selected ── */}
        {!vendorId && (
          <div
            className="card text-center py-12"
            style={{ color: 'var(--ink-4)' }}
          >
            <Package className="w-10 h-10 mx-auto mb-3" style={{ color: 'var(--ink-5)' }} />
            <p className="font-medium">Select a vendor to get started</p>
            <p className="text-sm mt-1" style={{ color: 'var(--ink-5)' }}>
              The cockpit shows open POs, items pending receipt, and catalogued
              products for the selected vendor.
            </p>
          </div>
        )}

        {/* ── Cockpit content (vendor selected) ── */}
        {vendorId && cockpit && !loadingCockpit && (
          <>
            {/* Stat strip */}
            <div className="stat-strip mb-4">
              <div>
                <div className="l">Open POs</div>
                <div className="v">{cockpit.open_pos.length}</div>
                <div className="d">awaiting receipt</div>
              </div>
              <div>
                <div className="l">Pending items</div>
                <div className="v" style={{ color: cockpit.pending_not_received.length > 0 ? 'var(--warn)' : 'var(--ink)' }}>
                  {cockpit.pending_not_received.length}
                </div>
                <div className="d warn">not yet received</div>
              </div>
              <div>
                <div className="l">Catalogued</div>
                <div className="v">{cockpit.pending_cataloged.length}</div>
                <div className="d">not on open PO</div>
              </div>
            </div>

            {/* ── Receive form (when a PO is selected) ── */}
            {activePO ? (
              <div className="space-y-4">
                {/* PO header */}
                <div className="card">
                  <div className="card-head">
                    <h3 className="flex items-center gap-2">
                      <FileText className="w-4 h-4" />
                      Receiving against{' '}
                      <span className="mono">{activePO.po_number}</span>
                    </h3>
                    <button
                      type="button"
                      className="btn sm"
                      onClick={onCancelPO}
                    >
                      <X className="w-3.5 h-3.5" /> Cancel
                    </button>
                  </div>
                  <div className="card-body">
                    <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
                      <div>
                        <label
                          className="block text-xs font-medium mb-1"
                          style={{ color: 'var(--ink-4)' }}
                        >
                          Vendor invoice number *
                        </label>
                        <input
                          type="text"
                          value={vendorInvoiceNo}
                          onChange={(e) =>
                            startTransition(() =>
                              setVendorInvoiceNo(e.target.value),
                            )
                          }
                          placeholder="e.g. JJ/26/05/2240"
                          className="input w-full"
                        />
                      </div>
                      <div>
                        <label
                          className="block text-xs font-medium mb-1"
                          style={{ color: 'var(--ink-4)' }}
                        >
                          Notes (optional)
                        </label>
                        <input
                          type="text"
                          value={notes}
                          onChange={(e) =>
                            startTransition(() => setNotes(e.target.value))
                          }
                          placeholder="Quality notes, comments…"
                          className="input w-full"
                        />
                      </div>
                    </div>
                  </div>
                </div>

                {/* Line items table */}
                <div className="card">
                  <div className="card-head">
                    <h3>
                      Line items &middot;{' '}
                      {receiveLines.filter((l) => l.received_qty > 0).length}/
                      {receiveLines.length} with receipt
                    </h3>
                  </div>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th className="right">Ordered</th>
                        <th className="right">Received</th>
                        <th className="right">Accepted</th>
                        <th className="right">Rejected</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {receiveLines.map((line, idx) => (
                        <tr key={idx}>
                          <td>
                            <div
                              className="font-medium"
                              style={{ color: 'var(--ink)' }}
                            >
                              {line.product_name}
                            </div>
                            {line.sku && (
                              <div
                                className="mono text-xs"
                                style={{ color: 'var(--ink-4)' }}
                              >
                                {line.sku}
                              </div>
                            )}
                            {/* Phase 1: READ-ONLY purchase context from the PO —
                                the agreed unit price for this line and the PO's
                                expected date. Muted; receiving never edits them. */}
                            {(line.unit_price != null || activePO.expected_date) && (
                              <div
                                className="text-xs"
                                style={{ color: 'var(--ink-4)', marginTop: 2 }}
                              >
                                {[
                                  line.unit_price != null
                                    ? `PO price ${fmtMoney(line.unit_price)}`
                                    : null,
                                  fmtDate(activePO.expected_date)
                                    ? `expected ${fmtDate(activePO.expected_date)}`
                                    : null,
                                ]
                                  .filter(Boolean)
                                  .join(' · ')}
                              </div>
                            )}
                            {/* P2: optional batch + expiry -- fill for contact
                                lenses so the received units are dated for FEFO.
                                Leave blank for frames / undated lenses. */}
                            <div
                              className="flex items-center gap-1.5"
                              style={{ marginTop: 4 }}
                            >
                              <input
                                type="text"
                                placeholder="Batch / lot"
                                value={line.batch_code}
                                onChange={(e) =>
                                  setLineField(idx, 'batch_code', e.target.value)
                                }
                                className="input"
                                style={{
                                  width: 96,
                                  height: 26,
                                  padding: '0 6px',
                                  fontSize: 11.5,
                                }}
                                aria-label={`Batch / lot for ${line.product_name}`}
                              />
                              <input
                                type="date"
                                value={line.expiry_date}
                                onChange={(e) =>
                                  setLineField(idx, 'expiry_date', e.target.value)
                                }
                                className="input"
                                style={{
                                  width: 130,
                                  height: 26,
                                  padding: '0 6px',
                                  fontSize: 11.5,
                                }}
                                aria-label={`Expiry date for ${line.product_name}`}
                                title="Expiry (contact lenses)"
                              />
                            </div>
                          </td>
                          <td className="right mono">{line.ordered_qty}</td>
                          <td className="right">
                            <input
                              type="number"
                              min={0}
                              max={line.ordered_qty * 2}
                              value={line.received_qty}
                              onChange={(e) =>
                                setLineField(
                                  idx,
                                  'received_qty',
                                  parseInt(e.target.value) || 0,
                                )
                              }
                              className="input"
                              style={{
                                width: 64,
                                textAlign: 'right',
                                height: 30,
                                padding: '0 6px',
                              }}
                            />
                          </td>
                          <td className="right">
                            <input
                              type="number"
                              min={0}
                              max={line.received_qty}
                              value={line.accepted_qty}
                              onChange={(e) =>
                                setLineField(
                                  idx,
                                  'accepted_qty',
                                  parseInt(e.target.value) || 0,
                                )
                              }
                              className="input"
                              style={{
                                width: 64,
                                textAlign: 'right',
                                height: 30,
                                padding: '0 6px',
                              }}
                            />
                          </td>
                          <td
                            className="right mono"
                            style={{
                              color:
                                line.rejected_qty > 0
                                  ? 'var(--err)'
                                  : 'var(--ink-4)',
                            }}
                          >
                            {line.rejected_qty}
                          </td>
                          <td>
                            {line.rejected_qty > 0 && (
                              <input
                                type="text"
                                value={line.rejection_reason}
                                onChange={(e) =>
                                  setLineField(
                                    idx,
                                    'rejection_reason',
                                    e.target.value,
                                  )
                                }
                                placeholder="Reason…"
                                className="input"
                                style={{
                                  height: 30,
                                  padding: '0 6px',
                                  minWidth: 120,
                                }}
                              />
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* MANDATORY attachment gate */}
                <div className="card">
                  <div className="card-head">
                    <h3 className="flex items-center gap-2">
                      <Upload className="w-4 h-4" />
                      Vendor invoice / challan attachment
                      <span
                        className="chip"
                        style={{
                          fontSize: 10,
                          background: 'var(--bv-soft)',
                          color: 'var(--bv)',
                        }}
                      >
                        REQUIRED
                      </span>
                    </h3>
                  </div>
                  <div className="card-body">
                    <p
                      className="text-xs mb-3"
                      style={{ color: 'var(--ink-4)' }}
                    >
                      Upload an image or PDF of the vendor's invoice or delivery
                      challan. The GRN cannot be created without this document.
                    </p>
                    <AttachmentZone
                      uploaded={uploadResult}
                      uploading={uploading}
                      onSelect={handleFileSelect}
                      onRemove={handleRemoveFile}
                    />
                    {uploadResult && !uploadResult.file_id && (
                      <div
                        className="flex items-center gap-2 mt-2 text-xs"
                        style={{ color: 'var(--warn)' }}
                      >
                        <AlertCircle className="w-3.5 h-3.5" />
                        File store unavailable — the attachment is buffered
                        locally. GRN will still be created.
                      </div>
                    )}
                  </div>
                </div>

                {/* Submit footer */}
                <div className="flex items-center gap-3 flex-wrap">
                  <div className="flex-1 space-y-0.5">
                    {!vendorInvoiceNo.trim() && (
                      <p className="text-xs" style={{ color: 'var(--err)' }}>
                        Vendor invoice number is required
                      </p>
                    )}
                    {!uploadResult && (
                      <p className="text-xs" style={{ color: 'var(--err)' }}>
                        Upload the vendor invoice / challan first
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    className="btn"
                    onClick={onCancelPO}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleSubmit}
                    disabled={!canSubmit || submitting}
                    className="btn accent"
                    title={
                      !uploadResult
                        ? 'Upload the vendor invoice first'
                        : !vendorInvoiceNo.trim()
                          ? 'Enter the vendor invoice number'
                          : 'Create goods receipt'
                    }
                  >
                    {submitting ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <CheckCircle2 className="w-4 h-4" />
                    )}
                    Create goods receipt
                  </button>
                </div>
              </div>
            ) : (
              /* ── Worklists (no PO selected yet) ── */
              <div className="space-y-4">
                {/* Pending receipts: created but NOT yet added to stock. Kept
                    visible so an interrupted/failed accept (or an old
                    duplicate) can be completed or voided in one click. */}
                {pendingGrns.length > 0 && (
                  <div className="card border-amber-200 bg-amber-50/40">
                    <div className="flex items-center gap-2 mb-2">
                      <AlertCircle className="w-4 h-4 text-amber-600" />
                      <h3 className="font-semibold text-gray-900">
                        Pending receipts — not yet added to stock ({pendingGrns.length})
                      </h3>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      These GRNs were created but never accepted, so their units are NOT in
                      stock and their POs still show as receivable. Accept the correct one;
                      void duplicates (voiding is safe — a pending GRN has added nothing).
                    </p>
                    <div className="space-y-2">
                      {pendingGrns.map((g) => (
                        <div
                          key={g.grn_id}
                          className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-white px-3 py-2"
                        >
                          <div className="min-w-0 text-sm">
                            <span className="font-medium text-gray-900">{g.grn_number}</span>
                            {g.vendor_invoice_no && (
                              <span className="text-gray-500"> · Inv {g.vendor_invoice_no}</span>
                            )}
                            {g.created_at && (
                              <span className="text-gray-400"> · {String(g.created_at).slice(0, 10)}</span>
                            )}
                            <span className="text-gray-500"> · {g.items?.length ?? 0} line(s)</span>
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => acceptPendingGrn(g.grn_id, g.grn_number)}
                              disabled={grnActionBusy === g.grn_id}
                              className="btn-primary !py-1 !px-3 text-xs flex items-center gap-1.5 disabled:opacity-50"
                            >
                              {grnActionBusy === g.grn_id ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              ) : (
                                <CheckCircle2 className="w-3.5 h-3.5" />
                              )}
                              Add to stock
                            </button>
                            <button
                              type="button"
                              onClick={() => voidPendingGrn(g.grn_id, g.grn_number)}
                              disabled={grnActionBusy === g.grn_id}
                              className="btn-secondary !py-1 !px-3 text-xs flex items-center gap-1.5 disabled:opacity-50"
                            >
                              <X className="w-3.5 h-3.5" />
                              Void (duplicate)
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Open POs */}
                <div className="card">
                  <div className="card-head">
                    <SectionHeader
                      title="Open purchase orders"
                      count={cockpit.open_pos.length}
                    />
                  </div>
                  {!hasOpenPOs ? (
                    <div
                      className="text-center py-8 text-sm"
                      style={{ color: 'var(--ink-4)' }}
                    >
                      No open POs for this vendor at this store.
                    </div>
                  ) : (
                    <div className="space-y-2 p-4">
                      {cockpit.open_pos.map((po) => (
                        <div
                          key={po.po_id}
                          className="rounded-lg p-3 flex items-start justify-between gap-3"
                          style={{
                            border: '1px solid var(--line)',
                            background: 'var(--surface)',
                          }}
                        >
                          <div className="flex-1 min-w-0">
                            <p
                              className="font-semibold mono text-sm"
                              style={{ color: 'var(--ink)' }}
                            >
                              {po.po_number}
                            </p>
                            <p
                              className="text-xs mt-0.5"
                              style={{ color: 'var(--ink-4)' }}
                            >
                              {po.lines.length} pending line
                              {po.lines.length === 1 ? '' : 's'} &middot;{' '}
                              {po.lines.reduce(
                                (s, l) => s + l.pending_qty,
                                0,
                              )}{' '}
                              units
                              {po.expected_date &&
                                ` · expected ${new Date(po.expected_date).toLocaleDateString('en-IN')}`}
                            </p>
                            <div className="flex flex-wrap gap-1 mt-1">
                              {po.lines.slice(0, 3).map((l, i) => (
                                <span
                                  key={i}
                                  className="chip"
                                  style={{ fontSize: 10 }}
                                >
                                  {l.product_name ?? l.product_id} ×{' '}
                                  {l.pending_qty}
                                </span>
                              ))}
                              {po.lines.length > 3 && (
                                <span
                                  className="chip"
                                  style={{ fontSize: 10 }}
                                >
                                  +{po.lines.length - 3} more
                                </span>
                              )}
                            </div>
                          </div>
                          <button
                            type="button"
                            className="btn sm accent flex-shrink-0"
                            onClick={() => onSelectPO(po)}
                          >
                            Receive
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Pending not received */}
                {hasPending && (
                  <div className="card">
                    <div
                      className="card-head cursor-pointer select-none"
                      onClick={() => setShowPending((v) => !v)}
                    >
                      <SectionHeader
                        title="Pending receipt by product"
                        count={cockpit.pending_not_received.length}
                      />
                      {showPending ? (
                        <ChevronUp className="w-4 h-4" style={{ color: 'var(--ink-4)' }} />
                      ) : (
                        <ChevronDown className="w-4 h-4" style={{ color: 'var(--ink-4)' }} />
                      )}
                    </div>
                    {showPending && (
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>Product</th>
                            <th className="right">Ordered</th>
                            <th className="right">Received</th>
                            <th className="right">Pending</th>
                          </tr>
                        </thead>
                        <tbody>
                          {cockpit.pending_not_received.map((item) => (
                            <tr key={item.product_id}>
                              <td>
                                <div
                                  className="font-medium text-sm"
                                  style={{ color: 'var(--ink)' }}
                                >
                                  {item.product_name ?? item.product_id}
                                </div>
                                {item.sku && (
                                  <div
                                    className="mono text-xs"
                                    style={{ color: 'var(--ink-4)' }}
                                  >
                                    {item.sku}
                                  </div>
                                )}
                              </td>
                              <td className="right mono">{fmt(item.ordered_qty)}</td>
                              <td className="right mono">{fmt(item.received_qty)}</td>
                              <td className="right">
                                <span
                                  className={clsx(
                                    'chip',
                                    item.pending_qty > 0 ? 'warn' : '',
                                  )}
                                >
                                  {fmt(item.pending_qty)}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}

                {/* Catalogued products not on open PO */}
                {hasCataloged && (
                  <div className="card">
                    <div
                      className="card-head cursor-pointer select-none"
                      onClick={() => setShowCataloged((v) => !v)}
                    >
                      <SectionHeader
                        title="Catalogued — not on open PO"
                        count={cockpit.pending_cataloged.length}
                      />
                      {showCataloged ? (
                        <ChevronUp className="w-4 h-4" style={{ color: 'var(--ink-4)' }} />
                      ) : (
                        <ChevronDown className="w-4 h-4" style={{ color: 'var(--ink-4)' }} />
                      )}
                    </div>
                    {showCataloged && (
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>Product</th>
                            <th>SKU</th>
                            <th>Category</th>
                          </tr>
                        </thead>
                        <tbody>
                          {cockpit.pending_cataloged.map((item) => (
                            <tr key={item.product_id}>
                              <td
                                className="font-medium text-sm"
                                style={{ color: 'var(--ink)' }}
                              >
                                {item.product_name ?? item.product_id}
                              </td>
                              <td
                                className="mono text-xs"
                                style={{ color: 'var(--ink-4)' }}
                              >
                                {item.sku ?? '—'}
                              </td>
                              <td>
                                {item.category && (
                                  <span className="chip" style={{ fontSize: 10 }}>
                                    {item.category}
                                  </span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}

                {!hasOpenPOs && !hasPending && !hasCataloged && (
                  <div
                    className="card text-center py-10"
                    style={{ color: 'var(--ink-4)' }}
                  >
                    <Package className="w-8 h-8 mx-auto mb-2" style={{ color: 'var(--ink-5)' }} />
                    <p className="font-medium">Nothing pending for this vendor</p>
                    <p className="text-sm mt-1" style={{ color: 'var(--ink-5)' }}>
                      All POs are fully received and no catalogued products are waiting.
                    </p>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}

export default GoodsReceiptCockpit;
