// ============================================================================
// IMS 2.0 - Buy Desk -> bulk draft Purchase Order
// ============================================================================
// Multi-select rows on the Buy Desk, pick ONE vendor, confirm per-line qty +
// (optional) cost, and create a single DRAFT PO via the existing
// POST /vendors/purchase-orders. Every line carries the row's REAL catalogued
// product_id, so the PO catalog gate (now ON) accepts it. Cost is optional at
// draft -- it legitimately arrives at receiving (GRN backfills it). Buyers who
// want lines from different vendors just create one draft per vendor.

import { useEffect, useMemo, useState } from 'react';
import { FileText, Loader2, X as XIcon } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { vendorsApi } from '../../services/api';
import type { BuyDeskRow } from '../../services/api/buyDesk';

interface VendorOption {
  id: string;
  name: string;
  code: string;
}

interface DraftLine {
  product_id: string;
  name: string;
  sku: string;
  quantity: number;
  unitCost: number;
}

function mapVendor(v: Record<string, unknown>): VendorOption {
  const id = String(v.vendor_id ?? v._id ?? '');
  return {
    id,
    name: String(v.trade_name ?? v.legal_name ?? id),
    code: String(v.vendor_code ?? id.slice(0, 8).toUpperCase()),
  };
}

export default function BuyDeskDraftPOModal({
  rows,
  onClose,
  onCreated,
}: {
  rows: BuyDeskRow[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const { user } = useAuth();

  const [vendors, setVendors] = useState<VendorOption[]>([]);
  const [vendorsLoading, setVendorsLoading] = useState(true);
  const [vendorId, setVendorId] = useState('');
  const [expectedDelivery, setExpectedDelivery] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [lines, setLines] = useState<DraftLine[]>(
    rows.map((r) => ({
      product_id: r.product_id,
      name: r.name || r.sku || r.product_id,
      sku: r.sku || '',
      // Default to the netted buy signal when we have one, else 1.
      quantity: r.buy_signal && r.buy_signal > 0 ? r.buy_signal : 1,
      unitCost: 0,
    })),
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await vendorsApi.getVendors({ is_active: true });
        if (cancelled) return;
        const raw: Record<string, unknown>[] = resp?.vendors ?? [];
        setVendors(raw.map(mapVendor).filter((v) => v.id));
      } catch {
        if (!cancelled) toast.error('Could not load vendors');
      } finally {
        if (!cancelled) setVendorsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateLine = (idx: number, field: 'quantity' | 'unitCost', value: number) => {
    setLines((prev) => prev.map((l, i) => (i === idx ? { ...l, [field]: value } : l)));
  };

  const subtotal = useMemo(
    () => lines.reduce((s, l) => s + l.quantity * l.unitCost, 0),
    [lines],
  );

  const handleCreate = async () => {
    if (!vendorId) {
      toast.error('Pick a vendor for this draft PO');
      return;
    }
    const validLines = lines.filter((l) => l.product_id && l.quantity > 0);
    if (validLines.length === 0) {
      toast.error('Every line needs a quantity of at least 1');
      return;
    }
    const storeId = user?.activeStoreId ?? 'default';
    setSaving(true);
    try {
      const resp = await vendorsApi.createPurchaseOrder({
        vendor_id: vendorId,
        delivery_store_id: storeId,
        expected_date: expectedDelivery || undefined,
        notes: notes || undefined,
        items: validLines.map((l) => ({
          product_id: l.product_id,
          product_name: l.name,
          sku: l.sku || 'N/A',
          quantity: l.quantity,
          unit_price: l.unitCost,
        })),
      });
      toast.success(`Draft PO ${resp.po_number ?? ''} created with ${validLines.length} line(s)`);
      onCreated();
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Failed to create draft PO';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl my-8">
        <div className="flex items-center justify-between p-5 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
            <FileText className="w-5 h-5 text-blue-600" />
            Create draft PO · {rows.length} product{rows.length === 1 ? '' : 's'}
          </h2>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <XIcon className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="p-5 space-y-5">
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Vendor *</label>
              <select
                value={vendorId}
                onChange={(e) => setVendorId(e.target.value)}
                disabled={vendorsLoading}
                className="input-field"
              >
                <option value="">{vendorsLoading ? 'Loading vendors…' : 'Select a vendor…'}</option>
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                    {v.code ? ` (${v.code})` : ''}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs text-gray-400">
                One vendor per draft. For multiple vendors, create a draft per group.
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Expected delivery</label>
              <input
                type="date"
                value={expectedDelivery}
                onChange={(e) => setExpectedDelivery(e.target.value)}
                className="input-field"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Lines</label>
            <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
              {lines.map((l, idx) => (
                <div key={l.product_id} className="grid grid-cols-12 gap-2 items-center">
                  <div className="col-span-6 min-w-0">
                    <div className="text-sm font-medium text-gray-900 truncate">{l.name}</div>
                    <div className="text-xs text-gray-500 truncate">{l.sku}</div>
                  </div>
                  <div className="col-span-3">
                    <label className="block text-[10px] uppercase text-gray-400">Qty</label>
                    <input
                      type="number"
                      min="1"
                      value={l.quantity}
                      onChange={(e) => updateLine(idx, 'quantity', parseInt(e.target.value) || 0)}
                      className="input-field text-sm"
                    />
                  </div>
                  <div className="col-span-3">
                    <label className="block text-[10px] uppercase text-gray-400">Unit cost ({'₹'})</label>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={l.unitCost}
                      onChange={(e) => updateLine(idx, 'unitCost', parseFloat(e.target.value) || 0)}
                      className="input-field text-sm"
                    />
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-gray-400">
              Cost is optional on a draft — it can be confirmed when goods are received.
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="Optional notes for this PO…"
              className="input-field"
            />
          </div>

          <div className="flex items-center justify-between rounded-lg bg-gray-50 px-4 py-2 text-sm">
            <span className="text-gray-600">Subtotal (excl. tax)</span>
            <span className="font-semibold text-gray-900">
              {'₹'}
              {subtotal.toLocaleString()}
            </span>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 p-5 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={saving}
            className="btn-primary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
            {saving ? 'Creating…' : 'Create draft PO'}
          </button>
        </div>
      </div>
    </div>
  );
}
