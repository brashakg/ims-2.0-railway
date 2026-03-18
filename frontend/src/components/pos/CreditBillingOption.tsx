// ============================================================================
// Credit Billing Option (POS Payment Step)
// ============================================================================

import { usePOSStore } from '../../stores/posStore';
import { CreditCard, AlertCircle, CheckCircle } from 'lucide-react';


export function CreditBillingOption() {
  const store = usePOSStore();

  const hasCreditPayment = (store.payments || []).some(p => p.method === 'CREDIT');
  const creditAmount = (store.payments || [])
    .filter(p => p.method === 'CREDIT')
    .reduce((sum, p) => sum + p.amount, 0);

  const handleAddCredit = () => {
    const grandTotal = store.getGrandTotal();
    store.addPayment({
      method: 'CREDIT',
      amount: grandTotal,
      reference: 'Account Credit',
    });
  };

  const handleRemoveCredit = () => {
    const creditPayment = (store.payments || []).findIndex(p => p.method === 'CREDIT');
    if (creditPayment >= 0) {
      store.removePayment(creditPayment);
    }
  };

  return (
    <div className="border-2 border-blue-200 rounded-lg p-4 bg-blue-50">
      <div className="flex items-start gap-3 mb-3">
        <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center flex-shrink-0">
          <CreditCard className="w-5 h-5 text-blue-600" />
        </div>
        <div className="flex-1">
          <p className="font-semibold text-gray-900">Credit Billing</p>
          <p className="text-sm text-gray-600 mt-1">
            Amount will be marked as outstanding on customer's account
          </p>
        </div>
      </div>

      {!hasCreditPayment ? (
        <button
          onClick={handleAddCredit}
          className="btn-primary w-full text-sm"
        >
          Mark Full Amount as Credit
        </button>
      ) : (
        <div className="space-y-3">
          <div className="bg-white rounded-lg p-3 border border-blue-100 flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-600">Credit Amount</p>
              <p className="text-lg font-bold text-blue-600">₹{creditAmount.toFixed(2)}</p>
            </div>
            <div className="flex items-center gap-2 text-green-600">
              <CheckCircle className="w-5 h-5" />
              <span className="text-sm font-medium">Applied</span>
            </div>
          </div>

          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-start gap-2">
            <AlertCircle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
            <p className="text-sm text-amber-800">
              Customer will receive outstanding invoice. Amount must be collected or adjusted later.
            </p>
          </div>

          <button
            onClick={handleRemoveCredit}
            className="btn-outline w-full text-sm text-red-600 border-red-300"
          >
            Remove Credit Payment
          </button>
        </div>
      )}
    </div>
  );
}
