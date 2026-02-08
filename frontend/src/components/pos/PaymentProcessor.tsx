import { useState } from 'react';
import { CreditCard, Wallet, Smartphone, DollarSign, CheckCircle } from 'lucide-react';
import clsx from 'clsx';

interface PaymentProcessorProps {
  billData: any;
  selectedCustomer: any;
  onComplete: () => void;
  onBack: () => void;
}

type PaymentMethod = 'cash' | 'card' | 'upi' | 'wallet' | 'split';

interface PaymentDetails {
  method: PaymentMethod;
  amount: number;
  referenceNumber?: string;
  cardNumber?: string;
  upiId?: string;
  walletName?: string;
}

export function PaymentProcessor({
  billData,
  onComplete,
  onBack,
}: PaymentProcessorProps) {
  const [selectedMethod, setSelectedMethod] = useState<PaymentMethod | null>(null);
  const [cashAmount, setCashAmount] = useState<number>(billData.total_amount);
  const [paymentDetails, setPaymentDetails] = useState<Partial<PaymentDetails>>({});
  const [isProcessing, setIsProcessing] = useState(false);
  const [paymentComplete, setPaymentComplete] = useState(false);
  const [changeAmount, setChangeAmount] = useState(0);

  const paymentMethods = [
    { id: 'cash', label: 'Cash', icon: DollarSign, color: 'green' },
    { id: 'card', label: 'Card', icon: CreditCard, color: 'blue' },
    { id: 'upi', label: 'UPI', icon: Smartphone, color: 'purple' },
    { id: 'wallet', label: 'Wallet', icon: Wallet, color: 'amber' },
  ];

  const handleCashPayment = () => {
    if (cashAmount < billData.total_amount) {
      alert('Insufficient payment amount');
      return;
    }
    setChangeAmount(cashAmount - billData.total_amount);
    processPayment();
  };

  const handleCardPayment = () => {
    if (!paymentDetails.cardNumber) {
      alert('Please enter card details');
      return;
    }
    processPayment();
  };

  const handleUPIPayment = () => {
    if (!paymentDetails.upiId) {
      alert('Please enter UPI ID');
      return;
    }
    processPayment();
  };

  const handleWalletPayment = () => {
    if (!paymentDetails.walletName) {
      alert('Please select wallet');
      return;
    }
    processPayment();
  };

  const processPayment = () => {
    setIsProcessing(true);
    // Simulate payment processing
    setTimeout(() => {
      setIsProcessing(false);
      setPaymentComplete(true);
    }, 2000);
  };

  if (paymentComplete) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center space-y-4">
          <div className="w-20 h-20 bg-green-500 rounded-full flex items-center justify-center mx-auto">
            <CheckCircle className="w-12 h-12 text-white" />
          </div>
          <h2 className="text-2xl font-bold text-white">Payment Successful!</h2>
          <div className="bg-gray-800 rounded-lg p-6 space-y-2 text-left">
            <p className="text-sm text-gray-400">Amount Paid: <span className="text-green-400 font-semibold">₹{billData.total_amount}</span></p>
            <p className="text-sm text-gray-400">Method: <span className="text-blue-400 font-semibold capitalize">{selectedMethod}</span></p>
            {changeAmount > 0 && (
              <p className="text-sm text-gray-400">Change: <span className="text-yellow-400 font-semibold">₹{changeAmount.toFixed(2)}</span></p>
            )}
          </div>
          <button
            onClick={onComplete}
            className="w-full bg-blue-600 hover:bg-blue-700 text-white py-3 rounded-lg font-semibold transition-colors mt-4"
          >
            Continue
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-2xl mx-auto">
      {/* Bill Summary */}
      <div className="bg-gray-800 rounded-lg p-4 space-y-2">
        <h3 className="text-lg font-bold text-white mb-4">Payment Summary</h3>
        <div className="flex justify-between text-gray-300">
          <span>Subtotal</span>
          <span>₹{billData.subtotal.toFixed(2)}</span>
        </div>
        <div className="flex justify-between text-gray-300">
          <span>Discounts</span>
          <span>-₹{(billData.item_discount + billData.order_discount_amount).toFixed(2)}</span>
        </div>
        <div className="flex justify-between text-gray-300">
          <span>GST</span>
          <span>₹{billData.total_gst.toFixed(2)}</span>
        </div>
        <div className="border-t border-gray-700 pt-2 mt-2 flex justify-between text-xl font-bold text-green-500">
          <span>Total Amount</span>
          <span>₹{billData.total_amount}</span>
        </div>
      </div>

      {/* Payment Methods */}
      <div className="space-y-4">
        <h3 className="text-lg font-bold text-white">Select Payment Method</h3>
        <div className="grid grid-cols-2 gap-4">
          {paymentMethods.map(method => (
            <button
              key={method.id}
              onClick={() => setSelectedMethod(method.id as PaymentMethod)}
              className={clsx(
                'p-4 rounded-lg border-2 transition-all flex flex-col items-center gap-2',
                selectedMethod === method.id
                  ? `border-${method.color}-500 bg-${method.color}-900 bg-opacity-20`
                  : 'border-gray-700 bg-gray-800 hover:border-gray-600'
              )}
            >
              <method.icon className={clsx('w-8 h-8', selectedMethod === method.id && `text-${method.color}-500`)} />
              <span className="font-medium text-white">{method.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Payment Details by Method */}
      <div className="bg-gray-800 rounded-lg p-6 space-y-4">
        {selectedMethod === 'cash' && (
          <div className="space-y-4">
            <h4 className="font-semibold text-white">Cash Payment</h4>
            <div>
              <label className="text-sm text-gray-300 block mb-2">Amount Received</label>
              <input
                type="number"
                value={cashAmount}
                onChange={(e) => setCashAmount(parseFloat(e.target.value) || 0)}
                className="w-full bg-gray-700 text-white border border-gray-600 rounded px-4 py-2 text-lg focus:outline-none focus:border-green-500"
              />
            </div>
            <div className="bg-gray-700 rounded p-3">
              <p className="text-sm text-gray-400">Bill Amount: <span className="text-white font-semibold">₹{billData.total_amount}</span></p>
              <p className="text-sm text-gray-400">Change Due: <span className={clsx(
                'font-semibold',
                cashAmount >= billData.total_amount ? 'text-green-400' : 'text-red-400'
              )}>₹{(cashAmount - billData.total_amount).toFixed(2)}</span></p>
            </div>
            <button
              onClick={handleCashPayment}
              disabled={isProcessing || cashAmount < billData.total_amount}
              className="w-full bg-green-600 hover:bg-green-700 disabled:bg-gray-700 text-white py-3 rounded-lg font-semibold transition-colors"
            >
              {isProcessing ? 'Processing...' : 'Complete Payment'}
            </button>
          </div>
        )}

        {selectedMethod === 'card' && (
          <div className="space-y-4">
            <h4 className="font-semibold text-white">Card Payment</h4>
            <div>
              <label className="text-sm text-gray-300 block mb-2">Card Number</label>
              <input
                type="text"
                placeholder="1234 5678 9012 3456"
                maxLength={19}
                onChange={(e) => setPaymentDetails({ ...paymentDetails, cardNumber: e.target.value })}
                className="w-full bg-gray-700 text-white border border-gray-600 rounded px-4 py-2 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm text-gray-300 block mb-2">Expiry</label>
                <input
                  type="text"
                  placeholder="MM/YY"
                  className="w-full bg-gray-700 text-white border border-gray-600 rounded px-4 py-2 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="text-sm text-gray-300 block mb-2">CVV</label>
                <input
                  type="text"
                  placeholder="123"
                  maxLength={3}
                  className="w-full bg-gray-700 text-white border border-gray-600 rounded px-4 py-2 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
            <p className="text-xs text-gray-400">Amount to charge: ₹{billData.total_amount}</p>
            <button
              onClick={handleCardPayment}
              disabled={isProcessing}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 text-white py-3 rounded-lg font-semibold transition-colors"
            >
              {isProcessing ? 'Processing...' : 'Complete Payment'}
            </button>
          </div>
        )}

        {selectedMethod === 'upi' && (
          <div className="space-y-4">
            <h4 className="font-semibold text-white">UPI Payment</h4>
            <div>
              <label className="text-sm text-gray-300 block mb-2">UPI ID</label>
              <input
                type="email"
                placeholder="username@bank"
                onChange={(e) => setPaymentDetails({ ...paymentDetails, upiId: e.target.value })}
                className="w-full bg-gray-700 text-white border border-gray-600 rounded px-4 py-2 focus:outline-none focus:border-purple-500"
              />
            </div>
            <p className="text-xs text-gray-400">Amount to charge: ₹{billData.total_amount}</p>
            <button
              onClick={handleUPIPayment}
              disabled={isProcessing}
              className="w-full bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 text-white py-3 rounded-lg font-semibold transition-colors"
            >
              {isProcessing ? 'Processing...' : 'Complete Payment'}
            </button>
          </div>
        )}

        {selectedMethod === 'wallet' && (
          <div className="space-y-4">
            <h4 className="font-semibold text-white">Wallet Payment</h4>
            <div>
              <label className="text-sm text-gray-300 block mb-2">Select Wallet</label>
              <select
                onChange={(e) => setPaymentDetails({ ...paymentDetails, walletName: e.target.value })}
                className="w-full bg-gray-700 text-white border border-gray-600 rounded px-4 py-2 focus:outline-none focus:border-amber-500"
              >
                <option value="">Choose wallet...</option>
                <option value="Paytm">Paytm</option>
                <option value="Google Pay">Google Pay</option>
                <option value="PhonePe">PhonePe</option>
                <option value="Amazon Pay">Amazon Pay</option>
              </select>
            </div>
            <p className="text-xs text-gray-400">Amount to charge: ₹{billData.total_amount}</p>
            <button
              onClick={handleWalletPayment}
              disabled={isProcessing}
              className="w-full bg-amber-600 hover:bg-amber-700 disabled:bg-gray-700 text-white py-3 rounded-lg font-semibold transition-colors"
            >
              {isProcessing ? 'Processing...' : 'Complete Payment'}
            </button>
          </div>
        )}
      </div>

      {/* Back Button */}
      {!selectedMethod && (
        <button
          onClick={onBack}
          className="w-full bg-gray-700 hover:bg-gray-600 text-white py-3 rounded-lg font-semibold transition-colors"
        >
          Back
        </button>
      )}
    </div>
  );
}

export default PaymentProcessor;
