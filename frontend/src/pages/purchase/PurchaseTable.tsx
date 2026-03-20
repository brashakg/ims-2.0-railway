// ============================================================================
// IMS 2.0 - Purchase Orders List
// ============================================================================

import {
  Eye,
  Download,
  Package,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { getStatusBadge } from './statusBadge';
import type { PurchaseOrder } from './purchaseTypes';

interface PurchaseTableProps {
  purchaseOrders: PurchaseOrder[];
  onViewPO: (po: PurchaseOrder) => void;
}

export function PurchaseTable({ purchaseOrders, onViewPO }: PurchaseTableProps) {
  const toast = useToast();

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
                onClick={() => toast.info('PO download coming soon')}
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
          <Package className="w-12 h-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-500">No purchase orders found</p>
        </div>
      )}
    </div>
  );
}
