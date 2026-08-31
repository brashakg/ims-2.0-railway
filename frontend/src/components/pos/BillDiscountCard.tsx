// ============================================================================
// IMS 2.0 - Bill-level discount (owner spec 3)
// ============================================================================
// Item discounts AND a bill discount can both apply; this is the bill half.
// Extracted verbatim from POSLayout so the classic surface and the new
// one-screen surfaces share ONE control. A second hand-rolled copy is how the
// two would drift into applying different caps to the same money - this
// repo's dominant defect class.
//
// The percent is capped at the user's role cap here for immediate feedback.
// It is NOT the authority: the server re-checks against canonical pricing_caps
// (role AND category AND brand, lowest wins), so a tampered client cannot
// widen a cap. Deliberately no new money logic on this path.

import { usePOSStore } from '../../stores/posStore';
import { useAuth } from '../../context/AuthContext';

const fc = (v: number) => `₹${Math.round(v || 0).toLocaleString('en-IN')}`;

export function BillDiscountCard() {
  const store = usePOSStore();
  const { user } = useAuth();
  const cap = user?.discountCap ?? 10;

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-2 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <label className="font-medium text-gray-900">Overall discount</label>
          <p className="text-xs text-gray-500">Applied to subtotal (after per-item discounts)</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <input
            type="number"
            min={0}
            max={cap}
            step={0.5}
            value={store.cart_discount_percent || 0}
            onChange={(e) => {
              const pct = Math.max(0, Math.min(cap, parseFloat(e.target.value) || 0));
              store.setCartDiscount(pct, store.cart_discount_reason || undefined);
            }}
            onFocus={(e) => e.target.select()}
            aria-label="Overall discount percent"
            className="w-20 min-h-[44px] px-2 border border-gray-300 rounded text-sm text-right text-gray-900"
            placeholder="0"
          />
          <span className="text-sm text-gray-500">%</span>
        </div>
      </div>
      {store.cart_discount_percent > 0 && (
        <div className="pt-2 space-y-2 border-t border-gray-200">
          {/* Owner ruling: a discount with no applicable offer needs a written
              reason. The server enforces >=4 chars; this asks for it up front
              so the sale is not refused at the very end. */}
          <input
            type="text"
            value={store.cart_discount_reason || ''}
            onChange={(e) =>
              store.setCartDiscount(
                store.cart_discount_percent,
                e.target.value,
                store.cart_discount_approved_by || undefined,
              )
            }
            placeholder="Reason (loyal customer, damaged box, festival offer...)"
            aria-label="Overall discount reason"
            className="w-full min-h-[44px] px-2 border border-gray-300 rounded text-xs text-gray-900"
          />
          {(store.cart_discount_reason || '').trim().length < 4 && (
            <p className="text-xs text-amber-700">
              Required — at least 4 characters. No reason, no discount.
            </p>
          )}
          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-500">Max allowed: {cap}% (role cap)</span>
            <span className="text-green-600 font-medium">
              -{fc(store.cart_discount_amount || 0)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default BillDiscountCard;
