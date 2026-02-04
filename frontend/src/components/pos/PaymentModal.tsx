// ============================================================================
// IMS 2.0 - Payment Modal Component
// ============================================================================
// Supports multi-tender payments: Cash, UPI, Card, Bank Transfer

import { useState } from 'react';
import {
  X,
  CreditCard,
  Banknote,
  Smartphone,
  Building2,
  Trash2,
  CheckCircle,
  AlertCircle,
} from 'lucide-react';
import type { Payment, PaymentMode } from '../../types';
import clsx from 'clsx';

interface PaymentModalProps {
  grandTotal: number;
  amountPaid: number;
  balanceDue: number;
  payments: Payment[];
  onAddPayment: (payment: Omit<Payment, 'id' | 'paidAt'>) => void;
  onRemovePayment: (paymentId: string) => void;
  onComplete: () => void;
  onClose: () => void;
}

// Payment mode configuration
const PAYMENT_MODES: {
  mode: PaymentMode;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
}[] = [
  { mode: 'CASH', label: 'Cash', icon: Banknote, color: 'bg-green-100 text-green-600' },
  { mode: 'UPI', label: 'UPI', icon: Smartphone, color: 'bg-purple-100 text-purple-600' },
  { mode: 'CARD', label: 'Card', icon: CreditCard, color: 'bg-blue-100 text-blue-600' },
  { mode: 'BANK_TRANSFER', label: 'Bank Transfer', icon: Building2, color: 'bg-orange-100 text-orange-600' },
];

// Quick amount options
const QUICK_AMOUNTS = [100, 500, 1000, 2000, 5000];

export function PaymentModal({
  grandTotal,
  amountPaid,
  balanceDue,
  payments,
  onAddPayment,
  onRemovePayment,
  onComplete,
  onClose,
}: PaymentModalProps) {
  const [selectedMode, setSelectedMode] = useState<PaymentMode>('CASH');
  const [amount, setAmount] = useState<string>(balanceDue.toString());
  const [reference, setReference] = useState('');

  const handleAddPayment = () => {
    const paymentAmount = parseFloat(amount);
    if (isNaN(paymentAmount) || paymentAmount <= 0) return;

    onAddPayment({
      mode: selectedMode,
      amount: paymentAmount,
      reference: reference || undefined,
    });

    // Reset form
    setAmount('');
    setReference('');

    // Update amount to remaining balance
    const newBalance = balanceDue - paymentAmount;
    if (newBalance > 0) {
      setAmount(newBalance.toString());
    }
  };

  const handleQuickAmount = (quickAmount: number) => {
    setAmount(quickAmount.toString());
  };

  const handlePayExact = () => {
    setAmount(balanceDue.toString());
  };

  const isPaymentComplete = balanceDue <= 0;
  const canAddPayment = parseFloat(amount) > 0 && !isNaN(parseFloat(amount));

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-bv-red-100 rounded-full flex items-center justify-center">
              <CreditCard className="w-5 h-5 text-bv-red-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Payment</h2>
              <p className="text-sm text-gray-500">
                Total: ₹{grandTotal.toLocaleString('en-IN')}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-6">
            {/* Left - Payment Entry */}
            <div>
              {/* Payment Mode Selection */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Payment Method
                </label>
                <div className="grid grid-cols-2 gap-2">
                  {PAYMENT_MODES.map(({ mode, label, icon: Icon, color }) => (
                    <button
                      key={mode}
                      onClick={() => setSelectedMode(mode)}
                      className={clsx(
                        'flex items-center gap-2 p-3 rounded-lg border transition-colors',
                        selectedMode === mode
                          ? 'border-bv-red-500 bg-bv-red-50'
                          : 'border-gray-200 hover:border-gray-300'
                      )}
                    >
                      <div className={clsx('w-8 h-8 rounded-lg flex items-center justify-center', color)}>
                        <Icon className="w-4 h-4" />
                      </div>
                      <span className="font-medium text-sm">{label}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Amount Input */}
              <div className="mb-4">
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  Amount
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 font-medium">
                    ₹
                  </span>
                  <input
                    type="number"
                    value={amount}
                    onChange={e => setAmount(e.target.value)}
                    className="input-field pl-8 text-lg font-bold"
                    placeholder="0.00"
                    min="0"
                    step="0.01"
                  />
                </div>

                {/* Quick Amount Buttons */}
                <div className="flex flex-wrap gap-2 mt-2">
                  {QUICK_AMOUNTS.map(quickAmount => (
                    <button
                      key={quickAmount}
                      onClick={() => handleQuickAmount(quickAmount)}
                      className="px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
                    >
                      +₹{quickAmount.toLocaleString('en-IN')}
                    </button>
                  ))}
                  <button
                    onClick={handlePayExact}
                    className="px-3 py-1.5 text-sm bg-bv-red-100 text-bv-red-600 hover:bg-bv-red-200 rounded-lg transition-colors font-medium"
                  >
                    Exact
                  </button>
                </div>
              </div>

              {/* Reference (for UPI/Card/Bank) */}
              {selectedMode !== 'CASH' && (
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {selectedMode === 'UPI' && 'UPI Transaction ID'}
                    {selectedMode === 'CARD' && 'Card Last 4 Digits / Auth Code'}
                    {selectedMode === 'BANK_TRANSFER' && 'Transfer Reference'}
                  </label>
                  <input
                    type="text"
                    value={reference}
                    onChange={e => setReference(e.target.value)}
                    className="input-field"
                    placeholder={
                      selectedMode === 'UPI' ? 'e.g., 123456789012' :
                      selectedMode === 'CARD' ? 'e.g., 4242 / A12345' :
                      'e.g., NEFT123456'
                    }
                  />
                </div>
              )}

              {/* Add Payment Button */}
              <button
                onClick={handleAddPayment}
                disabled={!canAddPayment || isPaymentComplete}
                className="btn-primary w-full py-3"
              >
                Add Payment
              </button>
            </div>

            {/* Right - Payment Summary */}
            <div>
              {/* Summary Card */}
              <div className="bg-gray-50 rounded-lg p-4 mb-4">
                <div className="space-y-3">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-600">Grand Total</span>
                    <span className="font-bold">₹{grandTotal.toLocaleString('en-IN')}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-600">Amount Paid</span>
                    <span className="font-medium text-green-600">
                      ₹{amountPaid.toLocaleString('en-IN')}
                    </span>
                  </div>
                  <div className="border-t border-gray-200 pt-3 flex justify-between">
                    <span className="font-medium text-gray-900">Balance Due</span>
                    <span className={clsx(
                      'font-bold text-lg',
                      balanceDue > 0 ? 'text-red-600' : 'text-green-600'
                    )}>
                      {balanceDue > 0 ? `₹${balanceDue.toLocaleString('en-IN')}` : 'PAID'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Payment List */}
              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-2">Payments</h3>
                {payments.length === 0 ? (
                  <div className="text-center py-6 text-gray-400 text-sm">
                    No payments added yet
                  </div>
                ) : (
                  <div className="space-y-2">
                    {payments.map(payment => {
                      const modeConfig = PAYMENT_MODES.find(m => m.mode === payment.mode);
                      const Icon = modeConfig?.icon || Banknote;
                      return (
                        <div
                          key={payment.id}
                          className="flex items-center justify-between p-3 bg-white border border-gray-200 rounded-lg"
                        >
                          <div className="flex items-center gap-3">
                            <div className={clsx(
                              'w-8 h-8 rounded-lg flex items-center justify-center',
                              modeConfig?.color || 'bg-gray-100'
                            )}>
                              <Icon className="w-4 h-4" />
                            </div>
                            <div>
                              <p className="font-medium">
                                ₹{payment.amount.toLocaleString('en-IN')}
                              </p>
                              <p className="text-xs text-gray-500">
                                {modeConfig?.label}
                                {payment.reference && ` • ${payment.reference}`}
                              </p>
                            </div>
                          </div>
                          <button
                            onClick={() => onRemovePayment(payment.id)}
                            className="p-2 text-gray-400 hover:text-red-500 transition-colors"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200">
          {isPaymentComplete ? (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-green-600">
                <CheckCircle className="w-5 h-5" />
                <span className="font-medium">Payment Complete</span>
              </div>
              <div className="flex gap-3">
                <button onClick={onClose} className="btn-outline">
                  Back
                </button>
                <button onClick={onComplete} className="btn-primary">
                  Complete Order
                </button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-yellow-600">
                <AlertCircle className="w-5 h-5" />
                <span className="text-sm">
                  ₹{balanceDue.toLocaleString('en-IN')} remaining
                </span>
              </div>
              <div className="flex gap-3">
                <button onClick={onClose} className="btn-outline">
                  Back
                </button>
                <button
                  onClick={onComplete}
                  disabled
                  className="btn-primary opacity-50 cursor-not-allowed"
                  title="Complete payment to proceed"
                >
                  Complete Order
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default PaymentModal;
