// ============================================================================
// IMS 2.0 - Payment Collection Panel for POS
// ============================================================================
// Handles multiple payment modes including online payments

import { useState } from 'react';
import {
  CreditCard, Banknote, Smartphone, Building2, Gift, Clock,
  Plus, X, Check, Loader2, QrCode,
} from 'lucide-react';
import type { Payment, PaymentMode } from '../../types';

interface PaymentCollectionPanelProps {
  grandTotal: number;
  payments: Payment[];
  onAddPayment: (payment: Omit<Payment, 'id' | 'paidAt'>) => void;
  onRemovePayment: (paymentId: string) => void;
  customerName?: string;
  customerEmail?: string;
  customerContact?: string;
  orderId?: string;
  orderNumber?: string;
  onInitiateOnlinePayment?: () => Promise<{ orderId: string; orderNumber: string }>;
  allowCredit?: boolean;
}

const PAYMENT_MODES: { mode: PaymentMode; label: string; icon: typeof CreditCard; color: string }[] = [
  { mode: 'CASH', label: 'Cash', icon: Banknote, color: 'text-green-600 bg-green-50' },
  { mode: 'UPI', label: 'UPI', icon: Smartphone, color: 'text-purple-600 bg-purple-50' },
  { mode: 'CARD', label: 'Card', icon: CreditCard, color: 'text-blue-600 bg-blue-50' },
  { mode: 'BANK_TRANSFER', label: 'Bank', icon: Building2, color: 'text-gray-600 bg-gray-50' },
  { mode: 'GIFT_VOUCHER', label: 'Voucher', icon: Gift, color: 'text-pink-600 bg-pink-50' },
  { mode: 'CREDIT', label: 'Credit', icon: Clock, color: 'text-amber-600 bg-amber-50' },
];

export function PaymentCollectionPanel({
  grandTotal,
  payments,
  onAddPayment,
  onRemovePayment,
  customerName: _customerName,
  customerEmail: _customerEmail,
  customerContact: _customerContact,
  orderId: _orderId,
  orderNumber,
  onInitiateOnlinePayment,
  allowCredit = true,
}: PaymentCollectionPanelProps) {
  // These props are reserved for future online payment integrations
  void _customerName;
  void _customerEmail;
  void _customerContact;
  void _orderId;
  const [selectedMode, setSelectedMode] = useState<PaymentMode | null>(null);
  const [amount, setAmount] = useState('');
  const [reference, setReference] = useState('');
  const [isProcessingOnline, setIsProcessingOnline] = useState(false);
  const [showQRCode, setShowQRCode] = useState(false);

  const totalPaid = payments.reduce((sum, p) => sum + p.amount, 0);
  const balanceDue = grandTotal - totalPaid;

  const handleAddPayment = () => {
    if (!selectedMode || !amount) return;

    const paymentAmount = parseFloat(amount);
    if (isNaN(paymentAmount) || paymentAmount <= 0) return;

    onAddPayment({
      mode: selectedMode,
      amount: paymentAmount,
      reference: reference || undefined,
    });

    // Reset form
    setSelectedMode(null);
    setAmount('');
    setReference('');
  };

  const handleQuickAmount = (value: number) => {
    setAmount(value.toString());
  };

  const handleOnlinePayment = async () => {
    if (!onInitiateOnlinePayment) return;

    setIsProcessingOnline(true);
    try {
      const result = await onInitiateOnlinePayment();
      // Show QR code for UPI payment
      setShowQRCode(true);
      // In production, this would integrate with payment gateway
      console.log('Online payment initiated:', result);
    } catch (err) {
      console.error('Failed to initiate online payment:', err);
    } finally {
      setIsProcessingOnline(false);
    }
  };

  const formatCurrency = (amount: number) => `₹${amount.toLocaleString('en-IN')}`;

  const getPaymentIcon = (mode: PaymentMode) => {
    return PAYMENT_MODES.find(m => m.mode === mode)?.icon || CreditCard;
  };

  const getPaymentColor = (mode: PaymentMode) => {
    return PAYMENT_MODES.find(m => m.mode === mode)?.color || '';
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <CreditCard className="w-4 h-4 text-bv-red-600" />
          <h3 className="font-medium text-gray-900">Payment</h3>
        </div>
        <div className="text-sm">
          <span className="text-gray-500">Balance: </span>
          <span className={balanceDue > 0 ? 'text-red-600 font-semibold' : 'text-green-600 font-semibold'}>
            {formatCurrency(Math.max(0, balanceDue))}
          </span>
        </div>
      </div>

      {/* Existing Payments */}
      {payments.length > 0 && (
        <div className="space-y-2 mb-4">
          {payments.map((payment) => {
            const Icon = getPaymentIcon(payment.mode);
            return (
              <div
                key={payment.id}
                className="flex items-center justify-between p-2 bg-gray-50 rounded-lg"
              >
                <div className="flex items-center gap-2">
                  <div className={`p-1.5 rounded ${getPaymentColor(payment.mode)}`}>
                    <Icon className="w-4 h-4" />
                  </div>
                  <div>
                    <p className="text-sm font-medium">{payment.mode}</p>
                    {payment.reference && (
                      <p className="text-xs text-gray-500">{payment.reference}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-green-600">
                    {formatCurrency(payment.amount)}
                  </span>
                  <button
                    onClick={() => onRemovePayment(payment.id)}
                    className="p-1 text-gray-400 hover:text-red-500 rounded"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Add Payment Form */}
      {balanceDue > 0 && (
        <div className="space-y-3">
          {/* Payment Mode Selection */}
          <div className="grid grid-cols-3 gap-2">
            {PAYMENT_MODES
              .filter(m => allowCredit || m.mode !== 'CREDIT')
              .map(({ mode, label, icon: Icon, color }) => (
                <button
                  key={mode}
                  onClick={() => {
                    setSelectedMode(mode);
                    if (mode === 'UPI' && onInitiateOnlinePayment) {
                      setAmount(balanceDue.toString());
                    }
                  }}
                  className={`p-2 rounded-lg border text-xs flex flex-col items-center gap-1 transition-all ${
                    selectedMode === mode
                      ? 'border-bv-red-500 bg-bv-red-50'
                      : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <Icon className={`w-4 h-4 ${selectedMode === mode ? 'text-bv-red-600' : color.split(' ')[0]}`} />
                  <span>{label}</span>
                </button>
              ))}
          </div>

          {/* Amount & Reference */}
          {selectedMode && (
            <div className="space-y-2 pt-2 border-t border-gray-100">
              {/* Quick amounts */}
              <div className="flex gap-2 flex-wrap">
                <button
                  onClick={() => handleQuickAmount(balanceDue)}
                  className="px-3 py-1 text-xs bg-gray-100 rounded-lg hover:bg-gray-200"
                >
                  Full ({formatCurrency(balanceDue)})
                </button>
                {[500, 1000, 2000, 5000].map(amt => (
                  <button
                    key={amt}
                    onClick={() => handleQuickAmount(amt)}
                    className="px-3 py-1 text-xs bg-gray-100 rounded-lg hover:bg-gray-200"
                  >
                    {formatCurrency(amt)}
                  </button>
                ))}
              </div>

              <div className="flex gap-2">
                <input
                  type="number"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="Amount"
                  className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
                />
                {(selectedMode === 'UPI' || selectedMode === 'CARD' || selectedMode === 'BANK_TRANSFER') && (
                  <input
                    type="text"
                    value={reference}
                    onChange={(e) => setReference(e.target.value)}
                    placeholder="Reference/Transaction ID"
                    className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
                  />
                )}
              </div>

              {/* Online Payment Option for UPI */}
              {selectedMode === 'UPI' && onInitiateOnlinePayment && (
                <div className="flex gap-2">
                  <button
                    onClick={handleOnlinePayment}
                    disabled={isProcessingOnline}
                    className="flex-1 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 flex items-center justify-center gap-2 disabled:opacity-50"
                  >
                    {isProcessingOnline ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <QrCode className="w-4 h-4" />
                    )}
                    Generate QR Code
                  </button>
                </div>
              )}

              {/* Add Payment Button */}
              <button
                onClick={handleAddPayment}
                disabled={!amount || parseFloat(amount) <= 0}
                className="w-full py-2 bg-bv-red-600 text-white rounded-lg text-sm font-medium hover:bg-bv-red-700 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Plus className="w-4 h-4" />
                Add {selectedMode} Payment
              </button>
            </div>
          )}
        </div>
      )}

      {/* Paid in Full */}
      {balanceDue <= 0 && (
        <div className="flex items-center justify-center gap-2 py-4 bg-green-50 rounded-lg text-green-600">
          <Check className="w-5 h-5" />
          <span className="font-medium">Payment Complete</span>
        </div>
      )}

      {/* QR Code Modal */}
      {showQRCode && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 max-w-sm mx-4 text-center">
            <h3 className="font-semibold text-gray-900 mb-4">Scan to Pay</h3>
            <div className="w-48 h-48 bg-gray-100 rounded-lg mx-auto mb-4 flex items-center justify-center">
              <QrCode className="w-32 h-32 text-gray-400" />
            </div>
            <p className="text-sm text-gray-500 mb-2">Amount: {formatCurrency(parseFloat(amount) || balanceDue)}</p>
            <p className="text-xs text-gray-400 mb-4">Order: {orderNumber}</p>
            <div className="flex gap-2">
              <button
                onClick={() => setShowQRCode(false)}
                className="flex-1 py-2 border border-gray-200 rounded-lg text-sm"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  // Mark as paid manually
                  onAddPayment({
                    mode: 'UPI',
                    amount: parseFloat(amount) || balanceDue,
                    reference: `QR-${Date.now()}`,
                  });
                  setShowQRCode(false);
                  setSelectedMode(null);
                  setAmount('');
                }}
                className="flex-1 py-2 bg-green-600 text-white rounded-lg text-sm"
              >
                Confirm Paid
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PaymentCollectionPanel;
