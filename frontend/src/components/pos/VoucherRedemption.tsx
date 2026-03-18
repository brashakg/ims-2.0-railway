// ============================================================================
// Voucher/Gift Card Redemption Component (POS Payment Step)
// ============================================================================

import { useState } from 'react';
import { usePOSStore } from '../../stores/posStore';
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
      // Mock voucher validation - in production, call API
      // For now: validate basic format and mock some codes
      const mockVouchers: Record<string, { amount: number; valid: boolean; expiry: string }> = {
        'GIFT100': { amount: 100, valid: true, expiry: '2026-12-31' },
        'GIFT500': { amount: 500, valid: true, expiry: '2026-12-31' },
        'WELCOME50': { amount: 50, valid: true, expiry: '2026-06-30' },
      };

      const voucher = mockVouchers[voucherCode.toUpperCase()];
      
      if (!voucher) {
        setError('Voucher code not found or expired');
        setIsValidating(false);
        return;
      }

      const now = new Date();
      const expiry = new Date(voucher.expiry);
      
      if (now > expiry) {
        setError('Voucher has expired');
        setIsValidating(false);
        return;
      }

      // Apply voucher
      const discountAmount = Math.min(voucher.amount, store.getGrandTotal());
      store.applyVoucher(voucherCode.toUpperCase(), discountAmount);
      
      // Add voucher as payment method
      store.addPayment({
        method: 'VOUCHER',
        amount: discountAmount,
        reference: voucherCode.toUpperCase(),
        voucherCode: voucherCode.toUpperCase(),
        voucherAmount: voucher.amount,
      });

      setVoucherCode('');
      onVoucherApplied?.(discountAmount);
      setIsValidating(false);
    } catch {
      setError('Failed to validate voucher. Please try again.');
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
