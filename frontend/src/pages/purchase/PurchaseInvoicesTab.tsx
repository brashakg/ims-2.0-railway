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
  CheckCircle2, ShieldCheck, Scale, ShieldAlert,
} from 'lucide-react';
import {
  purchaseInvoicesApi,
  type PurchaseInvoice,
  type PurchaseInvoiceLine,
  type PurchaseInvoiceCreate,
  type PurchaseInvoiceMatch,
  type PurchaseInvoiceConfig,
  type PurchaseConfig,
  type MatchStatus,
  type MatchLine,
} from '../../services/api/vendorAp';
import { vendorsApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import type { Supplier } from './purchaseTypes';
import type { UserRole } from '../../types';

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
        <InvoiceList invoices={invoices} onOpen={setDetailInvoice} />
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
// F9 — DC picker: select open Delivery Challans, consolidate into one draft
// invoice. The accountant filters by vendor + date range (last 30 days
// default), ticks one or more open DCs, and "Generate Draft Invoice" calls the
// from-dcs aggregation. Booking the draft runs the DC->invoice tally.
// ============================================================================
function DcPickerModal({
  suppliers, onClose, onPicked,
}: {
  suppliers: Supplier[];
  onClose: () => void;
  onPicked: (prefill: Partial<PurchaseInvoice>, lines: PurchaseInvoiceLine[]) => void;
}) {
  const toast = useToast();
  const { user } = useAuth();
  const todayIso = new Date().toISOString().slice(0, 10);
  const thirtyAgoIso = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const [vendorId, setVendorId] = useState('');
  const [dateFrom, setDateFrom] = useState(thirtyAgoIso);
  const [dateTo, setDateTo] = useState(todayIso);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [dcs, setDcs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const storeId = user?.activeStoreId;
      const rows = await purchaseInvoicesApi.getOpenDcs({
        vendor_id: vendorId || undefined,
        store_id: storeId || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
      setDcs(rows);
    } finally {
      setLoading(false);
    }
  }, [user?.activeStoreId, vendorId, dateFrom, dateTo]);

  useEffect(() => { reload(); }, [reload]);

  const chosenIds = Object.keys(selected).filter((k) => selected[k]);

  const generate = async () => {
    if (chosenIds.length === 0) { toast.error('Select at least one DC'); return; }
    setBusy(true);
    try {
      const draft = await purchaseInvoicesApi.createFromDcs(chosenIds, vendorId || undefined);
      onPicked(
        {
          vendor_id: draft.vendor_id,
          vendor_name: draft.vendor_name,
          vendor_invoice_no: '',
          vendor_invoice_date: todayIso,
          place_of_supply: draft.place_of_supply,
          recipient_gstin: draft.recipient_gstin,
          store_id: user?.activeStoreId,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          linked_dc_ids: (draft as any).linked_dc_ids ?? chosenIds,
        } as Partial<PurchaseInvoice>,
        draft.lines ?? [],
      );
    } catch (e) {
      toast.error(errMsg(e, 'Could not build a draft invoice from the selected DCs'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white w-full max-w-2xl rounded-lg shadow-xl max-h-[85vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3 sticky top-0 bg-white">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2"><PackageCheck className="w-5 h-5" /> Match Delivery Challans to one invoice</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-5 space-y-4">
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Vendor</label>
              <select value={vendorId} onChange={(e) => setVendorId(e.target.value)} className="border border-gray-300 rounded px-2 py-1.5 text-sm w-full">
                <option value="">All vendors</option>
                {suppliers.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">DC date from</label>
              <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="border border-gray-300 rounded px-2 py-1.5 text-sm w-full" title="DC date from" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">DC date to</label>
              <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="border border-gray-300 rounded px-2 py-1.5 text-sm w-full" title="DC date to" />
            </div>
          </div>

          {loading ? (
            <div className="flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading open Delivery Challans...</div>
          ) : dcs.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              <PackageCheck className="w-10 h-10 text-gray-300 mx-auto mb-2" />
              No open Delivery Challans for this filter. Log + accept a DC in the GRN flow first.
            </div>
          ) : (
            <div className="space-y-2">
              {dcs.map((g) => {
                const id = g.grn_id;
                return (
                  <label key={id} className="flex items-center gap-3 border border-gray-200 rounded-lg px-3 py-2 hover:bg-gray-50 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={Boolean(selected[id])}
                      onChange={(e) => setSelected((prev) => ({ ...prev, [id]: e.target.checked }))}
                    />
                    <div className="flex-1">
                      <div className="font-medium text-gray-900">
                        DC {g.dc_number || g.grn_number}
                        <span className="text-xs font-normal text-gray-500"> · {g.vendor_name || g.vendor_id}</span>
                      </div>
                      <div className="text-xs text-gray-500">
                        {(g.dc_date || '').slice(0, 10)} · {g.total_accepted ?? 0} units accepted · store {g.store_id || '-'}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2 border-t border-gray-100">
            <button type="button" onClick={onClose} className="px-4 py-2 border border-gray-300 text-gray-600 rounded-lg text-sm hover:bg-gray-100">Cancel</button>
            <button type="button" onClick={generate} disabled={busy || chosenIds.length === 0} className="btn sm primary disabled:opacity-60">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Generate Draft Invoice ({chosenIds.length})
            </button>
          </div>
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

// ============================================================================
// Phase 2 - 3-way match: badge + valuation/tolerance note + detail drawer
// ============================================================================

// Visual config for each match verdict. MATCHED = the invoice agrees with the
// PO + GRN within tolerance; ON_HOLD_EXCEPTION = a line is outside tolerance and
// needs a human; MATCHED_OVERRIDE = a reviewer approved despite the variance.
const MATCH_BADGE: Record<MatchStatus, { label: string; color: string; Icon: typeof CheckCircle2 }> = {
  MATCHED: { label: 'Matched', color: 'bg-green-100 text-green-800', Icon: CheckCircle2 },
  ON_HOLD_EXCEPTION: { label: 'On hold', color: 'bg-amber-100 text-amber-800', Icon: AlertTriangle },
  MATCHED_OVERRIDE: { label: 'Override approved', color: 'bg-blue-100 text-blue-800', Icon: ShieldCheck },
  UNMATCHED: { label: 'No PO/GRN', color: 'bg-gray-100 text-gray-600', Icon: FileText },
  NOT_APPLICABLE: { label: 'Not matched', color: 'bg-gray-100 text-gray-500', Icon: FileText },
};

function MatchBadge({ status }: { status: MatchStatus }) {
  const cfg = MATCH_BADGE[status] ?? MATCH_BADGE.NOT_APPLICABLE;
  const { label, color, Icon } = cfg;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${color}`}>
      <Icon className="w-3 h-3" /> {label}
    </span>
  );
}

// "MOVING_AVERAGE" -> "Moving average" for display.
function methodLabel(method?: string): string {
  if (!method) return '';
  return method
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w))
    .join(' ');
}

// The active config values live under `.config` (GET /config returns
// { config, defaults, valuation_methods }).
function activeConfig(config: PurchaseInvoiceConfig | null): PurchaseConfig | undefined {
  return config?.config ?? undefined;
}

// "+/-5%" tolerance label from a percentage, or '' when unknown.
function tolerancePctLabel(pct?: number | null): string {
  return pct == null ? '' : `+/-${pct}%`;
}

// Small read-only note under the page intro: the active valuation method +
// match tolerance, so the user understands how MATCHED / ON_HOLD is decided.
// Renders nothing when the backend exposes no config (fail-soft).
function ConfigNote({ config }: { config: PurchaseInvoiceConfig | null }) {
  const cfg = activeConfig(config);
  if (!cfg) return null;
  const method = methodLabel(cfg.valuation_method);
  const tol = tolerancePctLabel(cfg.match_tolerance_pct);
  if (!method && !tol) return null;
  return (
    <p className="inline-flex flex-wrap items-center gap-x-1.5 text-xs text-gray-400">
      <Scale className="w-3.5 h-3.5 text-gray-400" />
      {method && <span>Valuation: <span className="font-medium text-gray-500">{method}</span></span>}
      {method && tol && <span className="text-gray-300">·</span>}
      {tol && <span>3-way match tolerance: <span className="font-medium text-gray-500">{tol}</span></span>}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Number formatting helpers for the 3-way table (compact, fail-soft on undef)
// ---------------------------------------------------------------------------
const num = (n?: number) => (n == null || Number.isNaN(n) ? '-' : `${Math.round(n * 100) / 100}`);
// Roles allowed to approve a 3-way-match exception (release an ON_HOLD invoice
// for payment despite a variance). Mirrors the _AP_ROLES backend gate.
const APPROVE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];

// A right-aligned numeric cell that turns amber when its value is part of a
// flagged variance (qty / price out of tolerance).
function VCell({ value, flagged }: { value: string; flagged?: boolean }) {
  return (
    <td className={`px-2 py-1.5 text-right ${flagged ? 'text-amber-700 font-semibold bg-amber-50' : 'text-gray-700'}`}>
      {value}
    </td>
  );
}

// Signed % chip, amber when |pct| exceeds tolerance. Hidden when pct is null
// (not comparable, e.g. the product isn't on the PO).
function VarPct({ pct, tol }: { pct?: number | null; tol?: number }) {
  if (pct == null) return <span className="text-gray-300">-</span>;
  const over = tol != null && Math.abs(pct) > tol;
  const sign = pct > 0 ? '+' : '';
  return (
    <span className={over ? 'text-amber-700 font-semibold' : 'text-gray-500'}>
      {sign}{Math.round(pct * 10) / 10}%
    </span>
  );
}

// The side-by-side 3-way comparison: one row per product line, with PO ordered |
// GRN received | invoice invoiced (qty + price) and the qty/price variance %s +
// reasons. Out-of-tolerance cells are highlighted amber. `tol` is the active
// match tolerance (%) used to flag individual qty/price cells.
function ThreeWayTable({ lines, tol }: { lines: MatchLine[]; tol?: number }) {
  const overTol = (pct?: number | null) => pct != null && tol != null && Math.abs(pct) > tol;
  return (
    <div className="overflow-x-auto border border-gray-200 rounded-lg">
      <table className="w-full text-xs">
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th rowSpan={2} className="text-left px-2 py-1.5 align-bottom border-r border-gray-200">Item</th>
            <th colSpan={2} className="text-center px-2 py-1 border-r border-gray-200">Ordered (PO)</th>
            <th colSpan={1} className="text-center px-2 py-1 border-r border-gray-200">Received (GRN)</th>
            <th colSpan={2} className="text-center px-2 py-1 border-r border-gray-200">Invoiced (Invoice)</th>
            <th colSpan={2} className="text-center px-2 py-1 border-r border-gray-200">Variance</th>
            <th rowSpan={2} className="text-left px-2 py-1.5 align-bottom">Reasons</th>
          </tr>
          <tr className="text-[11px]">
            <th className="text-right px-2 py-1">Qty</th>
            <th className="text-right px-2 py-1 border-r border-gray-200">Rate</th>
            <th className="text-right px-2 py-1 border-r border-gray-200">Qty</th>
            <th className="text-right px-2 py-1">Qty</th>
            <th className="text-right px-2 py-1 border-r border-gray-200">Rate</th>
            <th className="text-right px-2 py-1">Qty</th>
            <th className="text-right px-2 py-1 border-r border-gray-200">Price</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {lines.map((l, i) => {
            const qtyFlag = overTol(l.qty_variance_pct);
            const priceFlag = overTol(l.price_variance_pct);
            return (
              <tr key={l.product_id ? `${l.product_id}-${i}` : i} className="hover:bg-gray-50">
                <td className="px-2 py-1.5 border-r border-gray-200">
                  <div className="font-medium text-gray-900">{l.description || l.product_id || `Line ${i + 1}`}</div>
                  {l.hsn && <div className="text-[11px] text-gray-400">HSN {l.hsn}</div>}
                </td>
                {/* PO */}
                <td className="px-2 py-1.5 text-right text-gray-700">{num(l.ordered_qty ?? undefined)}</td>
                <td className="px-2 py-1.5 text-right text-gray-700 border-r border-gray-200">{l.po_unit_price != null ? inr(l.po_unit_price) : '-'}</td>
                {/* GRN */}
                <td className={`px-2 py-1.5 text-right border-r border-gray-200 ${qtyFlag ? 'text-amber-700 font-semibold bg-amber-50' : 'text-gray-700'}`}>
                  {l.received_qty == null ? <span className="text-gray-300">-</span> : num(l.received_qty)}
                </td>
                {/* Invoice */}
                <VCell value={num(l.invoiced_qty)} flagged={qtyFlag} />
                <td className={`px-2 py-1.5 text-right border-r border-gray-200 ${priceFlag ? 'text-amber-700 font-semibold bg-amber-50' : 'text-gray-700'}`}>
                  {l.invoice_unit_price != null ? inr(l.invoice_unit_price) : '-'}
                </td>
                {/* Variance %s */}
                <td className="px-2 py-1.5 text-right"><VarPct pct={l.qty_variance_pct} tol={tol} /></td>
                <td className="px-2 py-1.5 text-right border-r border-gray-200"><VarPct pct={l.price_variance_pct} tol={tol} /></td>
                {/* Reasons */}
                <td className="px-2 py-1.5">
                  {l.reasons && l.reasons.length > 0 ? (
                    <ul className="space-y-0.5">
                      {l.reasons.map((r, ri) => (
                        <li key={ri} className="text-amber-700 flex items-start gap-1">
                          <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" /> <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  ) : l.status === 'EXCEPTION' ? (
                    <span className="text-amber-700">Out of tolerance</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-green-700"><CheckCircle2 className="w-3 h-3" /> OK</span>
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

// ============================================================================
// Detail drawer: header + 3-way match + valuation + approve-exception action
// ============================================================================
function InvoiceDetailDrawer({
  invoice, config, onClose, onChanged,
}: {
  invoice: PurchaseInvoice;
  config: PurchaseInvoiceConfig | null;
  onClose: () => void;
  onChanged: (updated: Partial<PurchaseInvoice> & { purchase_invoice_id?: string }) => void;
}) {
  const toast = useToast();
  const { hasRole } = useAuth();
  const id = invoice.purchase_invoice_id;
  // Start from the match detail embedded on the list row, then refine with the
  // (possibly recomputed) breakdown from getMatch(). Fail-soft: null -> no match
  // section. `override` is the approval audit, kept locally so the banner updates
  // immediately after an approve without a full reload.
  const [match, setMatch] = useState<PurchaseInvoiceMatch | null>(invoice.match_detail ?? null);
  const [override, setOverride] = useState(invoice.exception_override ?? null);
  const [rowStatus, setRowStatus] = useState<MatchStatus | null>(invoice.match_status ?? null);
  const [loadingMatch, setLoadingMatch] = useState(true);
  const [approving, setApproving] = useState(false);
  const [reason, setReason] = useState('');

  useEffect(() => {
    let alive = true;
    setLoadingMatch(true);
    purchaseInvoicesApi.getMatch(id)
      .then((m) => { if (alive && m) setMatch(m); })
      .finally(() => { if (alive) setLoadingMatch(false); });
    return () => { alive = false; };
  }, [id]);

  // The effective verdict: an approved override wins; else the fetched match
  // verdict; else the row's embedded status; else "not applicable".
  const status: MatchStatus = rowStatus ?? match?.match_status ?? 'NOT_APPLICABLE';
  const onHold = status === 'ON_HOLD_EXCEPTION';
  const canApprove = hasRole(APPROVE_ROLES);

  // Invoice-level exception reasons (the flat `exceptions` list from the match).
  const invoiceReasons = useMemo(() => Array.from(new Set(match?.exceptions ?? [])), [match]);

  const cfg = activeConfig(config);
  const valuationMethod = cfg?.valuation_method;
  // The active match tolerance %, preferring what the match actually used.
  const tol = match?.tolerance_pct ?? cfg?.match_tolerance_pct;
  const matchLines = match?.lines ?? [];
  const summary = match?.summary;
  // Per-line valuation only if a (future) backend stamps unit_cost on the line.
  const valuationLines = (invoice.lines ?? []).filter((l) => l.unit_cost != null || l.valuation_amount != null);

  const approve = async () => {
    if (!reason.trim()) { toast.error('A reason is required to approve the exception'); return; }
    setApproving(true);
    try {
      const res = await purchaseInvoicesApi.approveException(id, { reason: reason.trim() });
      const newStatus = res.match_status ?? 'MATCHED_OVERRIDE';
      setRowStatus(newStatus);
      if (res.exception_override) setOverride(res.exception_override);
      onChanged({ purchase_invoice_id: id, match_status: newStatus, exception_override: res.exception_override });
      toast.success('Exception approved - invoice released for payment');
    } catch (e) {
      toast.error(errMsg(e, 'Failed to approve the exception'));
    } finally {
      setApproving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/30 flex justify-end z-50" onClick={onClose}>
      <div className="bg-white w-full max-w-4xl h-full overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3 sticky top-0 bg-white z-10">
          <div>
            <h3 className="font-semibold text-gray-900 flex items-center gap-2">
              <FileText className="w-5 h-5" /> {invoice.vendor_name || invoice.vendor_id}
              <span className="text-sm font-normal text-gray-500">· {invoice.vendor_invoice_no}</span>
            </h3>
            <div className="text-xs text-gray-500 mt-0.5 flex flex-wrap gap-x-3">
              <span>{(invoice.vendor_invoice_date || '').slice(0, 10)}</span>
              {invoice.po_number && <span>PO {invoice.po_number}</span>}
              {invoice.grn_number && <span>GRN {invoice.grn_number}</span>}
              <span className="font-medium text-gray-700">{inr(invoice.total_amount)}</span>
            </div>
          </div>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
        </div>

        <div className="p-5 space-y-5">
          {/* Verdict banner */}
          <div className={`rounded-lg border p-3 flex items-start gap-3 ${
            status === 'MATCHED' ? 'bg-green-50 border-green-200'
              : onHold ? 'bg-amber-50 border-amber-200'
              : status === 'MATCHED_OVERRIDE' ? 'bg-blue-50 border-blue-200'
              : 'bg-gray-50 border-gray-200'
          }`}>
            {status === 'MATCHED' ? <CheckCircle2 className="w-5 h-5 text-green-600 mt-0.5" />
              : onHold ? <ShieldAlert className="w-5 h-5 text-amber-600 mt-0.5" />
              : status === 'MATCHED_OVERRIDE' ? <ShieldCheck className="w-5 h-5 text-blue-600 mt-0.5" />
              : <FileText className="w-5 h-5 text-gray-400 mt-0.5" />}
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-gray-900">3-way match</span>
                <MatchBadge status={status} />
              </div>
              <p className="text-xs text-gray-600 mt-1">
                {status === 'MATCHED' && 'Purchase order, goods receipt and supplier invoice agree within tolerance.'}
                {onHold && 'One or more lines are outside tolerance. Review the variances below; an authorised user can approve to release for payment.'}
                {status === 'MATCHED_OVERRIDE' && 'An exception was approved despite a variance.'}
                {status === 'UNMATCHED' && 'This invoice has no linked PO/GRN to match against (manual invoice).'}
                {status === 'NOT_APPLICABLE' && 'No 3-way match has been computed for this invoice.'}
              </p>
              {override && (
                <p className="text-[11px] text-blue-700 mt-1">
                  Override approved{override.approved_by ? ` by ${override.approved_by}` : ''}
                  {override.approved_at ? ` on ${String(override.approved_at).slice(0, 10)}` : ''}
                  {override.reason ? ` - "${override.reason}"` : ''}
                </p>
              )}
              {summary && (summary.total_lines ?? 0) > 0 && (
                <p className="text-[11px] text-gray-500 mt-1">
                  {summary.matched_lines ?? 0} of {summary.total_lines} lines matched
                  {(summary.exception_lines ?? 0) > 0 ? `, ${summary.exception_lines} with exceptions` : ''}
                  {tol != null ? ` · tolerance ${tolerancePctLabel(tol)}` : ''}
                </p>
              )}
            </div>
          </div>

          {/* Invoice-level exception reasons */}
          {invoiceReasons.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <p className="text-xs font-semibold text-amber-800 mb-1">Why this is on hold</p>
              <ul className="space-y-0.5">
                {invoiceReasons.map((r, i) => (
                  <li key={i} className="text-xs text-amber-700 flex items-start gap-1">
                    <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" /> <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 3-way comparison table */}
          {loadingMatch ? (
            <div className="flex items-center gap-2 text-gray-500 text-sm"><Loader2 className="w-4 h-4 animate-spin" /> Loading 3-way match...</div>
          ) : matchLines.length > 0 ? (
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Line comparison</h4>
              <ThreeWayTable lines={matchLines} tol={tol} />
              <p className="text-[11px] text-gray-400 mt-1.5">
                Qty / price variance is each line&apos;s invoiced value vs the purchase order; amber = outside the
                {tol != null ? ` ${tolerancePctLabel(tol)}` : ''} match tolerance.
              </p>
            </div>
          ) : (
            // No match breakdown -> still show the invoice's own lines so the
            // drawer is never empty (manual invoice / Phase-1-only backend).
            <InvoiceLinesFallback invoice={invoice} />
          )}

          {/* Inventory valuation note (the booking trues up product moving-average
              cost; per-line unit cost is shown only if the backend stamps it). */}
          {(valuationLines.length > 0 || valuationMethod) && (
            <div>
              <h4 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-1.5">
                <Scale className="w-4 h-4 text-gray-500" /> Inventory valuation
                {valuationMethod && <span className="text-xs font-normal text-gray-400">· {methodLabel(valuationMethod)}</span>}
              </h4>
              {valuationLines.length > 0 ? (
                <div className="overflow-x-auto border border-gray-200 rounded-lg">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-50 text-gray-500">
                      <tr>
                        <th className="text-left px-2 py-1.5">Item</th>
                        <th className="text-right px-2 py-1.5">Qty</th>
                        <th className="text-right px-2 py-1.5">Unit cost</th>
                        <th className="text-right px-2 py-1.5">Stock value</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {valuationLines.map((l, i) => (
                        <tr key={i} className="hover:bg-gray-50">
                          <td className="px-2 py-1.5 text-gray-900">{l.product_name || l.sku || `Line ${i + 1}`}</td>
                          <td className="px-2 py-1.5 text-right text-gray-700">{num(l.quantity)}</td>
                          <td className="px-2 py-1.5 text-right text-gray-700">{l.unit_cost != null ? inr(l.unit_cost) : '-'}</td>
                          <td className="px-2 py-1.5 text-right text-gray-700">
                            {l.valuation_amount != null ? inr(l.valuation_amount)
                              : l.unit_cost != null ? inr(l.unit_cost * (l.quantity || 0)) : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-xs text-gray-500">
                  Booking this invoice trues up each product&apos;s landed cost
                  {valuationMethod ? ` using ${methodLabel(valuationMethod)}` : ''} from the invoiced unit price.
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer: approve-exception action (gated, ON_HOLD only) */}
        <div className="sticky bottom-0 bg-white border-t border-gray-100 px-5 py-3">
          {onHold ? (
            canApprove ? (
              <div className="flex flex-col tablet:flex-row tablet:items-end gap-2">
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-600 mb-1">Reason for approval (required, audited)</label>
                  <input
                    className="border border-gray-300 rounded px-2 py-1.5 text-sm w-full"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Why release this invoice for payment despite the variance?"
                  />
                </div>
                <button type="button" onClick={approve} disabled={approving || !reason.trim()} className="btn sm primary disabled:opacity-60 whitespace-nowrap">
                  {approving ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />} Approve exception
                </button>
              </div>
            ) : (
              <p className="text-xs text-gray-500 flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4 text-amber-500" />
                This invoice is on hold for a 3-way-match exception. Only an Admin, Accountant or Superadmin can approve it.
              </p>
            )
          ) : (
            <div className="flex justify-end">
              <button type="button" onClick={onClose} className="btn sm">Close</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Fallback when there's no match breakdown: render the invoice's own line items
// (description / qty / rate / GST / taxable) so the detail drawer is never blank.
function InvoiceLinesFallback({ invoice }: { invoice: PurchaseInvoice }) {
  const lines = invoice.lines ?? [];
  if (lines.length === 0) {
    return <p className="text-sm text-gray-500">No line items recorded on this invoice.</p>;
  }
  return (
    <div>
      <h4 className="text-sm font-semibold text-gray-700 mb-2">Invoice lines</h4>
      <div className="overflow-x-auto border border-gray-200 rounded-lg">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="text-left px-2 py-1.5">Item</th>
              <th className="text-left px-2 py-1.5">HSN</th>
              <th className="text-right px-2 py-1.5">Qty</th>
              <th className="text-right px-2 py-1.5">Rate</th>
              <th className="text-right px-2 py-1.5">GST%</th>
              <th className="text-right px-2 py-1.5">Taxable</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {lines.map((l, i) => (
              <tr key={i} className="hover:bg-gray-50">
                <td className="px-2 py-1.5 text-gray-900">{l.product_name || l.sku || `Line ${i + 1}`}</td>
                <td className="px-2 py-1.5 text-gray-500">{l.hsn_code || '-'}</td>
                <td className="px-2 py-1.5 text-right text-gray-700">{num(l.quantity)}</td>
                <td className="px-2 py-1.5 text-right text-gray-700">{l.unit_price != null ? inr(l.unit_price) : '-'}</td>
                <td className="px-2 py-1.5 text-right text-gray-700">{num(l.gst_rate)}%</td>
                <td className="px-2 py-1.5 text-right text-gray-700">{l.taxable_amount != null ? inr(l.taxable_amount) : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
