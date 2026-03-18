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
      'border rounded-xl p-4 space-y-3',
      isWalkin ? 'bg-gray-50 border-gray-200' : 'bg-bv-gold-50 border-bv-gold-200'
    )}>
      {/* Customer Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={clsx(
            'w-12 h-12 rounded-full text-white flex items-center justify-center font-bold text-lg',
            isWalkin ? 'bg-gray-400' : 'bg-bv-gold-500'
          )}>
            {store.customer.name?.charAt(0) || 'W'}
          </div>
          <div>
            <p className="font-semibold text-gray-900">{store.customer.name}</p>
            <p className="text-sm text-gray-600">{store.customer.phone || 'No phone'}</p>
          </div>
        </div>
        <button 
          onClick={() => store.setCustomer(null)}
          className="text-sm text-gray-500 hover:text-gray-700 px-3 py-1 border border-gray-200 rounded-lg"
        >
          Change
        </button>
      </div>

      {!isWalkin && (
        <>
          {/* Loyalty Points Badge */}
          {store.customerLoyaltyPoints > 0 && (
            <div className="bg-white rounded-lg p-3 border border-bv-gold-100">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <Award className="w-5 h-5 text-bv-gold-600" />
                  <span className="font-medium text-gray-900">Loyalty Points</span>
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
            <div className="bg-white rounded-lg p-3 border border-blue-100">
              <div className="flex items-center gap-2 mb-2">
                <Eye className="w-5 h-5 text-blue-600" />
                <span className="font-medium text-gray-900 text-sm">Last Prescription</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs text-gray-600">
                {store.customerLastRx.map((rx: any, idx: number) => (
                  <div key={idx} className="bg-gray-50 rounded p-2">
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
        <p className="text-xs text-bv-gold-600 bg-white rounded p-2">
          Selected Patient: <span className="font-semibold">{store.patient.name}</span>
        </p>
      )}
    </div>
  );
}
