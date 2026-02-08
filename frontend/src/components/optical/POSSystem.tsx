// ============================================================================
// IMS 2.0 - POS System
// ============================================================================
// Point of Sale system for optical retail with real-time sales and payments

import { useState } from 'react';
import { Plus, Trash2, ShoppingCart, DollarSign, Check, X } from 'lucide-react';
import clsx from 'clsx';

export interface CartItem {
  id: string;
  type: 'frame' | 'lens' | 'coating' | 'service';
  name: string;
  quantity: number;
  unitPrice: number;
  discountPercent?: number;
}

export interface Payment {
  method: 'cash' | 'card' | 'check' | 'insurance';
  amount: number;
  reference?: string;
  status: 'pending' | 'completed' | 'failed';
}

export interface Sale {
  id: string;
  invoiceNumber: string;
  customerId?: string;
  customerName: string;
  items: CartItem[];
  subtotal: number;
  discountPercent: number;
  tax: number;
  total: number;
  payments: Payment[];
  paymentStatus: 'pending' | 'completed' | 'partially-paid';
  notes?: string;
  createdAt: string;
  status: 'draft' | 'completed' | 'cancelled';
}

interface POSSystemProps {
  sales: Sale[];
  onCreateSale: (sale: Sale) => Promise<void>;
  loadingInvoices?: boolean;
}

export function POSSystem({
  sales,
  onCreateSale,
  loadingInvoices = false,
}: POSSystemProps) {
  const [showCheckout, setShowCheckout] = useState(false);
  const [cart, setCart] = useState<CartItem[]>([]);
  const [customerName, setCustomerName] = useState('');
  const [discountPercent, setDiscountPercent] = useState(0);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [selectedPaymentMethod, setSelectedPaymentMethod] = useState<'cash' | 'card' | 'check' | 'insurance'>('cash');
  const [paymentAmount, setPaymentAmount] = useState('');
  const [saleNotes, setSaleNotes] = useState('');

  const subtotal = cart.reduce((sum, item) => sum + item.unitPrice * item.quantity, 0);
  const discountAmount = subtotal * (discountPercent / 100);
  const subtotalAfterDiscount = subtotal - discountAmount;
  const tax = subtotalAfterDiscount * 0.1; // 10% tax
  const total = subtotalAfterDiscount + tax;

  const handleAddItem = () => {
    // Placeholder - would integrate with frame/lens selection
    const newItem: CartItem = {
      id: Date.now().toString(),
      type: 'frame',
      name: 'Sample Frame',
      quantity: 1,
      unitPrice: 150,
    };
    setCart([...cart, newItem]);
  };

  const handleRemoveItem = (id: string) => {
    setCart(cart.filter(item => item.id !== id));
  };

  const handleUpdateQuantity = (id: string, quantity: number) => {
    if (quantity <= 0) {
      handleRemoveItem(id);
      return;
    }
    setCart(cart.map(item => item.id === id ? { ...item, quantity } : item));
  };

  const handleAddPayment = () => {
    if (!paymentAmount || parseFloat(paymentAmount) <= 0) {
      alert('Please enter a valid payment amount');
      return;
    }

    const payment: Payment = {
      method: selectedPaymentMethod,
      amount: parseFloat(paymentAmount),
      status: selectedPaymentMethod === 'cash' ? 'completed' : 'pending',
    };

    setPayments([...payments, payment]);
    setPaymentAmount('');
  };

  const handleRemovePayment = (index: number) => {
    setPayments(payments.filter((_, i) => i !== index));
  };

  const handleCompleteSale = async () => {
    if (cart.length === 0) {
      alert('Add items to cart before completing sale');
      return;
    }

    if (!customerName.trim()) {
      alert('Please enter customer name');
      return;
    }

    const paidAmount = payments.reduce((sum, p) => sum + p.amount, 0);
    if (paidAmount < total) {
      alert(`Payment short by $${(total - paidAmount).toFixed(2)}`);
      return;
    }

    const invoiceNumber = `INV-${Date.now()}`;
    const sale: Sale = {
      id: Date.now().toString(),
      invoiceNumber,
      customerName,
      items: cart,
      subtotal,
      discountPercent,
      tax,
      total,
      payments,
      paymentStatus: paidAmount >= total ? 'completed' : 'partially-paid',
      notes: saleNotes,
      createdAt: new Date().toISOString(),
      status: 'completed',
    };

    await Promise.resolve(onCreateSale(sale));

    // Reset form
    setCart([]);
    setCustomerName('');
    setDiscountPercent(0);
    setPayments([]);
    setSaleNotes('');
    setShowCheckout(false);
  };

  const paidAmount = payments.reduce((sum, p) => sum + p.amount, 0);
  const remainingBalance = total - paidAmount;

  return (
    <div className="space-y-4">
      {/* POS Display */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2 bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-6">
          <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <ShoppingCart className="w-5 h-5" />
            Cart
          </h2>

          {cart.length === 0 ? (
            <div className="text-center py-8 text-gray-500 dark:text-gray-400">
              <ShoppingCart className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p>No items in cart</p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="text-sm font-medium text-gray-700 dark:text-gray-300 grid grid-cols-5 gap-2 pb-2 border-b border-gray-200 dark:border-gray-700">
                <div>Item</div>
                <div className="text-center">Qty</div>
                <div className="text-right">Price</div>
                <div className="text-right">Total</div>
                <div className="text-center">Action</div>
              </div>
              {cart.map(item => (
                <div key={item.id} className="grid grid-cols-5 gap-2 items-center py-2 border-b border-gray-100 dark:border-gray-700 last:border-0">
                  <div className="text-sm text-gray-900 dark:text-white truncate">{item.name}</div>
                  <input
                    type="number"
                    min="1"
                    value={item.quantity}
                    onChange={e => handleUpdateQuantity(item.id, parseInt(e.target.value))}
                    className="px-2 py-1 border border-gray-300 dark:border-gray-700 rounded text-sm text-center bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                  <div className="text-sm text-right text-gray-600 dark:text-gray-400">
                    ${item.unitPrice.toFixed(2)}
                  </div>
                  <div className="text-sm text-right font-semibold text-gray-900 dark:text-white">
                    ${(item.quantity * item.unitPrice).toFixed(2)}
                  </div>
                  <button
                    onClick={() => handleRemoveItem(item.id)}
                    className="p-1 hover:bg-red-100 dark:hover:bg-red-900/20 rounded text-red-600 dark:text-red-400"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
            </div>
          )}

          <button
            onClick={handleAddItem}
            className="w-full mt-4 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center justify-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Add Item
          </button>
        </div>

        {/* Summary */}
        <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800 p-6">
          <h3 className="font-bold text-gray-900 dark:text-white mb-4">Sale Summary</h3>

          <div className="space-y-2 text-sm mb-4 pb-4 border-b border-gray-300 dark:border-gray-700">
            <div className="flex justify-between text-gray-700 dark:text-gray-300">
              <span>Subtotal:</span>
              <span>${subtotal.toFixed(2)}</span>
            </div>

            {discountPercent > 0 && (
              <div className="flex justify-between text-gray-700 dark:text-gray-300">
                <span>Discount ({discountPercent}%):</span>
                <span>-${discountAmount.toFixed(2)}</span>
              </div>
            )}

            <div className="flex justify-between text-gray-700 dark:text-gray-300">
              <span>Tax (10%):</span>
              <span>${tax.toFixed(2)}</span>
            </div>
          </div>

          <div className="flex justify-between items-center mb-6">
            <span className="font-bold text-gray-900 dark:text-white text-lg">Total:</span>
            <span className="font-bold text-blue-600 dark:text-blue-400 text-2xl">${total.toFixed(2)}</span>
          </div>

          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Discount %
              </label>
              <input
                type="number"
                min="0"
                max="100"
                value={discountPercent}
                onChange={e => setDiscountPercent(Math.min(100, Math.max(0, parseInt(e.target.value) || 0)))}
                className="w-full px-3 py-1 border border-gray-300 dark:border-gray-700 rounded text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                Customer Name *
              </label>
              <input
                type="text"
                value={customerName}
                onChange={e => setCustomerName(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
            </div>

            <button
              onClick={() => setShowCheckout(!showCheckout)}
              className={clsx(
                'w-full px-4 py-2 rounded-lg font-medium transition-colors',
                showCheckout
                  ? 'bg-red-600 text-white hover:bg-red-700'
                  : 'bg-blue-600 text-white hover:bg-blue-700'
              )}
            >
              {showCheckout ? 'Hide Checkout' : 'Proceed to Checkout'}
            </button>
          </div>
        </div>
      </div>

      {/* Checkout Section */}
      {showCheckout && (
        <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-6">
          <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4 flex items-center gap-2">
            <DollarSign className="w-5 h-5" />
            Checkout
          </h2>

          <div className="grid grid-cols-2 gap-6">
            {/* Payments */}
            <div>
              <h3 className="font-semibold text-gray-900 dark:text-white mb-3">Payments</h3>

              <div className="space-y-3 mb-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Method
                  </label>
                  <select
                    value={selectedPaymentMethod}
                    onChange={e => setSelectedPaymentMethod(e.target.value as any)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  >
                    <option value="cash">Cash</option>
                    <option value="card">Card</option>
                    <option value="check">Check</option>
                    <option value="insurance">Insurance</option>
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Amount
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    value={paymentAmount}
                    onChange={e => setPaymentAmount(e.target.value)}
                    placeholder="0.00"
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
                  />
                </div>

                <button
                  onClick={handleAddPayment}
                  className="w-full px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 font-medium"
                >
                  Add Payment
                </button>
              </div>

              {payments.length > 0 && (
                <div className="space-y-2">
                  {payments.map((payment, index) => (
                    <div key={index} className="flex items-center justify-between p-2 bg-gray-50 dark:bg-gray-800 rounded">
                      <div className="text-sm text-gray-700 dark:text-gray-300">
                        <span className="font-medium capitalize">{payment.method}</span>: ${payment.amount.toFixed(2)}
                      </div>
                      <button
                        onClick={() => handleRemovePayment(index)}
                        className="p-1 hover:bg-red-100 dark:hover:bg-red-900/20 rounded text-red-600 dark:text-red-400"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Summary */}
            <div>
              <h3 className="font-semibold text-gray-900 dark:text-white mb-3">Payment Summary</h3>

              <div className="space-y-2 text-sm mb-4 p-4 bg-gray-50 dark:bg-gray-800 rounded">
                <div className="flex justify-between text-gray-700 dark:text-gray-300">
                  <span>Total Amount:</span>
                  <span className="font-semibold">${total.toFixed(2)}</span>
                </div>
                <div className="flex justify-between text-gray-700 dark:text-gray-300">
                  <span>Paid Amount:</span>
                  <span className="font-semibold">${paidAmount.toFixed(2)}</span>
                </div>
                <div className={clsx(
                  'flex justify-between text-sm font-semibold',
                  remainingBalance > 0 ? 'text-orange-600 dark:text-orange-400' : 'text-green-600 dark:text-green-400'
                )}>
                  <span>{remainingBalance > 0 ? 'Balance Due:' : 'Change Due:'}</span>
                  <span>${Math.abs(remainingBalance).toFixed(2)}</span>
                </div>
              </div>

              <textarea
                placeholder="Sale notes (optional)"
                value={saleNotes}
                onChange={e => setSaleNotes(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white mb-4"
                rows={3}
              />

              <button
                onClick={handleCompleteSale}
                disabled={remainingBalance > 0 || cart.length === 0}
                className={clsx(
                  'w-full px-4 py-2 rounded-lg font-medium flex items-center justify-center gap-2 transition-colors',
                  remainingBalance > 0 || cart.length === 0
                    ? 'bg-gray-400 text-gray-200 cursor-not-allowed'
                    : 'bg-green-600 text-white hover:bg-green-700'
                )}
              >
                <Check className="w-4 h-4" />
                Complete Sale
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Recent Sales */}
      {!loadingInvoices && sales.length > 0 && (
        <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-6">
          <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">Recent Sales</h2>
          <div className="space-y-2">
            {sales.slice(-5).reverse().map(sale => (
              <div key={sale.id} className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800 rounded">
                <div className="text-sm">
                  <p className="font-medium text-gray-900 dark:text-white">{sale.invoiceNumber}</p>
                  <p className="text-xs text-gray-600 dark:text-gray-400">{sale.customerName} - {new Date(sale.createdAt).toLocaleString()}</p>
                </div>
                <span className="font-semibold text-gray-900 dark:text-white">${sale.total.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
