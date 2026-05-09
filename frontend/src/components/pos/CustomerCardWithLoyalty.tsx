// ============================================================================
// Customer Card with Loyalty Points, Last Rx, Last Order (POS Step 1)
// ============================================================================
//
// Live wiring to the loyalty engine. On mount (+ when the customer
// changes), fetch the real account snapshot and stamp the balance back
// onto the POS store so the rest of the POS flow (CustomerSummary, the
// Redeem control on POSPayment, the receipt summary) all read the same
// source-of-truth value.
//
// Fail-soft: if the request fails (no DB, network blip), the badge falls
// back to whatever was cached on the customer record. The redeem control
// on the Payment step will see the same fallback state.

import { useEffect, useState } from 'react';
import { Award, Eye, ShoppingBag, Clock } from 'lucide-react';
import clsx from 'clsx';

import { usePOSStore } from '../../stores/posStore';
import { loyaltyApi, type LoyaltyTier } from '../../services/api/loyalty';

// Color tokens per tier — used by the badge below the points number.
const TIER_TOKENS: Record<LoyaltyTier, { bg: string; text: string; ring: string }> = {
  BRONZE:   { bg: 'bg-amber-50',  text: 'text-amber-700',  ring: 'ring-amber-200' },
  SILVER:   { bg: 'bg-slate-50',  text: 'text-slate-700',  ring: 'ring-slate-200' },
  GOLD:     { bg: 'bg-yellow-50', text: 'text-yellow-700', ring: 'ring-yellow-200' },
  PLATINUM: { bg: 'bg-violet-50', text: 'text-violet-700', ring: 'ring-violet-200' },
};

export function CustomerCardWithLoyalty() {
  const store = usePOSStore();
  const [tier, setTier] = useState<LoyaltyTier>('BRONZE');
  const [expiringSoon, setExpiringSoon] = useState<number>(0);
  const [loading, setLoading] = useState(false);

  const customerId = store.customer?.id;
  const isWalkin = customerId?.toString().startsWith('walkin-');

  useEffect(() => {
    let alive = true;
    if (!customerId || isWalkin) return;

    setLoading(true);
    loyaltyApi
      .getAccount(String(customerId))
      .then((envelope) => {
        if (!alive) return;
        const acct = envelope.account;
        store.setCustomerLoyaltyPoints(acct?.balance_points ?? 0);
        setTier((acct?.tier ?? 'BRONZE') as LoyaltyTier);
        setExpiringSoon(envelope.expiring_soon_points ?? 0);
      })
      .catch(() => {
        // fail-soft — keep whatever was cached
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId, isWalkin]);

  if (!store.customer) return null;

  const tierTokens = TIER_TOKENS[tier];

  return (
    <div className={clsx(
      'border rounded-xl p-3 space-y-3 mt-2',
      isWalkin ? 'bg-white border-gray-200' : 'bg-bv-red-50 border-bv-red-200'
    )}>
      {!isWalkin && (
        <>
          {/* Loyalty Points + Tier Badge */}
          <div className="bg-white rounded-lg p-3 border border-bv-red-100">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <Award className="w-5 h-5 text-bv-red-600" />
                <span className="font-medium text-gray-900">Loyalty Points</span>
              </div>
              <span className="text-xl font-bold text-bv-red-600">
                {loading ? '…' : (store.customerLoyaltyPoints ?? 0).toLocaleString('en-IN')}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className={clsx(
                'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold ring-1 ring-inset',
                tierTokens.bg, tierTokens.text, tierTokens.ring,
              )}>
                {tier}
              </span>
              {expiringSoon > 0 && (
                <span className="inline-flex items-center gap-1 text-xs text-amber-700">
                  <Clock className="w-3 h-3" />
                  {expiringSoon} expiring soon
                </span>
              )}
            </div>
          </div>

          {/* Last Prescription Summary */}
          {store.customerLastRx && store.customerLastRx.length > 0 && (
            <div className="bg-white rounded-lg p-3 border border-blue-100">
              <div className="flex items-center gap-2 mb-2">
                <Eye className="w-5 h-5 text-blue-600" />
                <span className="font-medium text-gray-900 text-sm">Last Prescription</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs text-gray-500">
                {store.customerLastRx.map((rx: any, idx: number) => (
                  <div key={idx} className="bg-gray-100 rounded p-2">
                    <p className="font-semibold text-gray-700">{rx.eyeSide}</p>
                    <p>SPH: {rx.sph || '-'}</p>
                    {rx.cyl && <p>CYL: {rx.cyl}</p>}
                    {rx.add && <p>ADD: {rx.add}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Last Order */}
          {store.customerLastOrder && (
            <div className="bg-white rounded-lg p-3 border border-green-100">
              <div className="flex items-center gap-2">
                <ShoppingBag className="w-5 h-5 text-green-600" />
                <div className="text-sm">
                  <p className="font-medium text-gray-900">Last bought: {store.customerLastOrder.productName}</p>
                  <p className="text-xs text-gray-500">{store.customerLastOrder.monthsAgo} months ago</p>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {isWalkin && (
        <p className="text-xs text-amber-600">Walk-in — Quick Sale only</p>
      )}

      {store.patient && (
        <p className="text-xs text-bv-red-600 bg-white rounded p-2">
          Selected Patient: <span className="font-semibold">{store.patient.name}</span>
        </p>
      )}
    </div>
  );
}
