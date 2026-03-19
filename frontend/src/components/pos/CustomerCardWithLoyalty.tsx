// ============================================================================
// Customer Card with Loyalty Points, Last Rx, Last Order (POS Step 1)
// ============================================================================

import { usePOSStore } from '../../stores/posStore';
import { Award, Eye, ShoppingBag } from 'lucide-react';
import clsx from 'clsx';

export function CustomerCardWithLoyalty() {
  const store = usePOSStore();

  if (!store.customer) return null;

  const isWalkin = store.customer?.id?.toString().startsWith('walkin-');

  return (
    <div className={clsx(
      'border rounded-xl p-3 space-y-3 mt-2',
      isWalkin ? 'bg-gray-800 border-gray-700' : 'bg-bv-gold-900/20 border-bv-gold-600/30'
    )}>
      {!isWalkin && (
        <>
          {/* Loyalty Points Badge */}
          {store.customerLoyaltyPoints > 0 && (
            <div className="bg-gray-800 rounded-lg p-3 border border-bv-gold-100">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <Award className="w-5 h-5 text-bv-gold-600" />
                  <span className="font-medium text-white">Loyalty Points</span>
                </div>
                <span className="text-xl font-bold text-bv-gold-600">{store.customerLoyaltyPoints}</span>
              </div>
              <button
                onClick={() => {
                  const pointsToRedeem = Math.min(store.customerLoyaltyPoints, Math.floor(store.getGrandTotal() / 10));
                  if (pointsToRedeem > 0) {
                    store.redeemLoyaltyPoints(pointsToRedeem);
                  }
                }}
                className="text-xs text-bv-gold-600 hover:text-bv-gold-700 font-semibold"
              >
                ✓ Redeem Points
              </button>
            </div>
          )}

          {/* Last Prescription Summary */}
          {store.customerLastRx && store.customerLastRx.length > 0 && (
            <div className="bg-gray-800 rounded-lg p-3 border border-blue-100">
              <div className="flex items-center gap-2 mb-2">
                <Eye className="w-5 h-5 text-blue-600" />
                <span className="font-medium text-white text-sm">Last Prescription</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs text-gray-400">
                {store.customerLastRx.map((rx: any, idx: number) => (
                  <div key={idx} className="bg-gray-700 rounded p-2">
                    <p className="font-semibold text-gray-300">{rx.eyeSide}</p>
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
            <div className="bg-gray-800 rounded-lg p-3 border border-green-100">
              <div className="flex items-center gap-2">
                <ShoppingBag className="w-5 h-5 text-green-600" />
                <div className="text-sm">
                  <p className="font-medium text-white">Last bought: {store.customerLastOrder.productName}</p>
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
        <p className="text-xs text-bv-gold-600 bg-gray-800 rounded p-2">
          Selected Patient: <span className="font-semibold">{store.patient.name}</span>
        </p>
      )}
    </div>
  );
}
