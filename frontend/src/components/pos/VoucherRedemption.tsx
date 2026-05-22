// ============================================================================
// Voucher/Gift Card Redemption Component (POS Payment Step)
// ============================================================================

import { useState } from 'react';
import { usePOSStore } from '../../stores/posStore';
import { vouchersApi } from '../../services/api/vouchers';
import { Gift, X, Loader2, AlertCircle } from 'lucide-react';

interface VoucherRedemptionProps {
  onVoucherApplied?: (discountAmount: number) => void;
}

export function VoucherRedemption({ onVoucherApplied }: VoucherRedemptionProps) {
  const store = usePOSStore();
  const [voucherCode, setVoucherCode] = useState('');
  const [isValidating, setIsValidating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleRedeemVoucher = async () => {
    if (!voucherCode.trim()) {
      setError('Please enter a voucher code');
      return;
    }

    setIsValidating(true);
    setError(null);

    try {
      // Validate against the real voucher backend (read-only — the actual
      // redeem/decrement happens server-side when the payment is recorded
      // on the order, so an abandoned sale never burns a card).
      const code = voucherCode.trim().toUpperCase();
      const v = await vouchersApi.validate(code);

      if (!v.valid) {
        setError(v.reason || 'Voucher not valid');
        setIsValidating(false);
        return;
      }

      // Apply only up to the order total; the rest stays on the card.
      const balance = v.balance ?? 0;
      const discountAmount = Math.min(balance, store.getGrandTotal());
      store.applyVoucher(code, discountAmount);

      // Record the voucher as a GIFT_VOUCHER payment so the backend
      // recognizes it and redeems the card at payment time.
      store.addPayment({
        method: 'GIFT_VOUCHER',
        amount: discountAmount,
        reference: code,
        voucherCode: code,
        voucherAmount: balance,
      });

      setVoucherCode('');
      onVoucherApplied?.(discountAmount);
      setIsValidating(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to validate voucher. Please try again.';
      setError(message);
      setIsValidating(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Voucher Input Section */}
      <div className="border-2 border-dashed border-purple-200 rounded-lg p-4 bg-purple-50">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          <Gift className="w-4 h-4 inline mr-2" />
          Voucher / Gift Card Code
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            value={voucherCode}
            onChange={(e) => {
              setVoucherCode(e.target.value);
              setError(null);
            }}
            onKeyPress={(e) => e.key === 'Enter' && handleRedeemVoucher()}
            placeholder="e.g., GIFT100, WELCOME50"
            className="input-field flex-1"
            disabled={isValidating || !!store.appliedVoucher}
          />
          {!store.appliedVoucher ? (
            <button
              onClick={handleRedeemVoucher}
              disabled={isValidating || !voucherCode.trim()}
              className="btn-primary px-4"
            >
              {isValidating ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Apply'}
            </button>
          ) : (
            <button
              onClick={() => {
                store.removeVoucher();
                setVoucherCode('');
              }}
              className="btn-outline px-4"
            >
              Remove
            </button>
          )}
        </div>
        {error && (
          <div className="mt-2 flex items-center gap-2 text-sm text-red-600 bg-red-50 p-2 rounded">
            <AlertCircle className="w-4 h-4" />
            {error}
          </div>
        )}
      </div>

      {/* Applied Voucher Display */}
      {store.appliedVoucher && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-3 flex items-center justify-between">
          <div>
            <p className="font-semibold text-green-900">Voucher Applied</p>
            <p className="text-sm text-green-700">{store.appliedVoucher.code}</p>
            <p className="text-sm text-green-600 mt-1">Discount: ₹{store.appliedVoucher.discountAmount.toFixed(2)}</p>
          </div>
          <button
            onClick={() => {
              store.removeVoucher();
              setVoucherCode('');
            }}
            className="p-2 hover:bg-green-100 rounded-lg"
          >
            <X className="w-5 h-5 text-green-600" />
          </button>
        </div>
      )}
    </div>
  );
}
