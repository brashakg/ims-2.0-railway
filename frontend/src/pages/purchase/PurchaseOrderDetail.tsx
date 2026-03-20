// ============================================================================
// IMS 2.0 - Purchase Order Detail Modal
// ============================================================================

import {
  FileText,
  CheckCircle,
  X as XIcon,
  Truck,
  Package,
} from 'lucide-react';
import { getStatusBadge } from './statusBadge';
import type { PurchaseOrder } from './purchaseTypes';

interface PurchaseOrderDetailProps {
  po: PurchaseOrder;
  onClose: () => void;
  onAction: (po: PurchaseOrder, action: string) => void;
}

export function PurchaseOrderDetail({ po, onClose, onAction }: PurchaseOrderDetailProps) {
  return (
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
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <XIcon className="w-5 h-5 text-gray-500" />
          </button>
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
                  </tr>
                </thead>
                <tbody>
                  {po.items.map((item, idx) => (
                    <tr key={idx} className="border-b border-gray-100">
                      <td className="py-2 px-3 text-gray-900">{item.productName}</td>
                      <td className="py-2 px-3 text-gray-600">{item.sku}</td>
                      <td className="py-2 px-3 text-right text-gray-900">{item.quantity}</td>
                      <td className="py-2 px-3 text-right text-gray-900">{'\u20B9'}{item.unitCost.toLocaleString()}</td>
                      <td className="py-2 px-3 text-right text-gray-600">{item.taxRate}%</td>
                      <td className="py-2 px-3 text-right font-medium text-gray-900">{'\u20B9'}{item.total.toLocaleString()}</td>
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
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">Tax</span>
                <span className="font-medium text-gray-900">{'\u20B9'}{po.taxAmount.toLocaleString()}</span>
              </div>
              <div className="flex justify-between text-sm font-bold border-t border-gray-300 pt-2">
                <span className="text-gray-900">Total</span>
                <span className="text-gray-900">{'\u20B9'}{po.total.toLocaleString()}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Footer - Action Buttons */}
        <div className="flex items-center justify-between p-6 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
          >
            Close
          </button>
          <div className="flex items-center gap-2">
            {po.status === 'DRAFT' && (
              <button
                onClick={() => onAction(po, 'submit')}
                className="btn-primary flex items-center gap-2"
              >
                <FileText className="w-4 h-4" />
                Submit for Approval
              </button>
            )}
            {po.status === 'PENDING' && (
              <>
                <button
                  onClick={() => onAction(po, 'reject')}
                  className="px-4 py-2 text-sm font-medium text-red-700 bg-red-50 hover:bg-red-100 rounded-lg transition-colors flex items-center gap-2"
                >
                  <XIcon className="w-4 h-4" />
                  Reject
                </button>
                <button
                  onClick={() => onAction(po, 'approve')}
                  className="btn-primary flex items-center gap-2"
                >
                  <CheckCircle className="w-4 h-4" />
                  Approve
                </button>
              </>
            )}
            {po.status === 'APPROVED' && (
              <button
                onClick={() => onAction(po, 'order')}
                className="btn-primary flex items-center gap-2"
              >
                <Truck className="w-4 h-4" />
                Mark as Ordered
              </button>
            )}
            {po.status === 'ORDERED' && (
              <button
                onClick={() => onAction(po, 'receive')}
                className="btn-primary flex items-center gap-2"
              >
                <Package className="w-4 h-4" />
                Mark as Received
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
