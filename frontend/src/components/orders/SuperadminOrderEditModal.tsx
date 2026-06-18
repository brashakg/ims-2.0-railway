// ============================================================================
// IMS 2.0 - SUPERADMIN post-creation order edit modal (build item #16)
// ============================================================================
// SUPERADMIN-only. Two modes, chosen automatically from whether the order
// already carries a tax invoice:
//   * NO invoice  -> inline edit of the line items + cart discount + a
//     mandatory reason. Calls PUT /orders/{id}/superadmin-edit; the backend
//     recomputes GST + grand_total and writes an immutable audit row.
//   * Invoice issued -> the operator must choose REVISED INVOICE vs CREDIT/
//     DEBIT NOTE; the recomputed delta + reason are shown. Calls PUT
//     /orders/{id}/superadmin-invoice-change.
// The recomputed grand total + delta are shown live before submit. An issued
// invoice is NEVER silently mutated.

import { useMemo, useState } from 'react';
import { XCircle, AlertTriangle } from 'lucide-react';
import type { Order } from '../../types';
import { orderApi } from '../../services/api/sales';
import { useToast } from '../../context/ToastContext';

interface EditLine {
  item_id?: string;
  item_type: string;
  product_id?: string;
  product_name?: string;
  sku?: string;
  brand?: string;
  category?: string;
  hsn_code?: string;
  quantity: number;
  unit_price: number;
  discount_percent: number;
}

interface Props {
  order: Order;
  onClose: () => void;
  onSaved: () => void;
}

const inr = (n: number) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 2,
  }).format(Number.isFinite(n) ? n : 0);

function lineFromOrderItem(it: Record<string, unknown>): EditLine {
  return {
    item_id: (it.id as string) || (it.item_id as string) || undefined,
    item_type: (it.itemType as string) || (it.item_type as string) || 'FRAME',
    product_id: (it.productId as string) || (it.product_id as string) || undefined,
    product_name:
      (it.productName as string) || (it.product_name as string) || undefined,
    sku: (it.sku as string) || undefined,
    brand: (it.brand as string) || undefined,
    category: (it.category as string) || undefined,
    hsn_code: (it.hsn_code as string) || (it.hsnCode as string) || undefined,
    quantity: Number(it.quantity ?? 1) || 1,
    unit_price: Number(it.unitPrice ?? it.unit_price ?? 0) || 0,
    discount_percent: Number(it.discountPercent ?? it.discount_percent ?? 0) || 0,
  };
}

export function SuperadminOrderEditModal({ order, onClose, onSaved }: Props) {
  const toast = useToast();
  const invoiceNumber =
    (order as unknown as { invoiceNumber?: string }).invoiceNumber ||
    (order as unknown as { invoice_number?: string }).invoice_number ||
    '';
  const hasInvoice = Boolean(invoiceNumber);

  const [lines, setLines] = useState<EditLine[]>(() =>
    (order.items || []).map((it) =>
      lineFromOrderItem(it as unknown as Record<string, unknown>),
    ),
  );
  const [cartDiscountPct, setCartDiscountPct] = useState<number>(
    Number(
      (order as unknown as { cartDiscountPercent?: number }).cartDiscountPercent ??
        (order as unknown as { cart_discount_percent?: number })
          .cart_discount_percent ??
        0,
    ) || 0,
  );
  const [reason, setReason] = useState('');
  const [mode, setMode] = useState<'REVISED_INVOICE' | 'CREDIT_NOTE'>(
    'CREDIT_NOTE',
  );
  const [saving, setSaving] = useState(false);

  // Live, client-side estimate of the new grand total. The backend is the
  // source of truth (GST-inclusive per-category); this preview just helps the
  // operator see the direction + rough magnitude before submitting.
  const newGrandTotal = useMemo(() => {
    const sub = lines.reduce((acc, l) => {
      const gross = (Number(l.unit_price) || 0) * (Number(l.quantity) || 0);
      const disc = gross * ((Number(l.discount_percent) || 0) / 100);
      return acc + (gross - disc);
    }, 0);
    const afterCart = sub * (1 - (Number(cartDiscountPct) || 0) / 100);
    return Math.round(afterCart * 100) / 100;
  }, [lines, cartDiscountPct]);

  const originalGrandTotal = Number(order.grandTotal) || 0;
  const delta = Math.round((newGrandTotal - originalGrandTotal) * 100) / 100;

  const updateLine = (idx: number, patch: Partial<EditLine>) => {
    setLines((prev) =>
      prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)),
    );
  };

  const removeLine = (idx: number) => {
    setLines((prev) => prev.filter((_, i) => i !== idx));
  };

  const addLine = () => {
    setLines((prev) => [
      ...prev,
      {
        item_type: 'FRAME',
        category: 'FRAME',
        product_name: '',
        quantity: 1,
        unit_price: 0,
        discount_percent: 0,
      },
    ]);
  };

  const submit = async () => {
    if (reason.trim().length < 4) {
      toast.error('A reason (at least 4 characters) is required.');
      return;
    }
    if (lines.length === 0) {
      toast.error('An edited order must keep at least one item.');
      return;
    }
    setSaving(true);
    try {
      if (!hasInvoice) {
        const res = await orderApi.superadminEditOrder(order.id, {
          reason: reason.trim(),
          items: lines as unknown as Array<Record<string, unknown>>,
          cart_discount_percent: cartDiscountPct,
        });
        toast.success(
          `Order edited. New total ${inr(Number(res?.grand_total) || 0)}.`,
        );
      } else {
        const res = await orderApi.superadminInvoiceChange(order.id, {
          mode,
          reason: reason.trim(),
          items: lines as unknown as Array<Record<string, unknown>>,
          cart_discount_percent: cartDiscountPct,
        });
        if (mode === 'REVISED_INVOICE') {
          toast.success(
            `Revised invoice ${res?.revised_invoice_number || ''} issued; original superseded.`,
          );
        } else {
          toast.success(
            `${res?.note_type || 'Note'} ${res?.note_number || ''} issued for ${inr(Number(res?.amount) || 0)}.`,
          );
        }
      }
      onSaved();
      onClose();
    } catch (e) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || 'Failed to save the order change.';
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[92vh] overflow-y-auto">
        <div className="p-6 border-b border-gray-200 sticky top-0 bg-white z-10">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-bold text-gray-900">
                Edit order {order.orderNumber}
              </h2>
              <p className="text-sm text-gray-500 mt-0.5">
                SUPERADMIN-only. This change is recorded in the immutable audit
                log.
              </p>
            </div>
            <button
              onClick={onClose}
              className="p-2 hover:bg-gray-100 rounded-lg"
              aria-label="Close"
              title="Close"
            >
              <XCircle className="w-5 h-5 text-gray-500" />
            </button>
          </div>
        </div>

        <div className="p-6 space-y-5">
          {hasInvoice && (
            <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg p-3">
              <AlertTriangle className="w-5 h-5 text-amber-600 mt-0.5 shrink-0" />
              <div className="text-sm text-amber-800">
                A tax invoice (<span className="font-medium">{invoiceNumber}</span>)
                has been issued. An issued invoice is never changed in place.
                Choose how to correct it:
                <div className="mt-2 flex flex-col gap-2">
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="invmode"
                      checked={mode === 'REVISED_INVOICE'}
                      onChange={() => setMode('REVISED_INVOICE')}
                      className="mt-1"
                    />
                    <span>
                      <span className="font-medium">Revised invoice</span> — issue
                      a new invoice number for the corrected order; the original
                      is marked superseded.
                    </span>
                  </label>
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="invmode"
                      checked={mode === 'CREDIT_NOTE'}
                      onChange={() => setMode('CREDIT_NOTE')}
                      className="mt-1"
                    />
                    <span>
                      <span className="font-medium">Credit / debit note</span> —
                      keep the original invoice and issue a note for the
                      difference only (credit if lower, debit if higher).
                    </span>
                  </label>
                </div>
              </div>
            </div>
          )}

          {/* Line items */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium text-gray-700">Items</p>
              <button
                type="button"
                onClick={addLine}
                className="text-xs px-2 py-1 bg-gray-100 rounded hover:bg-gray-200"
              >
                + Add line
              </button>
            </div>
            <div className="space-y-2">
              {lines.map((l, idx) => (
                <div
                  key={l.item_id || idx}
                  className="grid grid-cols-12 gap-2 items-end bg-gray-50 rounded-lg p-3"
                >
                  <div className="col-span-12 tablet:col-span-4">
                    <label className="block text-xs text-gray-500 mb-0.5">
                      Product
                    </label>
                    <input
                      type="text"
                      value={l.product_name || ''}
                      onChange={(e) =>
                        updateLine(idx, { product_name: e.target.value })
                      }
                      className="input-field w-full text-sm"
                      placeholder="Item name"
                    />
                  </div>
                  <div className="col-span-4 tablet:col-span-2">
                    <label className="block text-xs text-gray-500 mb-0.5">
                      Category
                    </label>
                    <input
                      type="text"
                      value={l.category || ''}
                      onChange={(e) =>
                        updateLine(idx, { category: e.target.value })
                      }
                      className="input-field w-full text-sm"
                      placeholder="FRAME"
                    />
                  </div>
                  <div className="col-span-2 tablet:col-span-1">
                    <label className="block text-xs text-gray-500 mb-0.5">Qty</label>
                    <input
                      type="number"
                      min={1}
                      value={l.quantity}
                      onChange={(e) =>
                        updateLine(idx, {
                          quantity: Math.max(1, Number(e.target.value) || 1),
                        })
                      }
                      className="input-field w-full text-sm"
                    />
                  </div>
                  <div className="col-span-3 tablet:col-span-2">
                    <label className="block text-xs text-gray-500 mb-0.5">
                      Unit ₹
                    </label>
                    <input
                      type="number"
                      min={0}
                      value={l.unit_price}
                      onChange={(e) =>
                        updateLine(idx, {
                          unit_price: Math.max(0, Number(e.target.value) || 0),
                        })
                      }
                      className="input-field w-full text-sm"
                    />
                  </div>
                  <div className="col-span-2 tablet:col-span-2">
                    <label className="block text-xs text-gray-500 mb-0.5">
                      Disc %
                    </label>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={l.discount_percent}
                      onChange={(e) =>
                        updateLine(idx, {
                          discount_percent: Math.min(
                            100,
                            Math.max(0, Number(e.target.value) || 0),
                          ),
                        })
                      }
                      className="input-field w-full text-sm"
                    />
                  </div>
                  <div className="col-span-1 tablet:col-span-1 flex justify-end">
                    <button
                      type="button"
                      onClick={() => removeLine(idx)}
                      className="text-red-500 hover:text-red-700 p-1"
                      aria-label="Remove line"
                      title="Remove line"
                    >
                      <XCircle className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Cart discount */}
          <div className="flex items-center gap-3">
            <label className="text-sm font-medium text-gray-700">
              Cart discount %
            </label>
            <input
              type="number"
              min={0}
              max={100}
              value={cartDiscountPct}
              onChange={(e) =>
                setCartDiscountPct(
                  Math.min(100, Math.max(0, Number(e.target.value) || 0)),
                )
              }
              className="input-field w-24 text-sm"
            />
          </div>

          {/* Totals preview */}
          <div className="border-t pt-4 space-y-1">
            <div className="flex justify-between text-sm text-gray-500">
              <span>Original grand total</span>
              <span>{inr(originalGrandTotal)}</span>
            </div>
            <div className="flex justify-between font-semibold">
              <span>New grand total (estimate)</span>
              <span>{inr(newGrandTotal)}</span>
            </div>
            <div
              className={
                'flex justify-between text-sm font-medium ' +
                (delta < 0
                  ? 'text-green-600'
                  : delta > 0
                    ? 'text-red-600'
                    : 'text-gray-500')
              }
            >
              <span>
                {delta < 0
                  ? 'Refund / credit to customer'
                  : delta > 0
                    ? 'Additional amount owed'
                    : 'No change'}
              </span>
              <span>{inr(Math.abs(delta))}</span>
            </div>
            {hasInvoice && mode === 'CREDIT_NOTE' && delta !== 0 && (
              <p className="text-xs text-gray-500 pt-1">
                A {delta < 0 ? 'credit' : 'debit'} note for{' '}
                {inr(Math.abs(delta))} will be issued against invoice{' '}
                {invoiceNumber}.
              </p>
            )}
          </div>

          {/* Reason (mandatory) */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Reason <span className="text-red-500">*</span>
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              maxLength={500}
              placeholder="Why is this order being changed? (recorded in the audit log)"
              className="input-field w-full text-sm"
            />
          </div>

          <div className="flex gap-2 pt-2">
            <button
              onClick={onClose}
              className="btn-outline flex-1"
              disabled={saving}
            >
              Cancel
            </button>
            <button
              onClick={submit}
              className="btn-primary flex-1"
              disabled={saving}
            >
              {saving
                ? 'Saving…'
                : hasInvoice
                  ? mode === 'REVISED_INVOICE'
                    ? 'Issue revised invoice'
                    : 'Issue credit / debit note'
                  : 'Save order edit'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default SuperadminOrderEditModal;
