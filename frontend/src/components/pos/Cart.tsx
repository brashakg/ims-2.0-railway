// ============================================================================
// IMS 2.0 - Cart Component
// ============================================================================

import { Trash2, Plus, Minus, Percent, AlertTriangle, FileText } from 'lucide-react';
import type { CartItem } from '../../types';
import clsx from 'clsx';

interface CartProps {
  items: CartItem[];
  onRemoveItem: (itemId: string) => void;
  onUpdateQuantity: (itemId: string, quantity: number) => void;
  onApplyDiscount: (itemId: string) => void;
}

export function Cart({ items, onRemoveItem, onUpdateQuantity, onApplyDiscount }: CartProps) {
  if (items.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-gray-400">
        <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mb-3">
          <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z" />
          </svg>
        </div>
        <p className="text-sm">Cart is empty</p>
        <p className="text-xs mt-1">Add products to start</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {items.map((item) => (
        <div
          key={item.id}
          className={clsx(
            'p-3 border rounded-lg transition-colors',
            item.requiresPrescription && !item.prescriptionLinked
              ? 'border-yellow-300 bg-yellow-50'
              : 'border-gray-200 bg-white'
          )}
        >
          {/* Item Header */}
          <div className="flex items-start justify-between gap-2 mb-2">
            <div className="flex-1 min-w-0">
              <p className="font-medium text-gray-900 text-sm truncate">{item.productName}</p>
              <p className="text-xs text-gray-500">{item.sku}</p>
            </div>
            <button
              onClick={() => onRemoveItem(item.id)}
              className="p-1 text-gray-400 hover:text-red-500 transition-colors"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>

          {/* Prescription Warning */}
          {item.requiresPrescription && !item.prescriptionLinked && (
            <div className="flex items-center gap-2 mb-2 text-yellow-700 text-xs">
              <AlertTriangle className="w-4 h-4" />
              <span>Prescription required</span>
            </div>
          )}

          {/* Prescription Linked Badge */}
          {item.requiresPrescription && item.prescriptionLinked && (
            <div className="flex items-center gap-1 mb-2 text-green-600 text-xs">
              <FileText className="w-3 h-3" />
              <span>Rx linked</span>
            </div>
          )}

          {/* Quantity & Price */}
          <div className="flex items-center justify-between">
            {/* Quantity Controls */}
            <div className="flex items-center gap-1">
              <button
                onClick={() => onUpdateQuantity(item.id, item.quantity - 1)}
                disabled={item.quantity <= 1}
                className="w-7 h-7 flex items-center justify-center rounded border border-gray-300 text-gray-600 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Minus className="w-3 h-3" />
              </button>
              <span className="w-8 text-center text-sm font-medium">{item.quantity}</span>
              <button
                onClick={() => onUpdateQuantity(item.id, item.quantity + 1)}
                className="w-7 h-7 flex items-center justify-center rounded border border-gray-300 text-gray-600 hover:bg-gray-100"
              >
                <Plus className="w-3 h-3" />
              </button>
            </div>

            {/* Price & Discount */}
            <div className="flex items-center gap-2">
              <button
                onClick={() => onApplyDiscount(item.id)}
                className={clsx(
                  'p-1.5 rounded transition-colors',
                  item.discountAmount > 0
                    ? 'bg-green-100 text-green-600'
                    : 'text-gray-400 hover:text-bv-red-600 hover:bg-bv-red-50'
                )}
                title="Apply discount"
              >
                <Percent className="w-4 h-4" />
              </button>

              <div className="text-right">
                <p className="font-bold text-gray-900">
                  ₹{item.finalPrice.toLocaleString('en-IN')}
                </p>
                {item.discountAmount > 0 && (
                  <p className="text-xs text-green-600">
                    -{item.discountPercent}% (-₹{item.discountAmount.toLocaleString('en-IN')})
                  </p>
                )}
                {item.quantity > 1 && item.discountAmount === 0 && (
                  <p className="text-xs text-gray-500">
                    {item.quantity} × ₹{item.unitPrice.toLocaleString('en-IN')}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      ))}

      {/* Items Count */}
      <p className="text-xs text-gray-500 text-center pt-2">
        {items.length} item{items.length !== 1 ? 's' : ''} in cart
      </p>
    </div>
  );
}

export default Cart;
