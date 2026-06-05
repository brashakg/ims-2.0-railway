// ============================================================================
// Credit Billing Option (POS Payment Step)
// POS-4: khata / per-customer credit limit guard
// ============================================================================
//
// Fetches the customer's credit summary (limit + AR outstanding) on mount
// and enforces the limit before the operator can apply the CREDIT tender.
// A limit of 0 means unlimited (the default for B2C customers).

import { useEffect, useState } from 'react';
import { usePOSStore } from '../../stores/posStore';
import { CreditCard, AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { customersApi } from '../../services/api/customers';

interface CreditSummary {
  credit_limit: number;
  ar_outstanding: number;
  ar_available: number | null;
  limit_exceeded: boolean;
}

export function CreditBillingOption() {
  const store = usePOSStore();
  const [summary, setSummary] = useState<CreditSummary | null>(null);
  const [loading, setLoading] = useState(false);

  const customerId = store.customer?.id;
  const isWalkin = !customerId || customerId.startsWith('walkin-');

  const hasCreditPayment = (store.payments || []).some(p => p.method === 'CREDIT');
  const creditAmount = (store.payments || [])
    .filter(p => p.method === 'CREDIT')
    .reduce((sum, p) => sum + p.amount, 0);

  useEffect(() => {
    let alive = true;
    if (isWalkin || !customerId) return;

    setLoading(true);
    customersApi
      .getCreditSummary(customerId)
      .then((data) => {
        if (alive) setSummary(data);
      })
      .catch(() => {
        // fail-soft: no limit enforced when summary unavailable
        if (alive) setSummary(null);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId]);

  const grandTotal = store.getGrandTotal();
  const wouldExceed =
    summary !== null &&
    summary.credit_limit > 0 &&
    summary.ar_outstanding + grandTotal > summary.credit_limit;

  const handleAddCredit = () => {
    store.addPayment({
      method: 'CREDIT',
      amount: grandTotal,
      reference: 'Account Credit',
    });
  };

  const handleRemoveCredit = () => {
    const idx = (store.payments || []).findIndex(p => p.method === 'CREDIT');
    if (idx >= 0) store.removePayment(idx);
  };

  const fc = (n: number) =>
    new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(n);

  return (
    <div className="border-2 border-blue-200 rounded-lg p-4 bg-blue-50">
      <div className="flex items-start gap-3 mb-3">
        <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center flex-shrink-0">
          <CreditCard className="w-5 h-5 text-blue-600" />
        </div>
        <div className="flex-1">
          <p className="font-semibold text-gray-900">Credit Billing (Khata)</p>
          <p className="text-sm text-gray-600 mt-1">
            Amount will be marked as outstanding on customer's account
          </p>
        </div>
        {loading && <Loader className="w-4 h-4 text-blue-400 animate-spin" />}
      </div>

      {/* Credit limit / AR summary */}
      {!isWalkin && summary !== null && (
        <div className="mb-3 bg-white rounded-lg border border-blue-100 p-3 text-sm space-y-1">
          <div className="flex justify-between text-gray-600">
            <span>Credit limit</span>
            <span className="font-medium text-gray-900">
              {summary.credit_limit === 0 ? 'Unlimited' : fc(summary.credit_limit)}
            </span>
          </div>
          <div className="flex justify-between text-gray-600">
            <span>AR outstanding</span>
            <span className="font-medium text-gray-900">{fc(summary.ar_outstanding)}</span>
          </div>
          {summary.credit_limit > 0 && summary.ar_available !== null && (
            <div className="flex justify-between text-gray-600 border-t border-gray-100 pt-1">
              <span>Available credit</span>
              <span className={`font-semibold ${summary.ar_available <= 0 ? 'text-red-600' : 'text-green-700'}`}>
                {fc(summary.ar_available)}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Limit-exceeded warning */}
      {wouldExceed && !hasCreditPayment && (
        <div className="mb-3 bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2">
          <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-800">
            Adding this sale would exceed the credit limit
            {summary && summary.credit_limit > 0
              ? ` of ${fc(summary.credit_limit)}`
              : ''}.
            Get manager approval or collect cash/card.
          </p>
        </div>
      )}

      {!hasCreditPayment ? (
        <button
          type="button"
          onClick={handleAddCredit}
          disabled={wouldExceed}
          className="btn-primary w-full text-sm disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {wouldExceed ? 'Credit Limit Exceeded' : 'Mark Full Amount as Credit'}
        </button>
      ) : (
        <div className="space-y-3">
          <div className="bg-white rounded-lg p-3 border border-blue-100 flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-600">Credit Amount</p>
              <p className="text-lg font-bold text-blue-600">{fc(creditAmount)}</p>
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
            type="button"
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
