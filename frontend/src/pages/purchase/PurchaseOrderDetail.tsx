// ============================================================================
// IMS 2.0 - Purchase Order Detail Modal
// ============================================================================

import { useState, useEffect } from 'react';
import {
  Send,
  Pencil,
  X as XIcon,
  Printer,
  History,
  Ban,
} from 'lucide-react';
import { getStatusBadge } from './statusBadge';
import type { PurchaseOrder } from './purchaseTypes';
import { POPrint } from '../../components/print/POPrint';
import { POLifecycleDrawer } from '../../components/purchase/POLifecycleDrawer';
import { useAuth } from '../../context/AuthContext';
import { resolveStoreIdentity, type StoreIdentity } from '../../components/print/storeIdentity';

/** What a person can do to an order from this modal (owner rulings
 *  2026-09-28): send a draft to the vendor, edit a draft, cancel the order or
 *  one line -- the cancels always WITH a reason. */
export type POAction = 'send' | 'edit' | 'cancel' | 'cancel-line';
export interface POActionOptions {
  reason?: string;
  lineIndex?: number;
}

interface PurchaseOrderDetailProps {
  po: PurchaseOrder;
  onClose: () => void;
  onAction: (po: PurchaseOrder, action: POAction, opts?: POActionOptions) => void | Promise<void>;
}

const PART_RECEIVED = new Set(['PARTIALLY_RECEIVED', 'PARTIAL']);
const CANCELLABLE = new Set(['DRAFT', 'SENT', 'ACKNOWLEDGED', ...PART_RECEIVED]);

/** Units still due on a line (ordered minus received, never negative). */
function dueOn(item: PurchaseOrder['items'][number]): number {
  return Math.max(0, (item.quantity ?? 0) - (item.receivedQty ?? 0));
}

export function PurchaseOrderDetail({ po, onClose, onAction }: PurchaseOrderDetailProps) {
  const { user } = useAuth();
  const [showPrint, setShowPrint] = useState(false);
  const [showTimeline, setShowTimeline] = useState(false);
  // The cancel being confirmed: the whole order, or one line (by position).
  const [cancelTarget, setCancelTarget] = useState<{ lineIndex?: number } | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const isDraft = po.status === 'DRAFT';
  const partReceived = PART_RECEIVED.has(po.status);
  const cancellable = CANCELLABLE.has(po.status);
  // A draft line is removed (it never went to the vendor) but never the last
  // one -- that is cancelling the order. A sent line cancels what is still due.
  const canCancelLine = (item: PurchaseOrder['items'][number]) =>
    cancellable && (isDraft ? po.items.length > 1 : dueOn(item) > 0);
  const anyLineCancellable = po.items.some(canCancelLine);
  const cancelLine = cancelTarget?.lineIndex !== undefined ? po.items[cancelTarget.lineIndex] : null;

  const closeCancel = () => {
    setCancelTarget(null);
    setReason('');
  };
  const confirmCancel = async () => {
    const why = reason.trim();
    if (why.length < 3 || !cancelTarget) return;
    setBusy(true);
    try {
      if (cancelTarget.lineIndex !== undefined) {
        await onAction(po, 'cancel-line', { reason: why, lineIndex: cancelTarget.lineIndex });
      } else {
        await onAction(po, 'cancel', { reason: why });
      }
      closeCancel();
    } finally {
      setBusy(false);
    }
  };

  // Resolve the issuing (buyer) store + its legal entity from the PO's own
  // store_id (falls back to the user's active store) so the PO header shows the
  // real buyer entity legal name + GSTIN + logo -- never a hardcoded brand.
  const issuingStoreId = (po as any).storeId || (po as any).store_id || user?.activeStoreId;
  const [identity, setIdentity] = useState<StoreIdentity | null>(null);
  useEffect(() => {
    if (!issuingStoreId) return;
    let cancelled = false;
    resolveStoreIdentity(issuingStoreId)
      .then((id) => { if (!cancelled) setIdentity(id); })
      .catch(() => { if (!cancelled) setIdentity(null); });
    return () => { cancelled = true; };
  }, [issuingStoreId]);

  const sv = identity?.store;
  const storeInfo = {
    storeName: sv?.storeName || sv?.storeCode || '',
    storeCode: sv?.storeCode || '',
    brand: sv?.brand || '',
    address: sv?.address || '',
    city: sv?.city || '',
    state: sv?.state || '',
    stateCode: sv?.stateCode || '',
    pincode: sv?.pincode || '',
    phone: (sv as any)?.phone as string | undefined,
    gstin: sv?.gstin as string | undefined,
  };

  // Build the POPrintData shape from the PurchaseOrder.
  const poPrintData = {
    po_id: po.id,
    po_number: po.poNumber,
    po_date: po.date,
    expected_delivery: po.expectedDelivery ?? '',
    vendor_id: po.supplierId,
    vendor_name: po.supplierName,
    vendor_address: '',
    vendor_gstin: '',
    // A line cancelled in full orders nothing -- it does not print.
    items: po.items.filter((item) => item.quantity > 0).map((item) => ({
      product_id: item.productId,
      product_name: item.productName,
      quantity: item.quantity,
      unit_price: item.unitCost,
      total: item.total,
    })),
    subtotal: po.subtotal,
    tax_amount: po.taxAmount,
    grand_total: po.total,
    terms_conditions: po.notes,
  };

  return (
    <>
    {showPrint && (
      <POPrint
        po={poPrintData}
        store={storeInfo}
        entity={identity?.entity ?? null}
        onClose={() => setShowPrint(false)}
      />
    )}
    {/* Lifecycle drawer layers over this modal (z-60 > z-50). No
        onSendToVendor here — for a DRAFT the send action is the modal's own
        "Send to vendor" button right behind the drawer. */}
    {showTimeline && (
      <POLifecycleDrawer
        poId={po.id}
        poNumber={po.poNumber}
        onClose={() => setShowTimeline(false)}
      />
    )}
    <div className="fixed inset-0 bg-black/50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div>
            <h2 className="text-xl font-bold text-gray-900 flex items-center gap-3">
              {po.poNumber}
              {getStatusBadge(po.status)}
            </h2>
            <p className="text-sm text-gray-500 mt-1">{po.supplierName}</p>
          </div>
          <div className="flex items-center gap-2">
            {/* PO lifecycle drawer (timeline + GRNs + invoices) */}
            <button
              type="button"
              onClick={() => setShowTimeline(true)}
              className="px-3 py-1.5 text-sm font-medium text-gray-700 border border-gray-300 hover:bg-gray-50 rounded-lg transition-colors flex items-center gap-1.5"
            >
              <History className="w-4 h-4" />
              Timeline
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <XIcon className="w-5 h-5 text-gray-500" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="p-6 space-y-6">
          {/* PO Details */}
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-gray-600 mb-1">Order Date</p>
              <p className="text-sm font-medium text-gray-900">{new Date(po.date).toLocaleDateString()}</p>
            </div>
            <div>
              <p className="text-xs text-gray-600 mb-1">Expected Delivery</p>
              <p className="text-sm font-medium text-gray-900">{new Date(po.expectedDelivery).toLocaleDateString()}</p>
            </div>
            {po.approvedBy && (
              <div>
                <p className="text-xs text-gray-600 mb-1">Approved By</p>
                <p className="text-sm font-medium text-gray-900">{po.approvedBy}</p>
              </div>
            )}
            {po.receivedDate && (
              <div>
                <p className="text-xs text-gray-600 mb-1">Received Date</p>
                <p className="text-sm font-medium text-gray-900">{new Date(po.receivedDate).toLocaleDateString()}</p>
              </div>
            )}
          </div>

          {po.status === 'CANCELLED' && po.cancellationReason && (
            <div className="p-3 bg-red-50 rounded-lg border border-red-200">
              <p className="text-xs text-red-700 font-medium mb-1">Cancelled</p>
              <p className="text-sm text-red-800">Reason: {po.cancellationReason}</p>
              <p className="text-xs text-red-700 mt-1">The Timeline shows who cancelled it and when.</p>
            </div>
          )}

          {/* Notes */}
          {po.notes && (
            <div className="p-3 bg-yellow-50 rounded-lg border border-yellow-200">
              <p className="text-xs text-yellow-700 font-medium mb-1">Notes</p>
              <p className="text-sm text-yellow-800">{po.notes}</p>
            </div>
          )}

          {/* Items Table */}
          <div>
            <h3 className="text-sm font-semibold text-gray-900 mb-3">Items</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 border-b border-gray-200">
                    <th className="text-left py-2 px-3 text-xs font-medium text-gray-600">Product</th>
                    <th className="text-left py-2 px-3 text-xs font-medium text-gray-600">SKU</th>
                    <th className="text-right py-2 px-3 text-xs font-medium text-gray-600">Qty</th>
                    <th className="text-right py-2 px-3 text-xs font-medium text-gray-600">Unit Cost</th>
                    <th className="text-right py-2 px-3 text-xs font-medium text-gray-600">Tax %</th>
                    <th className="text-right py-2 px-3 text-xs font-medium text-gray-600">Total</th>
                    {anyLineCancellable && <th className="py-2 px-3" aria-label="Line actions" />}
                  </tr>
                </thead>
                <tbody>
                  {po.items.map((item, idx) => (
                    <tr key={idx} className="border-b border-gray-100">
                      <td className={`py-2 px-3 ${item.lineStatus === 'CANCELLED' ? 'text-gray-400 line-through' : 'text-gray-900'}`}>{item.productName}</td>
                      <td className="py-2 px-3 text-gray-600">{item.sku}</td>
                      <td className="py-2 px-3 text-right text-gray-900">
                        {item.quantity}
                        {(item.cancelledQty ?? 0) > 0 && (
                          <span className="block text-xs text-red-600">{item.cancelledQty} cancelled</span>
                        )}
                      </td>
                      <td className="py-2 px-3 text-right text-gray-900">{'\u20B9'}{item.unitCost.toLocaleString()}</td>
                      <td className="py-2 px-3 text-right text-gray-600">{item.taxRate}%</td>
                      <td className="py-2 px-3 text-right font-medium text-gray-900">{'\u20B9'}{item.total.toLocaleString()}</td>
                      {anyLineCancellable && (
                        <td className="py-2 px-3 text-right">
                          {canCancelLine(item) && (
                            <button
                              type="button"
                              onClick={() => { setReason(''); setCancelTarget({ lineIndex: idx }); }}
                              aria-label={`Cancel line ${item.productName}`}
                              className="px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50 rounded-lg whitespace-nowrap"
                            >
                              Cancel line
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Totals */}
          <div className="flex justify-end">
            <div className="w-64 space-y-2 p-4 bg-gray-50 rounded-lg">
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">Subtotal</span>
                <span className="font-medium text-gray-900">{'\u20B9'}{po.subtotal.toLocaleString()}</span>
              </div>
              {/* The split the server stored, not a single opaque "Tax" line:
                  the same money is a different GST return depending on which
                  one it is. Orders raised before the split was stored still
                  show the plain total. */}
              {po.gstSummary && po.interstate === true ? (
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600">IGST</span>
                  <span className="font-medium text-gray-900">{'\u20B9'}{po.gstSummary.igst.toLocaleString()}</span>
                </div>
              ) : po.gstSummary ? (
                <>
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-600">CGST</span>
                    <span className="font-medium text-gray-900">{'\u20B9'}{po.gstSummary.cgst.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-600">SGST</span>
                    <span className="font-medium text-gray-900">{'\u20B9'}{po.gstSummary.sgst.toLocaleString()}</span>
                  </div>
                </>
              ) : (
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600">Tax</span>
                  <span className="font-medium text-gray-900">{'\u20B9'}{po.taxAmount.toLocaleString()}</span>
                </div>
              )}
              <div className="flex justify-between text-sm font-bold border-t border-gray-300 pt-2">
                <span className="text-gray-900">Total</span>
                <span className="text-gray-900">{'\u20B9'}{po.total.toLocaleString()}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Cancel needs a reason: it goes on the order timeline beside the
            person's name and the time (owner ruling 2026-09-28). */}
        {cancelTarget && (
          <div className="px-6 py-4 border-t border-red-200 bg-red-50 space-y-2">
            <label htmlFor="po-cancel-reason" className="block text-sm font-medium text-red-900">
              {cancelLine
                ? `Why is this line (${cancelLine.productName}) being cancelled?`
                : partReceived
                  ? 'Why is what is still due being cancelled?'
                  : 'Why is this order being cancelled?'}
            </label>
            <textarea
              id="po-cancel-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              className="input-field w-full"
              placeholder="e.g. qty typo, vendor out of stock"
            />
            <p className="text-xs text-red-800">
              {partReceived || (cancelLine && !isDraft)
                ? 'Only what has not arrived is cancelled - stock already received stays. '
                : ''}
              The reason shows on the order timeline with your name and the time.
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={closeCancel}
                className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-white rounded-lg"
              >
                Keep it
              </button>
              <button
                type="button"
                onClick={confirmCancel}
                disabled={busy || reason.trim().length < 3}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg disabled:opacity-50"
              >
                Confirm cancel
              </button>
            </div>
          </div>
        )}

        {/* Footer - Action Buttons */}
        <div className="flex flex-wrap items-center justify-between gap-2 p-6 border-t border-gray-200">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
          >
            Close
          </button>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {/* Print PO (RPT-4) — available for all statuses */}
            <button
              type="button"
              onClick={() => setShowPrint(true)}
              className="px-4 py-2 text-sm font-medium text-gray-700 border border-gray-300 hover:bg-gray-50 rounded-lg transition-colors flex items-center gap-2"
            >
              <Printer className="w-4 h-4" />
              Print PO
            </button>
            {cancellable && (
              <button
                type="button"
                onClick={() => { setReason(''); setCancelTarget({}); }}
                className="px-4 py-2 text-sm font-medium text-red-700 border border-red-200 hover:bg-red-50 rounded-lg transition-colors flex items-center gap-2"
              >
                <Ban className="w-4 h-4" />
                {partReceived ? 'Cancel what is still due' : 'Cancel order'}
              </button>
            )}
            {isDraft && !po.source && (
              <button
                type="button"
                onClick={() => onAction(po, 'edit')}
                className="px-4 py-2 text-sm font-medium text-gray-700 border border-gray-300 hover:bg-gray-50 rounded-lg transition-colors flex items-center gap-2"
              >
                <Pencil className="w-4 h-4" />
                Edit
              </button>
            )}
            {/* No approval step (owner 2026-09-28): a draft goes straight to
                the vendor, and every screen calls it that. */}
            {isDraft && (
              <button
                type="button"
                onClick={() => onAction(po, 'send')}
                className="btn-primary flex items-center gap-2"
              >
                <Send className="w-4 h-4" />
                Send to vendor
              </button>
            )}
            {/* P0-4 (launch gate): the Approve / Mark-as-Ordered /
                Mark-as-Received buttons are REMOVED, not wired. They called
                no API — pure local status theater that evaporated on reload
                and made a manager believe lifecycle steps happened that
                never reached the server. Receiving is done on the real
                receiving screens; wiring approve/order here is a feature,
                not a launch fix. */}
          </div>
        </div>
      </div>
    </div>
    </>
  );
}
