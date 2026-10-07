// ============================================================================
// Purchase Invoices - the booking form drawer + its editable-line shape and
// GST line math. MOVED verbatim out of ../PurchaseInvoicesTab.tsx (Wave 6 diet).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { Plus, X, Loader2, FileText, Trash2 } from 'lucide-react';
import {
  purchaseInvoicesApi,
  type PurchaseInvoice,
  type PurchaseInvoiceLine,
  type PurchaseInvoiceCreate,
} from '../../../services/api/vendorAp';
import { useToast } from '../../../context/ToastContext';
import { useAuth } from '../../../context/AuthContext';
import type { Supplier } from '../purchaseTypes';
import { inr, GST_RATES, errMsg, stateCode, isInterstate } from './shared';

// The product ids a PRODUCT_NOT_CATALOGUED refusal names, so the accountant can
// ask the cataloguer without retyping them.
function blockedProductIds(e: unknown): string[] {
  if (!e || typeof e !== 'object' || !('response' in e)) return [];
  const d = (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
  if (!d || typeof d !== 'object') return [];
  const det = d as { code?: string; lines?: Array<{ product_id?: string }> };
  if (det.code !== 'PRODUCT_NOT_CATALOGUED') return [];
  return (det.lines || []).map((l) => l.product_id).filter((x): x is string => !!x);
}

// ---------------------------------------------------------------------------
// Editable line shape (string inputs while typing, coerced on submit)
// ---------------------------------------------------------------------------
export interface EditLine {
  product_id?: string;
  product_name: string;
  sku?: string;
  hsn_code?: string;
  quantity: string;
  unit_price: string;
  gst_rate: string;
}

export const blankLine = (): EditLine => ({ product_name: '', sku: '', hsn_code: '', quantity: '1', unit_price: '0', gst_rate: '5' });

function lineTaxable(l: EditLine): number {
  return (parseFloat(l.quantity) || 0) * (parseFloat(l.unit_price) || 0);
}
function lineTax(l: EditLine): number {
  return lineTaxable(l) * ((parseFloat(l.gst_rate) || 0) / 100);
}

// ============================================================================
// Invoice form drawer: header + editable lines + live GST split + Book
// ============================================================================
export function InvoiceFormDrawer({
  suppliers, prefill, initialLines, onClose, onBooked,
}: {
  suppliers: Supplier[];
  prefill: Partial<PurchaseInvoice>;
  initialLines: EditLine[];
  onClose: () => void;
  onBooked: () => void;
}) {
  const toast = useToast();
  const { user } = useAuth();
  const today = new Date().toISOString().slice(0, 10);

  const [vendorId, setVendorId] = useState(prefill.vendor_id ?? '');
  const [vendorInvoiceNo, setVendorInvoiceNo] = useState(prefill.vendor_invoice_no ?? '');
  const [vendorInvoiceDate, setVendorInvoiceDate] = useState((prefill.vendor_invoice_date ?? today).slice(0, 10));
  const [placeOfSupply, setPlaceOfSupply] = useState(prefill.place_of_supply ?? '');
  const [recipientGstin, setRecipientGstin] = useState(prefill.recipient_gstin ?? '');
  const [notes, setNotes] = useState('');
  const [lines, setLines] = useState<EditLine[]>(initialLines);
  const [saving, setSaving] = useState(false);

  const locked = Boolean(prefill.grn_id); // from a GRN -> keep the link fixed
  // Receipt already linked (from-GRN or consolidated DCs)? Then this IS a
  // goods bill and no declaration is asked for. A manual invoice must say
  // what it is for: GOODS routes to a receipt-first flow (the server refuses
  // a receipt-less goods bill — GRN_LINK_REQUIRED); SERVICES books as before.
  const prefillDcIds = (prefill as { linked_dc_ids?: string[] }).linked_dc_ids;
  const receiptLinked = locked || Boolean(prefillDcIds && prefillDcIds.length);
  const [billKind, setBillKind] = useState<'' | 'GOODS' | 'SERVICES'>('');

  // Default place_of_supply from the chosen vendor's state (the supplier's
  // state IS the place of supply for a purchase). Only auto-fill when empty so
  // a GRN-prefilled or hand-typed value is never clobbered.
  const selectedVendor = useMemo(() => suppliers.find((s) => s.id === vendorId), [suppliers, vendorId]);
  useEffect(() => {
    if (!placeOfSupply && selectedVendor) {
      const fromGstin = stateCode(selectedVendor.gstNumber);
      if (fromGstin) setPlaceOfSupply(fromGstin);
      else if (selectedVendor.state) setPlaceOfSupply(selectedVendor.state);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vendorId]);

  const inter = isInterstate(placeOfSupply, recipientGstin);
  const posKnown = Boolean(stateCode(placeOfSupply)) && Boolean(stateCode(recipientGstin));

  const taxable = lines.reduce((s, l) => s + lineTaxable(l), 0);
  const tax = lines.reduce((s, l) => s + lineTax(l), 0);
  const cgst = inter ? 0 : tax / 2;
  const sgst = inter ? 0 : tax / 2;
  const igst = inter ? tax : 0;
  const total = taxable + tax;

  const setLine = (i: number, patch: Partial<EditLine>) =>
    setLines((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const addLine = () => setLines((prev) => [...prev, blankLine()]);
  const removeLine = (i: number) => setLines((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev));

  const validLines = lines.filter((l) => l.product_name.trim() && (parseFloat(l.quantity) || 0) > 0);

  const book = async () => {
    if (!vendorId) { toast.error('Select a supplier'); return; }
    if (!vendorInvoiceNo.trim()) { toast.error('Supplier invoice number is required'); return; }
    if (validLines.length === 0) { toast.error('Add at least one line item with a name and quantity'); return; }
    if (!receiptLinked && !billKind) {
      toast.error('Say what this bill is for: goods, or services/expenses');
      return;
    }
    if (!receiptLinked && billKind === 'GOODS') {
      // Client mirror of the server's GRN_LINK_REQUIRED — goods bills start
      // from the receipt, and the guidance box names the routes.
      toast.error('A goods bill starts from its goods receipt — see the note above the Book button');
      return;
    }

    setSaving(true);
    try {
      const payloadLines: PurchaseInvoiceLine[] = validLines.map((l) => {
        const lt = lineTaxable(l);
        const rate = parseFloat(l.gst_rate) || 0;
        const t = lt * (rate / 100);
        return {
          product_id: l.product_id,
          product_name: l.product_name.trim(),
          sku: l.sku?.trim() || undefined,
          hsn_code: l.hsn_code?.trim() || undefined,
          quantity: parseFloat(l.quantity) || 0,
          unit_price: parseFloat(l.unit_price) || 0,
          gst_rate: rate,
          taxable_amount: Math.round(lt * 100) / 100,
          cgst: inter ? 0 : Math.round((t / 2) * 100) / 100,
          sgst: inter ? 0 : Math.round((t / 2) * 100) / 100,
          igst: inter ? Math.round(t * 100) / 100 : 0,
          line_total: Math.round((lt + t) * 100) / 100,
        };
      });
      // F9 — when this draft consolidates Delivery Challans, pass linked_dc_ids
      // so the backend runs the DC tally + flips dc_matched on each DC.
      const linkedDcIds = (prefill as { linked_dc_ids?: string[] }).linked_dc_ids;
      const payload: PurchaseInvoiceCreate = {
        vendor_id: vendorId,
        vendor_invoice_no: vendorInvoiceNo.trim(),
        vendor_invoice_date: vendorInvoiceDate,
        place_of_supply: placeOfSupply.trim() || undefined,
        recipient_gstin: recipientGstin.trim() || undefined,
        po_id: prefill.po_id,
        grn_id: prefill.grn_id,
        store_id: prefill.store_id ?? user?.activeStoreId,
        lines: payloadLines,
        notes: notes.trim() || undefined,
        linked_dc_ids: linkedDcIds && linkedDcIds.length ? linkedDcIds : undefined,
        bill_kind: receiptLinked ? 'GOODS' : (billKind as 'GOODS' | 'SERVICES'),
      };
      await purchaseInvoicesApi.create(payload);
      toast.success('Purchase invoice booked');
      onBooked();
    } catch (e) {
      const blocked = blockedProductIds(e);
      toast.error(errMsg(e, 'Failed to book purchase invoice'));
      if (blocked.length > 0) {
        // The accountant holds no products:write, so the refusal has to come
        // with a way forward: raise the task for the cataloguer.
        try {
          await purchaseInvoicesApi.requestCataloguing(
            blocked,
            undefined,
            prefill.store_id ?? user?.activeStoreId ?? undefined,
          );
          toast.info('Asked the cataloguer to finish these items — the bill can be booked after that');
        } catch (askErr) {
          // Never silent: the accountant must know nobody was asked.
          toast.warning(errMsg(askErr, 'The catalogue manager could not be asked. Try again.'));
        }
      }
    } finally {
      setSaving(false);
    }
  };

  const cls = 'border border-gray-300 rounded px-2 py-1.5 text-sm w-full';

  return (
    <div className="fixed inset-0 bg-black/30 flex justify-end z-50" onClick={onClose}>
      <div className="bg-white w-full max-w-3xl h-full overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3 sticky top-0 bg-white z-10">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <FileText className="w-5 h-5" />
            {prefill.grn_id ? `Invoice from GRN ${prefill.grn_number ?? ''}` : 'New purchase invoice'}
          </h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
        </div>

        <div className="p-5 space-y-5">
          {/* What is this bill for? (only asked when no receipt is linked) */}
          {!receiptLinked && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">This bill is for</label>
              <select
                className="border border-gray-300 rounded px-2 py-1.5 text-sm w-full"
                value={billKind}
                onChange={(e) => setBillKind(e.target.value as '' | 'GOODS' | 'SERVICES')}
              >
                <option value="">Choose: goods, or services/expenses…</option>
                <option value="GOODS">Goods (frames, lenses, stock — needs the goods receipt)</option>
                <option value="SERVICES">Services / expenses (rent, freight, job-work)</option>
              </select>
              {billKind === 'GOODS' && (
                <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  A goods bill starts from its goods receipt, so the quantities are tallied before the
                  purchase is final. Close this form and use <span className="font-semibold">Create from GRN</span> (a
                  PO-backed receipt) or <span className="font-semibold">Match DCs to Invoice</span> (Delivery
                  Challans). Goods bought without a PO? Log them as a Delivery Challan on the
                  Goods Receipt screen first (tick &lsquo;This is a Delivery Challan&rsquo;, pick the vendor, add what arrived).
                </div>
              )}
            </div>
          )}

          {/* Header */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Supplier</label>
              <select className={cls} value={vendorId} onChange={(e) => setVendorId(e.target.value)} disabled={locked}>
                <option value="">Select supplier...</option>
                {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}{s.code ? ` (${s.code})` : ''}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Supplier invoice no.</label>
              <input className={cls} value={vendorInvoiceNo} onChange={(e) => setVendorInvoiceNo(e.target.value)} placeholder="As printed on the supplier's bill" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Invoice date</label>
              <input className={cls} type="date" value={vendorInvoiceDate} onChange={(e) => setVendorInvoiceDate(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Place of supply (state)</label>
              <input className={cls} value={placeOfSupply} onChange={(e) => setPlaceOfSupply(e.target.value)} placeholder="e.g. 27 or 27-Maharashtra" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Recipient GSTIN (our entity)</label>
              <input className={cls} value={recipientGstin} onChange={(e) => setRecipientGstin(e.target.value)} placeholder="GSTIN receiving the supply" />
            </div>
            <div className="flex items-end">
              {/* Inter/intra-state indicator: the visible proof of the IGST fix */}
              <div className={`w-full rounded-lg px-3 py-2 text-sm border ${posKnown ? (inter ? 'bg-purple-50 border-purple-200 text-purple-800' : 'bg-blue-50 border-blue-200 text-blue-800') : 'bg-amber-50 border-amber-200 text-amber-800'}`}>
                {posKnown
                  ? (inter
                      ? <>Inter-state supply: <span className="font-semibold">IGST</span> (place of supply {stateCode(placeOfSupply)} differs from recipient {stateCode(recipientGstin)})</>
                      : <>Intra-state supply: <span className="font-semibold">CGST + SGST</span> (both state {stateCode(placeOfSupply)})</>)
                  : <>Enter place of supply and recipient GSTIN to classify CGST/SGST vs IGST.</>}
              </div>
            </div>
          </div>

          {/* Line items */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-semibold text-gray-700">Line items</h4>
              <button type="button" onClick={addLine} className="inline-flex items-center gap-1 text-xs font-medium text-bv hover:bg-bv-soft rounded-lg px-2 py-1"><Plus className="w-3.5 h-3.5" /> Add line</button>
            </div>
            <div className="overflow-x-auto border border-gray-200 rounded-lg">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-500 text-xs">
                  <tr>
                    <th className="text-left px-2 py-2">Product</th>
                    <th className="text-left px-2 py-2">HSN</th>
                    <th className="text-right px-2 py-2 w-16">Qty</th>
                    <th className="text-right px-2 py-2 w-24">Unit price</th>
                    <th className="text-right px-2 py-2 w-20">GST %</th>
                    <th className="text-right px-2 py-2 w-24">Taxable</th>
                    <th className="text-right px-2 py-2 w-24">{inter ? 'IGST' : 'CGST+SGST'}</th>
                    <th className="px-1 py-2 w-8"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {lines.map((l, i) => {
                    const lt = lineTaxable(l);
                    const t = lineTax(l);
                    return (
                      <tr key={i}>
                        <td className="px-2 py-1">
                          <input className="border border-gray-200 rounded px-2 py-1 text-sm w-full" value={l.product_name} onChange={(e) => setLine(i, { product_name: e.target.value })} placeholder="Item description" />
                          <input className="border border-gray-200 rounded px-2 py-0.5 text-xs w-full mt-1 text-gray-500" value={l.sku ?? ''} onChange={(e) => setLine(i, { sku: e.target.value })} placeholder="SKU (optional)" />
                        </td>
                        <td className="px-2 py-1"><input className="border border-gray-200 rounded px-2 py-1 text-sm w-20" value={l.hsn_code ?? ''} onChange={(e) => setLine(i, { hsn_code: e.target.value })} placeholder="HSN" /></td>
                        <td className="px-2 py-1"><input className="border border-gray-200 rounded px-2 py-1 text-sm w-16 text-right" type="number" min="0" value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} /></td>
                        <td className="px-2 py-1"><input className="border border-gray-200 rounded px-2 py-1 text-sm w-24 text-right" type="number" min="0" step="0.01" value={l.unit_price} onChange={(e) => setLine(i, { unit_price: e.target.value })} /></td>
                        <td className="px-2 py-1">
                          <select className="border border-gray-200 rounded px-1 py-1 text-sm w-20 text-right" value={l.gst_rate} onChange={(e) => setLine(i, { gst_rate: e.target.value })}>
                            {GST_RATES.map((r) => <option key={r} value={r}>{r}%</option>)}
                          </select>
                        </td>
                        <td className="px-2 py-1 text-right text-gray-700">{inr(lt)}</td>
                        <td className="px-2 py-1 text-right text-gray-500">{inr(t)}</td>
                        <td className="px-1 py-1 text-center">
                          <button type="button" onClick={() => removeLine(i)} className="text-gray-300 hover:text-red-600" title="Remove line"><Trash2 className="w-4 h-4" /></button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Totals */}
          <div className="flex justify-end">
            <div className="w-full tablet:w-72 space-y-1 text-sm">
              <Row label="Taxable" value={inr(taxable)} />
              {inter ? (
                <Row label="IGST" value={inr(igst)} />
              ) : (
                <>
                  <Row label="CGST" value={inr(cgst)} />
                  <Row label="SGST" value={inr(sgst)} />
                </>
              )}
              <div className="border-t border-gray-200 pt-1">
                <Row label="Total" value={inr(total)} strong />
              </div>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Notes (optional)</label>
            <input className={cls} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Internal note" />
          </div>
        </div>

        <div className="sticky bottom-0 bg-white border-t border-gray-100 px-5 py-3 flex items-center justify-between">
          <p className="text-xs text-gray-500">
            Booking posts this to the vendor's AP ledger {prefill.grn_id ? 'and links the GRN/PO' : ''}. Due date is set from the supplier's credit terms.
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} className="btn sm">Cancel</button>
            <button
              type="button"
              onClick={book}
              disabled={saving || (!receiptLinked && billKind === 'GOODS')}
              className="btn sm primary disabled:opacity-60"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />} Book invoice
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className={strong ? 'font-semibold text-gray-900' : 'text-gray-500'}>{label}</span>
      <span className={strong ? 'font-semibold text-gray-900' : 'text-gray-700'}>{value}</span>
    </div>
  );
}
