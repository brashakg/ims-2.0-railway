import { Minus, Plus, Trash2, User, Zap } from 'lucide-react';
import type { CartItem, Customer } from './POSLayout';
import { useState } from 'react';
import clsx from 'clsx';

interface CartPanelProps {
  items: CartItem[];
  selectedCustomer: Customer | null;
  onSetCustomer: (customer: Customer | null) => void;
  onUpdateQuantity: (productId: string, quantity: number) => void;
  onRemoveItem: (productId: string) => void;
  onApplyDiscount: (productId: string, discountPercent: number) => void;
  onProceedToBilling: () => void;
  totalItems: number;
  totalAmount: number;
}

const MOCK_CUSTOMERS: Customer[] = [
  { id: 'c1', name: 'Raj Kumar', phone: '9876543210', email: 'raj@example.com', loyalty_points: 5000 },
  { id: 'c2', name: 'Priya Sharma', phone: '9876543211', email: 'priya@example.com', loyalty_points: 8500 },
  { id: 'c3', name: 'Amit Patel', phone: '9876543212', email: 'amit@example.com', loyalty_points: 3200 },
];

export function CartPanel({
  items,
  selectedCustomer,
  onSetCustomer,
  onUpdateQuantity,
  onRemoveItem,
  onProceedToBilling,
  totalItems,
  totalAmount,
}: CartPanelProps) {
  const [showCustomerSelect, setShowCustomerSelect] = useState(false);

  return (
    <div className="bg-gray-800 h-full flex flex-col">
      {/* Header */}
      <div className="bg-gray-900 border-b border-gray-700 px-4 py-4">
        <h2 className="text-lg font-bold text-white">Shopping Cart</h2>
        <p className="text-sm text-gray-400">{totalItems} items</p>
      </div>

      {/* Customer Section */}
      <div className="px-4 py-4 border-b border-gray-700">
        {selectedCustomer ? (
          <div className="bg-blue-900 rounded-lg p-3 flex items-start justify-between">
            <div>
              <p className="font-semibold text-white text-sm">{selectedCustomer.name}</p>
              <p className="text-xs text-gray-300">{selectedCustomer.phone}</p>
              {selectedCustomer.loyalty_points && (
                <p className="text-xs text-blue-300 mt-1">
                  <Zap className="w-3 h-3 inline mr-1" />
                  {selectedCustomer.loyalty_points} points
                </p>
              )}
            </div>
            <button
              onClick={() => onSetCustomer(null)}
              className="text-red-400 hover:text-red-300 text-xs"
            >
              Clear
            </button>
          </div>
        ) : (
          <button
            onClick={() => setShowCustomerSelect(!showCustomerSelect)}
            className="w-full bg-gray-700 hover:bg-gray-600 text-white py-2 rounded-lg text-sm font-medium flex items-center justify-center gap-2 transition-colors"
          >
            <User className="w-4 h-4" />
            Select Customer
          </button>
        )}

        {/* Customer Dropdown */}
        {showCustomerSelect && !selectedCustomer && (
          <div className="mt-3 space-y-2 max-h-40 overflow-y-auto">
            {MOCK_CUSTOMERS.map(customer => (
              <button
                key={customer.id}
                onClick={() => {
                  onSetCustomer(customer);
                  setShowCustomerSelect(false);
                }}
                className="w-full text-left bg-gray-700 hover:bg-gray-600 p-2 rounded text-sm text-white transition-colors"
              >
                <p className="font-medium">{customer.name}</p>
                <p className="text-xs text-gray-300">{customer.phone}</p>
              </button>
            ))}
            <button
              onClick={() => {
                setShowCustomerSelect(false);
              }}
              className="w-full text-left bg-green-900 hover:bg-green-800 p-2 rounded text-sm text-green-300 transition-colors"
            >
              + Add New Customer
            </button>
          </div>
        )}
      </div>

      {/* Cart Items */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-gray-400">
            <p className="text-sm">Cart is empty</p>
            <p className="text-xs">Add items to get started</p>
          </div>
        ) : (
          items.map(item => (
            <div key={item.product_id} className="bg-gray-700 rounded-lg p-3 space-y-2">
              {/* Item Header */}
              <div className="flex justify-between items-start">
                <div className="flex-1">
                  <p className="font-semibold text-white text-sm">{item.name}</p>
                  <p className="text-xs text-gray-400">{item.brand} • {item.sku}</p>
                </div>
                <button
                  onClick={() => onRemoveItem(item.product_id)}
                  className="text-red-400 hover:text-red-300 ml-2"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>

              {/* Quantity Controls */}
              <div className="flex items-center justify-between bg-gray-800 rounded px-2 py-1">
                <button
                  onClick={() => onUpdateQuantity(item.product_id, item.quantity - 1)}
                  className="text-gray-400 hover:text-white"
                >
                  <Minus className="w-4 h-4" />
                </button>
                <span className="text-white font-medium text-sm min-w-8 text-center">
                  {item.quantity}
                </span>
                <button
                  onClick={() => onUpdateQuantity(item.product_id, item.quantity + 1)}
                  className="text-gray-400 hover:text-white"
                >
                  <Plus className="w-4 h-4" />
                </button>
              </div>

              {/* Price */}
              <div className="flex justify-between items-center text-sm">
                <span className="text-gray-300">
                  ₹{item.unit_price.toLocaleString('en-IN')} × {item.quantity}
                </span>
                <span className="text-green-500 font-semibold">
                  ₹{(item.unit_price * item.quantity).toLocaleString('en-IN')}
                </span>
              </div>

              {/* Discount Badge */}
              {item.discount_percent && (
                <div className="text-xs bg-amber-900 text-amber-300 px-2 py-1 rounded">
                  {item.discount_percent}% discount applied
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Totals & Action Buttons */}
      <div className="border-t border-gray-700 px-4 py-4 space-y-4">
        {/* GST Breakdown */}
        {items.length > 0 && (
          <div className="bg-gray-700 rounded-lg p-3 space-y-2 text-xs">
            <p className="font-semibold text-gray-300 flex items-center gap-2">
              <Zap className="w-3 h-3 text-yellow-400" />
              GST Breakdown (18% Standard)
            </p>
            {(() => {
              const gstRate = 0.18;
              const cgstAmount = totalAmount * (gstRate / 2) / (1 + gstRate);
              const sgstAmount = totalAmount * (gstRate / 2) / (1 + gstRate);
              const subtotal = totalAmount / (1 + gstRate);
              return (
                <>
                  <div className="flex justify-between text-gray-300">
                    <span>Subtotal (excl. GST)</span>
                    <span>₹{subtotal.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between text-blue-300">
                    <span>CGST (9%)</span>
                    <span>₹{cgstAmount.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between text-blue-300">
                    <span>SGST (9%)</span>
                    <span>₹{sgstAmount.toFixed(2)}</span>
                  </div>
                </>
              );
            })()}
          </div>
        )}

        {/* Summary */}
        <div className="space-y-2 text-sm">
          <div className="flex justify-between text-gray-300">
            <span>Subtotal</span>
            <span>₹{totalAmount.toFixed(2)}</span>
          </div>
          <div className="flex justify-between text-gray-300">
            <span>Items</span>
            <span>{totalItems}</span>
          </div>
          <div className="border-t border-gray-700 pt-2 flex justify-between text-white font-bold text-base">
            <span>Total</span>
            <span className="text-green-500">₹{totalAmount.toFixed(2)}</span>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="space-y-2">
          <button
            onClick={onProceedToBilling}
            disabled={items.length === 0}
            className={clsx(
              'w-full py-3 rounded-lg font-semibold transition-colors flex items-center justify-center gap-2',
              items.length === 0
                ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                : 'bg-green-600 hover:bg-green-700 text-white'
            )}
          >
            Proceed to Billing
          </button>
          <button className="w-full py-2 rounded-lg font-medium bg-gray-700 hover:bg-gray-600 text-white transition-colors text-sm">
            Hold Bill
          </button>
        </div>
      </div>
    </div>
  );
}

export default CartPanel;
