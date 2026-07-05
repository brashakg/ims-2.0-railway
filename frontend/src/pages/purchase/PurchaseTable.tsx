// ============================================================================
// IMS 2.0 - Purchase Orders List
// ============================================================================

import { useState } from 'react';
import {
  Eye,
  Download,
  Package,
  Truck,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { getStatusBadge } from './statusBadge';
import { PurchaseStatusChip } from '../../components/purchase/PurchaseStatusChip';
import { POLifecycleDrawer } from '../../components/purchase/POLifecycleDrawer';
import { useAuth } from '../../context/AuthContext';
import { RECEIVABLE_PO_STATUSES } from './purchaseTypes';
import type { PurchaseOrder } from './purchaseTypes';

interface PurchaseTableProps {
  purchaseOrders: PurchaseOrder[];
  onViewPO: (po: PurchaseOrder) => void;
}

// Receiving progress for a PO: how many lines are FULLY received.
// receivedQty comes from the per-line received_qty (header fallback mapped in
// PurchaseManagementPage). Lines with no ordered qty are ignored.
function receivingProgress(po: PurchaseOrder): { received: number; total: number } {
  const lines = po.items.filter((i) => (i.quantity ?? 0) > 0);
  const received = lines.filter((i) => (i.receivedQty ?? 0) >= i.quantity).length;
  return { received, total: lines.length };
}

// Statuses where receiving is in play (receivable now, or already done) —
// the progress chip is only meaningful once the PO has gone to the vendor.
const RECEIVING_VISIBLE_STATUSES = new Set([...RECEIVABLE_PO_STATUSES, 'RECEIVED']);

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
  const navigate = useNavigate();
  const { hasRole } = useAuth();
  // Mirrors the /purchase/receive ProtectedRoute gate in App.tsx so a role
  // is never handed a button that lands on /unauthorized. Phase 2: ACCOUNTANT
  // added — express receive is for ALL receiving roles (backend gate matches).
  const canReceive = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);
  // Phase 2: clicking the PO number opens the lifecycle drawer (timeline +
  // GRNs + invoices + one derived next-step action).
  const [timelinePO, setTimelinePO] = useState<PurchaseOrder | null>(null);

  return (
    <div className="space-y-4">
      {purchaseOrders.map((po) => {
        const receivable = RECEIVABLE_PO_STATUSES.includes(po.status);
        const showReceiving = RECEIVING_VISIBLE_STATUSES.has(po.status);
        const progress = showReceiving ? receivingProgress(po) : null;
        return (
        <div key={po.id} className="card hover:shadow-lg transition-shadow">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <div className="flex items-center gap-3 mb-2">
                {/* PO number opens the lifecycle drawer (keyboard accessible). */}
                <h3 className="text-lg font-semibold">
                  <button
                    type="button"
                    onClick={() => setTimelinePO(po)}
                    title="View PO lifecycle timeline"
                    className="text-gray-900 hover:text-blue-700 hover:underline underline-offset-2 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                  >
                    {po.poNumber}
                  </button>
                </h3>
                {getStatusBadge(po.status)}
              </div>
              <p className="text-sm text-gray-600">{po.supplierName}</p>
              {/* Receiving column (Phase 1): owner 5-word vocabulary chip +
                  fully-received line count, once the PO is with the vendor. */}
              {progress && (
                <div className="flex items-center gap-2 mt-1.5">
                  <PurchaseStatusChip status={po.status} />
                  <span className="text-xs text-gray-500">
                    {progress.received} of {progress.total} line{progress.total === 1 ? '' : 's'} received
                  </span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {receivable && canReceive && (
                <button
                  onClick={() =>
                    navigate(
                      `/purchase/receive?vendor_id=${encodeURIComponent(po.supplierId)}&po_id=${encodeURIComponent(po.id)}`,
                    )
                  }
                  title="Receive goods against this PO"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
                >
                  <Truck className="w-4 h-4" />
                  Receive
                </button>
              )}
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
        );
      })}

      {purchaseOrders.length === 0 && (
        <div className="text-center py-12">
          <Package className="w-12 h-12 text-gray-500 mx-auto mb-3" />
          <p className="text-gray-500">No purchase orders found</p>
        </div>
      )}

      {/* PO lifecycle drawer — "Send to vendor" (DRAFT) routes through the
          existing send path: close the drawer and open the PO detail modal,
          where the submit/send action lives. */}
      {timelinePO && (
        <POLifecycleDrawer
          poId={timelinePO.id}
          poNumber={timelinePO.poNumber}
          onClose={() => setTimelinePO(null)}
          onSendToVendor={() => {
            const po = timelinePO;
            setTimelinePO(null);
            onViewPO(po);
          }}
        />
      )}
    </div>
  );
}
