// ============================================================================
// Purchase Invoices - the two source pickers (accepted GRN, open Delivery
// Challans). MOVED verbatim out of ../PurchaseInvoicesTab.tsx (Wave 6 diet).
// ============================================================================

import { useCallback, useEffect, useState } from 'react';
import { Plus, X, Loader2, PackageCheck } from 'lucide-react';
import {
  purchaseInvoicesApi,
  type PurchaseInvoice,
  type PurchaseInvoiceLine,
} from '../../../services/api/vendorAp';
import { vendorsApi } from '../../../services/api';
import { useToast } from '../../../context/ToastContext';
import { useAuth } from '../../../context/AuthContext';
import type { Supplier } from '../purchaseTypes';
import { errMsg } from './shared';
import { istDayString } from '../../../utils/datetime';

// ============================================================================
// GRN picker: choose an ACCEPTED GRN to bill. `onPick` is the tab's one
// from-GRN door (the deep link uses it too), so a refused draft behaves the
// same whichever way the receipt was opened.
// ============================================================================
export function GrnPickerModal({
  onClose, onPick,
}: {
  onClose: () => void;
  onPick: (grnId: string) => Promise<void>;
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
        const scope = storeId ? { store_id: storeId } : {};
        // Only ACCEPTED GRNs are billable (goods physically verified into
        // stock). PARTIALLY_ACCEPTED ones are listed too, but not billable:
        // some lines are HELD because their product is not catalogued yet, and
        // a receipt that simply vanished from this list with no explanation was
        // a dead end -- the accountant had nothing to act on.
        const [ok, held] = await Promise.all([
          vendorsApi.getGRNs({ status: 'ACCEPTED', ...scope }),
          vendorsApi.getGRNs({ status: 'PARTIALLY_ACCEPTED', ...scope }),
        ]);
        setGrns([...(ok?.grns ?? []), ...(held?.grns ?? [])]);
      } catch {
        setGrns([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [user?.activeStoreId]);

  const pick = async (grnId: string) => {
    setBusyId(grnId);
    try {
      await onPick(grnId);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-white w-full max-w-2xl rounded-lg shadow-xl max-h-[80dvh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
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
              {grns.map((g) => {
                const heldLines: Array<{ product_id?: string }> = g.unresolved_lines || [];
                const held = g.status === 'PARTIALLY_ACCEPTED' || heldLines.length > 0;
                return (
                <div key={g.grn_id} className="flex items-center justify-between border border-gray-200 rounded-lg px-3 py-2 hover:bg-gray-50">
                  <div>
                    <div className="font-medium text-gray-900">{g.grn_number} <span className="text-xs font-normal text-gray-500">· {g.vendor_name || g.vendor_id}</span></div>
                    <div className="text-xs text-gray-500">
                      PO {g.po_number || '-'} · Supplier inv {g.vendor_invoice_no || '-'} · {g.total_accepted ?? 0} units accepted
                    </div>
                    {held ? (
                      <div className="text-xs text-amber-700 mt-0.5">
                        {heldLines.length || 'Some'} line(s) are waiting to be catalogued — this receipt
                        cannot be invoiced until they are finished.
                      </div>
                    ) : null}
                  </div>
                  {held ? (
                    <button
                      type="button"
                      onClick={async () => {
                        const ids = heldLines.map((l) => l.product_id).filter(Boolean) as string[];
                        if (ids.length === 0) { toast.error('Nothing to request on this receipt'); return; }
                        setBusyId(g.grn_id);
                        try {
                          await purchaseInvoicesApi.requestCataloguing(ids);
                          toast.success('Asked the cataloguer to finish these items');
                        } catch (e) {
                          toast.error(errMsg(e, 'Could not raise the cataloguing request'));
                        } finally {
                          setBusyId(null);
                        }
                      }}
                      disabled={busyId === g.grn_id}
                      className="btn sm disabled:opacity-60"
                    >
                      {busyId === g.grn_id ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Ask for cataloguing
                    </button>
                  ) : (
                    <button type="button" onClick={() => pick(g.grn_id)} disabled={busyId === g.grn_id} className="btn sm primary disabled:opacity-60">
                      {busyId === g.grn_id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Invoice
                    </button>
                  )}
                </div>
                );
              })}
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
export function DcPickerModal({
  suppliers, onClose, onPicked,
}: {
  suppliers: Supplier[];
  onClose: () => void;
  onPicked: (prefill: Partial<PurchaseInvoice>, lines: PurchaseInvoiceLine[]) => void;
}) {
  const toast = useToast();
  const { user } = useAuth();
  // IST days (owner ruling): the UTC day is yesterday from 00:00 to 05:30 IST.
  const todayIso = istDayString(new Date()) ?? '';
  const thirtyAgoIso = istDayString(Date.now() - 30 * 86400000) ?? '';
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
          vendor_gstin: draft.vendor_gstin,
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
      <div className="bg-white w-full max-w-2xl rounded-lg shadow-xl max-h-[85dvh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
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
