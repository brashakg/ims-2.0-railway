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

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Plus, X, Loader2, FileText, PackageCheck, Trash2, AlertTriangle, RefreshCw,
} from 'lucide-react';
import {
  purchaseInvoicesApi,
  type PurchaseInvoice,
  type PurchaseInvoiceLine,
  type PurchaseInvoiceCreate,
} from '../../services/api/vendorAp';
import { vendorsApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import type { Supplier } from './purchaseTypes';

const inr = (n?: number) => `₹${(Math.round((n || 0) * 100) / 100).toLocaleString('en-IN')}`;
// Optical GST rates per business rules (5% frames/lenses/CL, 12% certain CL,
// 18% sunglasses/watches/accessories, plus 0/28 for completeness).
const GST_RATES = [0, 5, 12, 18, 28];

function errMsg(e: unknown, fb: string) {
  if (e && typeof e === 'object' && 'response' in e) {
    const r = (e as { response?: { data?: { detail?: string } } }).response;
    if (r?.data?.detail) return String(r.data.detail);
  }
  return e instanceof Error ? e.message : fb;
}

// Pull a 2-digit state code from a place_of_supply ("27", "27-Maharashtra")
// or a GSTIN (first two chars). Mirrors backend itc_reconcile._state_code so
// the FE preview matches how the server will route the tax.
function stateCode(value?: string): string {
  if (!value) return '';
  const m = String(value).trim().match(/\d{2}/);
  return m ? m[0] : '';
}

// True when the supplier's place_of_supply state differs from our recipient
// GSTIN's state -> the supply is inter-state -> IGST. Missing either side
// defaults to intra-state (CGST/SGST), matching the backend fallback.
function isInterstate(placeOfSupply?: string, recipientGstin?: string): boolean {
  const pos = stateCode(placeOfSupply);
  const rec = stateCode(recipientGstin);
  if (!pos || !rec) return false;
  return pos !== rec;
}

// ---------------------------------------------------------------------------
// Editable line shape (string inputs while typing, coerced on submit)
// ---------------------------------------------------------------------------
interface EditLine {
  product_id?: string;
  product_name: string;
  sku?: string;
  hsn_code?: string;
  quantity: string;
  unit_price: string;
  gst_rate: string;
}

const blankLine = (): EditLine => ({ product_name: '', sku: '', hsn_code: '', quantity: '1', unit_price: '0', gst_rate: '5' });

function lineTaxable(l: EditLine): number {
  return (parseFloat(l.quantity) || 0) * (parseFloat(l.unit_price) || 0);
}
function lineTax(l: EditLine): number {
  return lineTaxable(l) * ((parseFloat(l.gst_rate) || 0) / 100);
}

// ============================================================================
// Tab root: list + GRN picker + invoice form
// ============================================================================
export function PurchaseInvoicesTab({ suppliers }: { suppliers: Supplier[] }) {
  const { user } = useAuth();
  const [invoices, setInvoices] = useState<PurchaseInvoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pickingGrn, setPickingGrn] = useState(false);
  // form: either prefilled from a GRN draft or a fresh manual invoice
  const [form, setForm] = useState<{ prefill: Partial<PurchaseInvoice>; lines: EditLine[] } | null>(null);

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

  const openManual = () => setForm({ prefill: {}, lines: [blankLine()] });

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
        <p className="text-sm text-gray-500">
          Supplier tax invoices booked to Accounts Payable &amp; the ITC register, with line-level HSN + GST.
        </p>
        <div className="flex items-center gap-2">
          <button type="button" onClick={load} className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          <button type="button" onClick={() => setPickingGrn(true)} className="btn sm">
            <PackageCheck className="w-4 h-4" /> Create from GRN
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
        <InvoiceList invoices={invoices} />
      )}

      {pickingGrn && (
        <GrnPickerModal
          onClose={() => setPickingGrn(false)}
          onPicked={openFromGrnDraft}
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
    </div>
  );
}

// ============================================================================
// List of booked / draft purchase invoices
// ============================================================================
function InvoiceList({ invoices }: { invoices: PurchaseInvoice[] }) {
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
            <th className="text-center px-3 py-2">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {invoices.map((pi) => {
            const inter = pi.is_interstate ?? (pi.igst || 0) > 0;
            return (
              <tr key={pi.purchase_invoice_id} className="hover:bg-gray-50">
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
                <td className="px-3 py-2 text-center">
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${pi.status === 'BOOKED' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-700'}`}>
                    {pi.status || 'BOOKED'}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ============================================================================
// GRN picker: choose an ACCEPTED GRN to bill, calls createFromGrn for a draft
// ============================================================================
function GrnPickerModal({
  onClose, onPicked,
}: {
  onClose: () => void;
  onPicked: (prefill: Partial<PurchaseInvoice>, lines: PurchaseInvoiceLine[]) => void;
}) {
  const toast = useToast();
  const { user } = useAuth();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [grns, setGrns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const storeId = user?.activeStoreId;
        // Only ACCEPTED GRNs are billable (goods physically verified into stock).
        const res = await vendorsApi.getGRNs({ status: 'ACCEPTED', ...(storeId ? { store_id: storeId } : {}) });
        setGrns(res?.grns ?? []);
      } catch {
        setGrns([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [user?.activeStoreId]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pick = async (grn: any) => {
    const grnId = grn.grn_id;
    setBusyId(grnId);
    try {
      const draft = await purchaseInvoicesApi.createFromGrn(grnId);
      onPicked(
        {
          vendor_id: draft.vendor_id ?? grn.vendor_id,
          vendor_name: draft.vendor_name ?? grn.vendor_name,
          vendor_invoice_no: draft.vendor_invoice_no ?? grn.vendor_invoice_no,
          vendor_invoice_date: draft.vendor_invoice_date ?? grn.vendor_invoice_date,
          po_id: draft.po_id ?? grn.po_id,
          po_number: draft.po_number ?? grn.po_number,
          grn_id: draft.grn_id ?? grnId,
          grn_number: draft.grn_number ?? grn.grn_number,
          place_of_supply: draft.place_of_supply,
          recipient_gstin: draft.recipient_gstin,
          store_id: draft.store_id ?? grn.store_id,
        },
        draft.lines ?? [],
      );
    } catch (e) {
      // Fail-soft: backend draft route not ready -> still let the user proceed
      // with a header prefilled from the GRN; they fill the lines manually.
      toast.warning(errMsg(e, 'Could not auto-prefill from GRN; opening a blank invoice for this GRN.'));
      onPicked(
        {
          vendor_id: grn.vendor_id,
          vendor_name: grn.vendor_name,
          vendor_invoice_no: grn.vendor_invoice_no,
          vendor_invoice_date: grn.vendor_invoice_date,
          po_id: grn.po_id,
          po_number: grn.po_number,
          grn_id: grnId,
          grn_number: grn.grn_number,
          store_id: grn.store_id,
        },
        [],
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white w-full max-w-2xl rounded-lg shadow-xl max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3 sticky top-0 bg-white">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2"><PackageCheck className="w-5 h-5" /> Pick an accepted GRN to invoice</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-5">
          {loading ? (
            <div className="flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading accepted GRNs...</div>
          ) : grns.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              <PackageCheck className="w-10 h-10 text-gray-300 mx-auto mb-2" />
              No accepted GRNs to invoice. Receive and accept goods in the GRN flow first.
            </div>
          ) : (
            <div className="space-y-2">
              {grns.map((g) => (
                <div key={g.grn_id} className="flex items-center justify-between border border-gray-200 rounded-lg px-3 py-2 hover:bg-gray-50">
                  <div>
                    <div className="font-medium text-gray-900">{g.grn_number} <span className="text-xs font-normal text-gray-500">· {g.vendor_name || g.vendor_id}</span></div>
                    <div className="text-xs text-gray-500">
                      PO {g.po_number || '-'} · Supplier inv {g.vendor_invoice_no || '-'} · {g.total_accepted ?? 0} units accepted
                    </div>
                  </div>
                  <button type="button" onClick={() => pick(g)} disabled={busyId === g.grn_id} className="btn sm primary disabled:opacity-60">
                    {busyId === g.grn_id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Invoice
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Invoice form drawer: header + editable lines + live GST split + Book
// ============================================================================
function InvoiceFormDrawer({
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
      };
      await purchaseInvoicesApi.create(payload);
      toast.success('Purchase invoice booked');
      onBooked();
    } catch (e) {
      toast.error(errMsg(e, 'Failed to book purchase invoice'));
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
            <button type="button" onClick={book} disabled={saving} className="btn sm primary disabled:opacity-60">
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
