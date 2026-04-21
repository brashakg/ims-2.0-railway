// ============================================================================
// IMS 2.0 - Purchase Orders List
// ============================================================================

import {
  Eye,
  Download,
  Package,
} from 'lucide-react';
import { getStatusBadge } from './statusBadge';
import type { PurchaseOrder } from './purchaseTypes';

interface PurchaseTableProps {
  purchaseOrders: PurchaseOrder[];
  onViewPO: (po: PurchaseOrder) => void;
}

function downloadPO(po: PurchaseOrder) {
  const lines: string[] = [];
  lines.push('PURCHASE ORDER');
  lines.push('==============');
  lines.push(`PO Number : ${po.poNumber}`);
  lines.push(`Supplier  : ${po.supplierName}`);
  lines.push(`Date      : ${new Date(po.date).toLocaleDateString()}`);
  lines.push(`Delivery  : ${new Date(po.expectedDelivery).toLocaleDateString()}`);
  lines.push(`Status    : ${po.status}`);
  lines.push('');
  lines.push('ITEMS');
  lines.push('-----');
  po.items.forEach((item, idx) => {
    lines.push(
      `${idx + 1}. ${item.productName} (SKU: ${item.sku})` +
      ` | Qty: ${item.quantity} | Unit: Rs.${item.unitCost.toLocaleString()}` +
      ` | Total: Rs.${item.total.toLocaleString()}`
    );
  });
  lines.push('');
  lines.push(`Subtotal  : Rs.${po.subtotal.toLocaleString()}`);
  lines.push(`Tax       : Rs.${po.taxAmount.toLocaleString()}`);
  lines.push(`TOTAL     : Rs.${po.total.toLocaleString()}`);
  if (po.notes) {
    lines.push('');
    lines.push(`Notes     : ${po.notes}`);
  }

  const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${po.poNumber}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function PurchaseTable({ purchaseOrders, onViewPO }: PurchaseTableProps) {

  return (
    <div className="space-y-4">
      {purchaseOrders.map((po) => (
        <div key={po.id} className="card hover:shadow-lg transition-shadow">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                <h3 className="text-lg font-semibold text-gray-900">{po.poNumber}</h3>
                {getStatusBadge(po.status)}
              </div>
              <p className="text-sm text-gray-600">{po.supplierName}</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => onViewPO(po)}
                className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <Eye className="w-5 h-5 text-gray-600" />
              </button>
              <button
                onClick={() => downloadPO(po)}
                title="Download PO"
                className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <Download className="w-5 h-5 text-gray-600" />
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4 mb-4">
            <div>
              <p className="text-xs text-gray-600 mb-1">Order Date</p>
              <p className="text-sm font-medium text-gray-900">{new Date(po.date).toLocaleDateString()}</p>
            </div>
            <div>
              <p className="text-xs text-gray-600 mb-1">Expected Delivery</p>
              <p className="text-sm font-medium text-gray-900">{new Date(po.expectedDelivery).toLocaleDateString()}</p>
            </div>
            <div>
              <p className="text-xs text-gray-600 mb-1">Items</p>
              <p className="text-sm font-medium text-gray-900">{po.items.length} products</p>
            </div>
            <div>
              <p className="text-xs text-gray-600 mb-1">Total Amount</p>
              <p className="text-sm font-semibold text-gray-900">{'\u20B9'}{po.total.toLocaleString()}</p>
            </div>
          </div>

          {/* Items Preview */}
          <div className="border-t border-gray-200 pt-3">
            <p className="text-xs text-gray-600 mb-2">Items:</p>
            <div className="space-y-1">
              {po.items.map((item, idx) => (
                <div key={idx} className="flex items-center justify-between text-sm">
                  <span className="text-gray-700">{item.productName} (x{item.quantity})</span>
                  <span className="font-medium text-gray-900">{'\u20B9'}{item.total.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ))}

      {purchaseOrders.length === 0 && (
        <div className="text-center py-12">
          <Package className="w-12 h-12 text-gray-500 mx-auto mb-3" />
          <p className="text-gray-500">No purchase orders found</p>
        </div>
      )}
    </div>
  );
}
