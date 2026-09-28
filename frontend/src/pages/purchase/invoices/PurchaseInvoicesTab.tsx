// ============================================================================
// IMS 2.0 - Purchase Invoices (first-class AP + ITC document)
// ============================================================================
// The supplier's tax invoice booked into Accounts Payable. Unlike the old
// header-only "bill" (a 3-field amount form on Finance -> Cash-Flow that
// dropped the PO/GRN link), this carries:
//   - line items with HSN + per-rate GST
//   - an explicit place_of_supply, so tax is split correctly:
//       intra-state -> CGST + SGST     inter-state -> IGST
//
// Writing place_of_supply here is the fix for the long-standing bug where the
// ITC code READ place_of_supply but nothing ever WROTE it, so every
// inter-state purchase was mis-booked CGST+SGST instead of IGST.
//
// Two entry paths, one form:
//   1. Create from GRN  -> server prefills a draft from the ACCEPTED GRN + PO.
//   2. Manual invoice   -> blank form (no GRN link).
// The user reviews / edits, then Books it (POST).

import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Plus, Loader2, FileText, PackageCheck, AlertTriangle, RefreshCw } from 'lucide-react';
import {
  purchaseInvoicesApi,
  type PurchaseInvoice,
  type PurchaseInvoiceLine,
  type PurchaseInvoiceConfig,
} from '../../../services/api/vendorAp';
import { useToast } from '../../../context/ToastContext';
import { useAuth } from '../../../context/AuthContext';
import type { Supplier } from '../purchaseTypes';
import { inr, errMsg } from './shared';
import { ExceptionsPanel } from './ExceptionsPanel';
import { GrnPickerModal, DcPickerModal } from './pickers';
import { InvoiceFormDrawer, blankLine, type EditLine } from './InvoiceFormDrawer';
import { InvoiceDetailDrawer, MatchBadge, ConfigNote } from './InvoiceDetailDrawer';

// ============================================================================
// Tab root: list + GRN picker + invoice form
// ============================================================================
export function PurchaseInvoicesTab({ suppliers }: { suppliers: Supplier[] }) {
  const { user } = useAuth();
  const [invoices, setInvoices] = useState<PurchaseInvoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pickingGrn, setPickingGrn] = useState(false);
  // F9 — "Match DCs to Invoice": pick open Delivery Challans -> draft a
  // consolidated invoice that flips dc_matched on each DC when booked.
  const [pickingDcs, setPickingDcs] = useState(false);
  // form: either prefilled from a GRN draft or a fresh manual invoice
  const [form, setForm] = useState<{ prefill: Partial<PurchaseInvoice>; lines: EditLine[] } | null>(null);
  // Phase 2: the invoice whose 3-way-match detail drawer is open, + the active
  // match/valuation settings (loaded once; null when the backend has none).
  const [detailInvoice, setDetailInvoice] = useState<PurchaseInvoice | null>(null);
  const [config, setConfig] = useState<PurchaseInvoiceConfig | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const storeId = user?.activeStoreId;
      const res = await purchaseInvoicesApi.list(storeId ? { store_id: storeId } : {});
      setInvoices(res.purchase_invoices);
    } catch (e) {
      setError(errMsg(e, 'Failed to load purchase invoices'));
    } finally {
      setLoading(false);
    }
  }, [user?.activeStoreId]);

  useEffect(() => { load(); }, [load]);

  // Best-effort: fetch the active valuation method + tolerance once for the
  // read-only note. Never blocks the tab (getConfig is fail-soft -> null).
  useEffect(() => {
    let alive = true;
    purchaseInvoicesApi.getConfig().then((c) => { if (alive) setConfig(c); });
    return () => { alive = false; };
  }, []);

  const openManual = () => setForm({ prefill: {}, lines: [blankLine()] });

  // Deep-link auto-open: /purchase?tab=purchase-invoices&grn_id=<id> (the
  // /purchase/invoices/book redirect used by the express-receive accountant
  // task and the PO timeline drawer). Fetch the from-GRN draft ONCE and open
  // the booking form prefilled; clear the param so refresh/back doesn't
  // re-open. Fail-soft: a blocked draft (e.g. GRN not ACCEPTED) surfaces the
  // server's message as a toast and leaves the tab usable.
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const autoOpenRanRef = useRef(false);
  useEffect(() => {
    const grnId = searchParams.get('grn_id');
    if (!grnId || autoOpenRanRef.current) return;
    autoOpenRanRef.current = true;
    (async () => {
      try {
        const draft = await purchaseInvoicesApi.createFromGrn(grnId);
        openFromGrnDraft(
          {
            vendor_id: draft.vendor_id,
            vendor_name: draft.vendor_name,
            vendor_invoice_no: draft.vendor_invoice_no,
            vendor_invoice_date: draft.vendor_invoice_date,
            po_id: draft.po_id,
            po_number: draft.po_number,
            grn_id: draft.grn_id ?? grnId,
            grn_number: draft.grn_number,
            place_of_supply: draft.place_of_supply,
            recipient_gstin: draft.recipient_gstin,
            store_id: draft.store_id,
          },
          draft.lines ?? [],
        );
      } catch (e) {
        toast.error(errMsg(e, 'Could not open the invoice draft for this receipt'));
      } finally {
        // Drop grn_id but keep the tab param so the URL stays truthful.
        const next = new URLSearchParams(searchParams);
        next.delete('grn_id');
        setSearchParams(next, { replace: true });
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const openFromGrnDraft = (prefill: Partial<PurchaseInvoice>, lines: PurchaseInvoiceLine[]) => {
    setPickingGrn(false);
    setForm({
      prefill,
      lines: (lines.length ? lines : [{} as PurchaseInvoiceLine]).map((l) => ({
        product_id: l.product_id,
        product_name: l.product_name ?? '',
        sku: l.sku ?? '',
        hsn_code: l.hsn_code ?? '',
        quantity: String(l.quantity ?? 1),
        unit_price: String(l.unit_price ?? 0),
        gst_rate: String(l.gst_rate ?? 5),
      })),
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-1">
          <p className="text-sm text-gray-500">
            Supplier tax invoices booked to Accounts Payable &amp; the ITC register, with line-level HSN + GST.
          </p>
          <ConfigNote config={config} />
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={load} className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          <button type="button" onClick={() => setPickingGrn(true)} className="btn sm">
            <PackageCheck className="w-4 h-4" /> Create from GRN
          </button>
          <button type="button" onClick={() => setPickingDcs(true)} className="btn sm">
            <PackageCheck className="w-4 h-4" /> Match DCs to Invoice
          </button>
          <button type="button" onClick={openManual} className="btn sm primary">
            <Plus className="w-4 h-4" /> Manual invoice
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2">
          <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-900">Failed to load purchase invoices</p>
            <p className="text-xs text-red-700 mt-1">{error}</p>
          </div>
          <button type="button" onClick={load} className="text-xs font-medium text-red-700 hover:text-red-900 underline">Retry</button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
        </div>
      ) : (
        <>
          {/* P3: Variance-Approval panel — surfaces ON_HOLD_EXCEPTION invoices
              above the full list so the accountant can act without hunting. */}
          <ExceptionsPanel
            invoices={invoices}
            onApproved={(invoiceId, updated) => {
              setInvoices((prev) =>
                prev.map((p) =>
                  p.purchase_invoice_id === invoiceId ? { ...p, ...updated } : p,
                ),
              );
            }}
            onViewDetail={setDetailInvoice}
          />
          <InvoiceList invoices={invoices} onOpen={setDetailInvoice} />
        </>
      )}

      {pickingGrn && (
        <GrnPickerModal
          onClose={() => setPickingGrn(false)}
          onPicked={openFromGrnDraft}
        />
      )}

      {pickingDcs && (
        <DcPickerModal
          suppliers={suppliers}
          onClose={() => setPickingDcs(false)}
          onPicked={(prefill, lines) => {
            setPickingDcs(false);
            openFromGrnDraft(prefill, lines);
          }}
        />
      )}

      {form && (
        <InvoiceFormDrawer
          suppliers={suppliers}
          prefill={form.prefill}
          initialLines={form.lines}
          onClose={() => setForm(null)}
          onBooked={() => { setForm(null); load(); }}
        />
      )}

      {detailInvoice && (
        <InvoiceDetailDrawer
          invoice={detailInvoice}
          config={config}
          onClose={() => setDetailInvoice(null)}
          // After an override is approved the verdict changes -> refresh the list
          // so the badge updates, but keep the drawer open on the fresh detail.
          onChanged={(updated) => {
            setInvoices((prev) => prev.map((p) =>
              p.purchase_invoice_id === updated.purchase_invoice_id ? { ...p, ...updated } : p));
            setDetailInvoice((cur) => (cur ? { ...cur, ...updated } : cur));
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// List of booked / draft purchase invoices
// ============================================================================
function InvoiceList({ invoices, onOpen }: { invoices: PurchaseInvoice[]; onOpen: (pi: PurchaseInvoice) => void }) {
  if (invoices.length === 0) {
    return (
      <div className="text-center py-12 bg-white border border-gray-200 rounded-lg">
        <FileText className="w-12 h-12 text-gray-400 mx-auto mb-3" />
        <p className="text-gray-700 font-medium">No purchase invoices yet</p>
        <p className="text-sm text-gray-500 mt-1">
          Use <span className="font-medium">Create from GRN</span> to book a received goods receipt as a tax invoice, or add one manually.
        </p>
      </div>
    );
  }
  // Whether ANY invoice carries a match verdict -> only then show the Match
  // column (a Phase-1 backend without the match engine hides it entirely).
  const anyMatch = invoices.some((pi) => Boolean(pi.match_status));
  return (
    <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-500 text-xs">
          <tr>
            <th className="text-left px-3 py-2">Supplier / Invoice</th>
            <th className="text-left px-3 py-2">Date</th>
            <th className="text-left px-3 py-2">Refs</th>
            <th className="text-center px-3 py-2">Tax type</th>
            <th className="text-right px-3 py-2">Taxable</th>
            <th className="text-right px-3 py-2">CGST</th>
            <th className="text-right px-3 py-2">SGST</th>
            <th className="text-right px-3 py-2">IGST</th>
            <th className="text-right px-3 py-2">Total</th>
            {anyMatch && <th className="text-center px-3 py-2">3-way match</th>}
            <th className="text-center px-3 py-2">Status</th>
            <th className="px-2 py-2"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {invoices.map((pi) => {
            const inter = pi.is_interstate ?? (pi.igst || 0) > 0;
            return (
              <tr
                key={pi.purchase_invoice_id}
                className="hover:bg-gray-50 cursor-pointer"
                onClick={() => onOpen(pi)}
                title="View 3-way match detail"
              >
                <td className="px-3 py-2">
                  <div className="font-medium text-gray-900">{pi.vendor_name || pi.vendor_id}</div>
                  <div className="text-xs text-gray-500">{pi.vendor_invoice_no}</div>
                </td>
                <td className="px-3 py-2 text-gray-700">{(pi.vendor_invoice_date || '').slice(0, 10)}</td>
                <td className="px-3 py-2 text-xs text-gray-500">
                  {pi.po_number && <div>PO {pi.po_number}</div>}
                  {pi.grn_number && <div>GRN {pi.grn_number}</div>}
                  {!pi.po_number && !pi.grn_number && <span className="text-gray-400">Manual</span>}
                </td>
                <td className="px-3 py-2 text-center">
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${inter ? 'bg-purple-100 text-purple-800' : 'bg-blue-100 text-blue-800'}`}>
                    {inter ? 'IGST' : 'CGST+SGST'}
                  </span>
                </td>
                <td className="px-3 py-2 text-right text-gray-700">{inr(pi.taxable_amount)}</td>
                <td className="px-3 py-2 text-right text-gray-500">{pi.cgst ? inr(pi.cgst) : '-'}</td>
                <td className="px-3 py-2 text-right text-gray-500">{pi.sgst ? inr(pi.sgst) : '-'}</td>
                <td className="px-3 py-2 text-right text-gray-500">{pi.igst ? inr(pi.igst) : '-'}</td>
                <td className="px-3 py-2 text-right font-semibold text-gray-900">{inr(pi.total_amount)}</td>
                {anyMatch && (
                  <td className="px-3 py-2 text-center">
                    {pi.match_status ? <MatchBadge status={pi.match_status} /> : <span className="text-gray-300 text-xs">-</span>}
                  </td>
                )}
                <td className="px-3 py-2 text-center">
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${pi.status === 'PAID' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-700'}`}>
                    {pi.status || 'OUTSTANDING'}
                  </span>
                </td>
                <td className="px-2 py-2 text-right text-gray-300">
                  <span className="text-xs text-bv font-medium">View</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
