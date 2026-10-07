// ============================================================================
// Purchase Invoices - the booking form drawer + its editable-line shape.
// MOVED out of ../PurchaseInvoicesTab.tsx (Wave 6 diet).
//
// It holds NO GST math. Every tax figure on it -- which GSTIN of ours, the tax
// head, each line's tax, the totals -- is the server's POST /preview, computed
// by the very code the booking stores with. Its own copy previewed CGST + SGST
// on a manual bill the server booked as IGST, called a junk-prefix GSTIN
// inter-state, and rounded a paisa differently (panel on F6/F40).
//
// It also says, for an admin, which shop the bill books to: the server books
// it to the receipt's shop, else to the booker's own active shop -- whatever
// shop his Purchase filter shows. A manual bill raised while he viewed Pune
// went to Dhanbad and vanished from the list, unannounced (review r2 #19).
// ============================================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, X, Loader2, FileText, Trash2 } from 'lucide-react';
import {
  purchaseInvoicesApi,
  type PurchaseInvoice,
  type PurchaseInvoiceLine,
  type PurchaseInvoiceCreate,
  type PurchaseInvoicePreview,
} from '../../../services/api/vendorAp';
import { useToast } from '../../../context/ToastContext';
import { PurchaseShopName, usePurchaseShop } from '../purchaseShop';
import type { Supplier } from '../purchaseTypes';
import { inr, GST_RATES, errMsg } from './shared';
import { istDayString } from '../../../utils/datetime';

// The product ids a PRODUCT_NOT_CATALOGUED refusal names, so the accountant can
// ask the cataloguer without retyping them. Read off the client's ApiError
// (code + detail): axios's `.response` never leaves services/api/client, so
// reading it here meant the cataloguer was never asked.
function blockedProductIds(e: unknown): string[] {
  const err = e as { code?: string; detail?: { lines?: Array<{ product_id?: string }> } } | null;
  if (err?.code !== 'PRODUCT_NOT_CATALOGUED') return [];
  return (err.detail?.lines || []).map((l) => l.product_id).filter((x): x is string => !!x);
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

const isBookable = (l: EditLine) => Boolean(l.product_name.trim()) && (parseFloat(l.quantity) || 0) > 0;

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
  /** `bookedAt`: the shop the server booked the bill to. */
  onBooked: (bookedAt?: string) => void;
}) {
  const toast = useToast();
  const { canPick, ownStoreId, storeId: viewing } = usePurchaseShop();
  const shopNameRef = useRef<HTMLSpanElement>(null);
  // The IST day (owner ruling): toISOString() is the UTC day, yesterday from
  // 00:00 to 05:30 IST -- a bill booked on it lands in the previous month's
  // GSTR-3B on the 1st, or under that month's lock.
  const today = istDayString(new Date()) ?? '';

  const [vendorId, setVendorId] = useState(prefill.vendor_id ?? '');
  const [vendorInvoiceNo, setVendorInvoiceNo] = useState(prefill.vendor_invoice_no ?? '');
  // `||`, not `??`: a receipt with no supplier invoice date drafts '' -- the
  // box must still open on a date (the due date is counted from it).
  const [vendorInvoiceDate, setVendorInvoiceDate] = useState((prefill.vendor_invoice_date || today).slice(0, 10));
  const [recipientGstin, setRecipientGstin] = useState(prefill.recipient_gstin ?? '');
  const [notes, setNotes] = useState('');
  // One credit switch (it can only turn credit OFF). No reverse-charge tick:
  // accounts payable does not handle reverse charge yet, so the form must not
  // offer it (owner/CA ruling needed first).
  const [claimCredit, setClaimCredit] = useState(true);
  const [lines, setLines] = useState<EditLine[]>(initialLines);
  const [saving, setSaving] = useState(false);

  const locked = Boolean(prefill.grn_id); // from a GRN -> keep the link fixed
  // Receipt already linked (from-GRN or consolidated DCs)? Then this IS a
  // goods bill and no declaration is asked for. A manual invoice must say
  // what it is for: GOODS routes to a receipt-first flow (the server refuses
  // a receipt-less goods bill — GRN_LINK_REQUIRED); SERVICES books as before.
  const prefillDcIds = (prefill as { linked_dc_ids?: string[] }).linked_dc_ids;
  const receiptLinked = locked || Boolean(prefillDcIds && prefillDcIds.length);
  // The shop the server books this bill to (purchase_invoices create): the
  // receipt's shop, else the booker's own active shop. A linked receipt whose
  // shop the form was not told (the from-GRN deep link's draft names none)
  // still books to the receipt's shop -- unknown here, so not named.
  const bookedTo = prefill.store_id || (receiptLinked ? undefined : ownStoreId);
  const [billKind, setBillKind] = useState<'' | 'GOODS' | 'SERVICES'>('');

  const selectedVendor = useMemo(() => suppliers.find((s) => s.id === vendorId), [suppliers, vendorId]);

  const setLine = (i: number, patch: Partial<EditLine>) =>
    setLines((prev) => prev.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const addLine = () => setLines((prev) => [...prev, blankLine()]);
  const removeLine = (i: number) => setLines((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev));

  const validLines = lines.filter(isBookable);
  // THE body the Book button sends -- the preview is computed on exactly this.
  const linkedDcIds = (prefill as { linked_dc_ids?: string[] }).linked_dc_ids;
  const payload: PurchaseInvoiceCreate = {
    vendor_id: vendorId,
    vendor_invoice_no: vendorInvoiceNo.trim(),
    vendor_invoice_date: vendorInvoiceDate,
    recipient_gstin: recipientGstin.trim() || undefined,
    po_id: prefill.po_id,
    grn_id: prefill.grn_id,
    store_id: prefill.store_id || ownStoreId,
    lines: validLines.map((l): PurchaseInvoiceLine => ({
      product_id: l.product_id,
      product_name: l.product_name.trim(),
      sku: l.sku?.trim() || undefined,
      hsn_code: l.hsn_code?.trim() || undefined,
      quantity: parseFloat(l.quantity) || 0,
      unit_price: parseFloat(l.unit_price) || 0,
      gst_rate: parseFloat(l.gst_rate) || 0,
    })),
    notes: notes.trim() || undefined,
    // F9 -- a draft consolidating Delivery Challans passes linked_dc_ids so
    // the backend runs the DC tally + flips dc_matched on each DC.
    linked_dc_ids: linkedDcIds && linkedDcIds.length ? linkedDcIds : undefined,
    bill_kind: receiptLinked ? 'GOODS' : (billKind || undefined),
    itc_eligible: claimCredit,
  };
  // What the tax depends on (not the invoice no. or notes): a change here asks
  // the server again, and Book waits until the answer is for THIS form. The
  // shop is in it: a bill with no receipt is booked for store_id's company.
  const taxKey = JSON.stringify([payload.vendor_id, payload.recipient_gstin, payload.grn_id, payload.linked_dc_ids, payload.store_id, payload.itc_eligible, payload.lines]);
  const [preview, setPreview] = useState<{ key: string; data?: PurchaseInvoicePreview; error?: string } | null>(null);
  const wantsPreview = Boolean(vendorId) && validLines.length > 0;
  useEffect(() => {
    if (!wantsPreview) return;
    let live = true;
    const t = setTimeout(() => {
      purchaseInvoicesApi
        .preview(payload)
        .then((data) => { if (live) setPreview({ key: taxKey, data }); })
        .catch((e) => { if (live) setPreview({ key: taxKey, error: errMsg(e, 'Could not work out the tax for this bill') }); });
    }, 250);
    return () => { live = false; clearTimeout(t); };
    // taxKey carries every input the preview depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taxKey, wantsPreview]);
  const shown = wantsPreview && preview?.key === taxKey ? preview : null;
  const pending = wantsPreview && !shown;
  const pv = shown?.data;
  const inter = Boolean(pv?.interstate);
  const supplierGstin = (pv?.vendor_gstin || prefill.vendor_gstin || selectedVendor?.gstNumber || '').trim();
  // The preview line for form row i (only bookable rows are sent, in order).
  const previewLine = (i: number) => {
    if (!pv || !isBookable(lines[i])) return undefined;
    return pv.lines[lines.slice(0, i).filter(isBookable).length];
  };

  const book = async () => {
    if (!vendorId) { toast.error('Select a supplier'); return; }
    if (!vendorInvoiceNo.trim()) { toast.error('Supplier invoice number is required'); return; }
    if (!vendorInvoiceDate) { toast.error('Supplier invoice date is required'); return; }
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
      const booked = await purchaseInvoicesApi.create(payload);
      const bookedAt = booked?.store_id || bookedTo;
      // An admin booking to a shop other than the one his list shows (or on
      // All stores) is told where it went, in the words of the line above.
      const named = bookedAt === bookedTo ? shopNameRef.current?.textContent : null;
      toast.success(
        canPick && bookedAt && bookedAt !== viewing
          ? `Purchase invoice booked to ${named || bookedAt}`
          : 'Purchase invoice booked',
      );
      onBooked(bookedAt);
    } catch (e) {
      const blocked = blockedProductIds(e);
      toast.error(errMsg(e, 'Failed to book purchase invoice'));
      if (blocked.length > 0) {
        // The accountant holds no products:write, so the refusal has to come
        // with a way forward: raise the task for the cataloguer.
        try {
          await purchaseInvoicesApi.requestCataloguing(blocked);
          toast.info('Asked the cataloguer to finish these items — the bill can be booked after that');
        } catch {
          /* the refusal above is the message that matters */
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
          {/* F63: for an admin, whatever shop his filter shows, where this
              bill books to -- the NewOrdersDeliverTo pattern for bills. */}
          {canPick && (bookedTo || receiptLinked) && (
            <p className="text-sm text-gray-600">
              Bills booked here go to{' '}
              {bookedTo ? (
                <span ref={shopNameRef} className="font-medium text-gray-900">
                  <PurchaseShopName storeId={bookedTo} />
                </span>
              ) : (
                'the shop that received the goods'
              )}
            </p>
          )}

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
              <label className="block text-xs font-medium text-gray-600 mb-1">Supplier GSTIN</label>
              <div className="border border-gray-200 bg-gray-50 rounded px-2 py-1.5 text-sm text-gray-700">{supplierGstin || 'Not on file'}</div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Recipient GSTIN (our entity)</label>
              <input className={cls} value={recipientGstin} onChange={(e) => setRecipientGstin(e.target.value)} placeholder="GSTIN receiving the supply" />
              {!recipientGstin.trim() && pv?.recipient_gstin && (
                <p className="mt-1 text-xs text-gray-500">Left blank: booked on ours, {pv.recipient_gstin}</p>
              )}
            </div>
            <div className="flex items-end">
              {/* The tax head the server WILL book (POST /preview) */}
              <div className={`w-full rounded-lg px-3 py-2 text-sm border ${pv ? (inter ? 'bg-purple-50 border-purple-200 text-purple-800' : 'bg-blue-50 border-blue-200 text-blue-800') : 'bg-amber-50 border-amber-200 text-amber-800'}`}>
                {pv
                  ? (inter
                      ? <>Inter-state supply: <span className="font-semibold">IGST</span> (supplier state {pv.supplier_state}, ours {pv.supply_place_recipient})</>
                      : pv.supplier_state && pv.supply_place_recipient
                        ? <>Intra-state supply: <span className="font-semibold">CGST + SGST</span> (both state {pv.supplier_state})</>
                        : <>Booked as <span className="font-semibold">CGST + SGST</span>: {pv.supplier_state ? 'no GSTIN of ours to compare with' : 'the supplier\'s GSTIN names no state'}.</>)
                  : shown?.error
                    ? <>{shown.error}</>
                    : pending
                      ? <>Working out the tax...</>
                      : <>Pick the supplier and add a line to see the tax.</>}
              </div>
            </div>
          </div>

          {/* Input credit: the server's verdict for THIS form, and the one switch */}
          <div className="rounded-lg border border-gray-200 px-3 py-2 space-y-2">
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                <input type="checkbox" role="switch" checked={claimCredit} onChange={(e) => setClaimCredit(e.target.checked)} />
                Claim input credit
              </label>
            </div>
            {pv && pv.itc_eligible === false && (
              <p className="text-xs font-medium text-amber-800">
                {claimCredit
                  ? 'No input credit: the supplier has no valid GSTIN'
                  : 'No input credit: switched off'}
              </p>
            )}
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
                    const pl = previewLine(i);
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
                        <td className="px-2 py-1 text-right text-gray-700">{pl ? inr(pl.taxable) : '-'}</td>
                        <td className="px-2 py-1 text-right text-gray-500">{pl ? inr(pl.cgst + pl.sgst + pl.igst) : '-'}</td>
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
              <Row label="Taxable" value={inr(pv?.taxable_total)} />
              {inter ? (
                <Row label="IGST" value={inr(pv?.igst_total)} />
              ) : (
                <>
                  <Row label="CGST" value={inr(pv?.cgst_total)} />
                  <Row label="SGST" value={inr(pv?.sgst_total)} />
                </>
              )}
              <div className="border-t border-gray-200 pt-1">
                <Row label="Total" value={inr(pv?.total)} strong />
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
              disabled={saving || pending || (!receiptLinked && billKind === 'GOODS')}
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
