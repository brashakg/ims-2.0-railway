// ============================================================================
// IMS 2.0 - Units & labels (F26 / F27)
// ============================================================================
// Receiving mints one stock unit, with its own barcode, per physical piece.
// This lists those units -- for one product at the shop (from the stock
// ledger) or for one goods receipt (the dialog after receiving) -- and holds
// the label print doors: the selected units, every unit just received, or a
// reprint of one. All of them go through the ONE label renderer (unitLabel.ts)
// and record barcode_printed on exactly the units sent to the print dialog.

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Printer, X } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi, type StockUnit } from '../../services/api/inventory';
import type { UserRole } from '../../types';
import { printUnitLabels } from './unitLabel';

/** Mirrors the POST /inventory/units/barcode-printed gate (_INVENTORY_ROLES). */
const LABEL_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'];

const STATUS_TEXT: Record<string, string> = {
  AVAILABLE: 'On shelf',
  RESERVED: 'Reserved',
  SOLD: 'Sold',
  TRANSFERRED: 'Transferred out',
  QUARANTINED: 'Quarantined',
  DAMAGED: 'Damaged',
  RTV: 'Returned to vendor',
};

/** A unit still physically in the shop -- the only kind worth a label. */
const inShop = (u: StockUnit) => u.status === 'AVAILABLE' || u.status === 'RESERVED';

const money = (n: number) => `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

interface UnitLabelsModalProps {
  /** Units of this product at the shop (stock ledger door)... */
  productId?: string;
  /** ...or the units one goods receipt put on the shelf (after receiving). */
  grnId?: string;
  title: string;
  subtitle?: string;
  onClose: () => void;
}

export function UnitLabelsModal({ productId, grnId, title, subtitle, onClose }: UnitLabelsModalProps) {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const canPrint = hasRole(LABEL_ROLES);
  // Settings > Printers is open to these roles only (settingsSections.ts).
  const canCalibrate = hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']);
  const [units, setUnits] = useState<StockUnit[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const storeId = user?.activeStoreId;

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await inventoryApi.getUnits({ store_id: storeId, product_id: productId, grn_id: grnId });
        if (!alive) return;
        setUnits(res.units || []);
        // After receiving, every piece just shelved is what needs a label.
        if (grnId) setSelected(new Set((res.units || []).filter(inShop).map((u) => u.stock_id)));
      } catch {
        if (alive) setFailed(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [storeId, productId, grnId]);

  const print = async (batch: StockUnit[]) => {
    if (batch.length === 0) return;
    const result = printUnitLabels(batch);
    if (result.method !== 'html') {
      toast.error(`${result.message} Allow pop-ups for IMS and try again.`);
      return;
    }
    const ids = batch.map((u) => u.stock_id);
    setBusy(true);
    try {
      await inventoryApi.markBarcodePrinted(ids);
      setUnits((cur) => (cur || []).map((u) => (ids.includes(u.stock_id) ? { ...u, barcode_printed: true } : u)));
      toast.success(`${ids.length} label${ids.length === 1 ? '' : 's'} opened in the print dialog.`);
    } catch {
      toast.warning('Labels opened in the print dialog, but IMS could not record them as printed.');
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const printable = (units || []).filter(inShop);
  const chosen = printable.filter((u) => selected.has(u.stock_id));
  const showCost = (units || []).some((u) => u.cost_price !== undefined);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[92dvh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={title}
      >
        <div className="px-5 py-4 border-b border-gray-200 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-semibold text-gray-900 truncate">{title}</h2>
            <p className="text-sm text-gray-500">
              {subtitle || 'Each piece has its own barcode. Its label carries that barcode.'}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-700 shrink-0" aria-label="Close" title="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-auto p-5">
          {failed ? (
            <p className="text-sm text-red-600">Could not load the units. Close this and try again.</p>
          ) : units === null ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            </div>
          ) : units.length === 0 ? (
            <p className="text-sm text-gray-600">
              {grnId
                ? 'This receipt put no units on the shelf, so there is nothing to print.'
                : 'No units of this product at this shop.'}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead className="text-xs text-gray-500 uppercase">
                  <tr className="border-b border-gray-200">
                    {canPrint && (
                      <th className="py-2 pr-2 text-left w-8">
                        <input
                          type="checkbox"
                          aria-label="Select every unit on the shelf"
                          checked={printable.length > 0 && chosen.length === printable.length}
                          onChange={(e) =>
                            setSelected(new Set(e.target.checked ? printable.map((u) => u.stock_id) : []))
                          }
                        />
                      </th>
                    )}
                    <th className="py-2 pr-3 text-left">Barcode</th>
                    <th className="py-2 pr-3 text-left">Receipt</th>
                    <th className="py-2 pr-3 text-left">Status</th>
                    <th className="py-2 pr-3 text-left">Received</th>
                    <th className="py-2 pr-3 text-left">Label</th>
                    {showCost && <th className="py-2 pr-3 text-right">Cost</th>}
                    {canPrint && <th className="py-2" />}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {units.map((u) => (
                    <tr key={u.stock_id}>
                      {canPrint && (
                        <td className="py-2 pr-2">
                          <input
                            type="checkbox"
                            aria-label={`Select ${u.barcode}`}
                            disabled={!inShop(u)}
                            checked={selected.has(u.stock_id)}
                            onChange={() => toggle(u.stock_id)}
                          />
                        </td>
                      )}
                      <td className="py-2 pr-3 font-mono text-gray-900">{u.barcode}</td>
                      <td className="py-2 pr-3 text-gray-600">
                        {u.grn_number || (u.source === 'OPENING_STOCK' ? 'Opening stock' : '-')}
                      </td>
                      <td className="py-2 pr-3">{STATUS_TEXT[u.status] || u.status}</td>
                      <td className="py-2 pr-3 text-gray-600">{u.received_on || '-'}</td>
                      <td className="py-2 pr-3 text-gray-600">{u.barcode_printed ? 'Printed' : 'Not printed'}</td>
                      {showCost && (
                        <td className="py-2 pr-3 text-right">{u.cost_price != null ? money(u.cost_price) : '-'}</td>
                      )}
                      {canPrint && (
                        <td className="py-2 text-right">
                          {inShop(u) && (
                            <button
                              type="button"
                              onClick={() => print([u])}
                              disabled={busy}
                              className="p-1.5 text-gray-500 hover:text-gray-900"
                              aria-label={`Reprint label for ${u.barcode}`}
                              title="Print this unit's label"
                            >
                              <Printer className="w-4 h-4" />
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="px-5 py-3 border-t border-gray-200 flex flex-wrap items-center justify-end gap-2">
          {canCalibrate && (
            <Link to="/settings/printers" className="text-xs text-gray-500 hover:text-gray-800 mr-auto">
              Labels off-centre? Set the label offset
            </Link>
          )}
          <button onClick={onClose} className="btn">
            {grnId ? 'Skip for now' : 'Close'}
          </button>
          {canPrint && chosen.length > 0 && (
            <button onClick={() => print(chosen)} disabled={busy} className="btn accent">
              <Printer className="w-4 h-4" />
              Print {chosen.length} label{chosen.length === 1 ? '' : 's'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default UnitLabelsModal;
