// ============================================================================
// IMS 2.0 - Discount Modal Component
// ============================================================================
// Role-based discount limits enforced by user.discountCap

import { useState, useMemo } from 'react';
import { X, Percent, AlertTriangle, Tag } from 'lucide-react';
import type { CartItem } from '../../types';
import clsx from 'clsx';

interface DiscountModalProps {
  item: CartItem;
  maxDiscountPercent: number; // From user.discountCap
  onApply: (discountPercent: number, discountAmount: number) => void;
  onClose: () => void;
}

// Quick discount percentage options
const QUICK_DISCOUNTS = [5, 10, 15, 20];

export function DiscountModal({
  item,
  maxDiscountPercent,
  onApply,
  onClose,
}: DiscountModalProps) {
  const [discountType, setDiscountType] = useState<'percent' | 'amount'>('percent');
  const [inputValue, setInputValue] = useState<string>(
    item.discountPercent > 0 ? item.discountPercent.toString() : ''
  );

  // Calculate values based on input
  const calculations = useMemo(() => {
    const itemTotal = item.unitPrice * item.quantity;
    const numValue = parseFloat(inputValue) || 0;

    let discountPercent: number;
    let discountAmount: number;

    if (discountType === 'percent') {
      discountPercent = Math.min(numValue, 100); // Cap at 100%
      discountAmount = Math.round((itemTotal * discountPercent) / 100);
    } else {
      discountAmount = Math.min(numValue, itemTotal); // Cap at item total
      discountPercent = itemTotal > 0 ? (discountAmount / itemTotal) * 100 : 0;
    }

    const finalPrice = itemTotal - discountAmount;
    const exceedsLimit = discountPercent > maxDiscountPercent;

    return {
      itemTotal,
      discountPercent,
      discountAmount,
      finalPrice,
      exceedsLimit,
    };
  }, [item, inputValue, discountType, maxDiscountPercent]);

  const handleQuickDiscount = (percent: number) => {
    setDiscountType('percent');
    setInputValue(percent.toString());
  };

  const handleApply = () => {
    if (calculations.exceedsLimit) return;
    onApply(
      Math.round(calculations.discountPercent * 100) / 100,
      calculations.discountAmount
    );
  };

  const handleClearDiscount = () => {
    onApply(0, 0);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-bv-red-100 rounded-full flex items-center justify-center">
              <Percent className="w-5 h-5 text-bv-red-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Apply Discount</h2>
              <p className="text-sm text-gray-500 truncate max-w-[200px]">
                {item.productName}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4">
          {/* Item Info */}
          <div className="bg-gray-50 rounded-lg p-3 mb-4">
            <div className="flex justify-between text-sm">
              <span className="text-gray-600">Unit Price</span>
              <span className="font-medium">₹{item.unitPrice.toLocaleString('en-IN')}</span>
            </div>
            <div className="flex justify-between text-sm mt-1">
              <span className="text-gray-600">Quantity</span>
              <span className="font-medium">{item.quantity}</span>
            </div>
            <div className="flex justify-between text-sm mt-1 pt-2 border-t border-gray-200">
              <span className="font-medium text-gray-900">Item Total</span>
              <span className="font-bold">₹{calculations.itemTotal.toLocaleString('en-IN')}</span>
            </div>
          </div>

          {/* Discount Limit Notice */}
          <div className="flex items-center gap-2 mb-4 p-2 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700">
            <Tag className="w-4 h-4 flex-shrink-0" />
            <span>Your discount limit: {maxDiscountPercent}%</span>
          </div>

          {/* Discount Type Toggle */}
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => setDiscountType('percent')}
              className={clsx(
                'flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-colors',
                discountType === 'percent'
                  ? 'bg-bv-red-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              )}
            >
              Percentage (%)
            </button>
            <button
              onClick={() => setDiscountType('amount')}
              className={clsx(
                'flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-colors',
                discountType === 'amount'
                  ? 'bg-bv-red-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              )}
            >
              Amount (₹)
            </button>
          </div>

          {/* Discount Input */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              {discountType === 'percent' ? 'Discount Percentage' : 'Discount Amount'}
            </label>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 font-medium">
                {discountType === 'percent' ? '%' : '₹'}
              </span>
              <input
                type="number"
                value={inputValue}
                onChange={e => setInputValue(e.target.value)}
                className={clsx(
                  'input-field pl-8 text-lg font-bold',
                  calculations.exceedsLimit && 'border-red-500 focus:border-red-500 focus:ring-red-200'
                )}
                placeholder="0"
                min="0"
                max={discountType === 'percent' ? 100 : calculations.itemTotal}
                step={discountType === 'percent' ? '0.5' : '1'}
              />
            </div>

            {/* Exceeds Limit Warning */}
            {calculations.exceedsLimit && (
              <div className="flex items-center gap-2 mt-2 text-red-600 text-sm">
                <AlertTriangle className="w-4 h-4" />
                <span>
                  Exceeds your discount limit ({maxDiscountPercent}%). Request manager approval.
                </span>
              </div>
            )}
          </div>

          {/* Quick Discount Buttons */}
          <div className="mb-4">
            <label className="block text-sm text-gray-500 mb-2">Quick Select</label>
            <div className="flex flex-wrap gap-2">
              {QUICK_DISCOUNTS.filter(d => d <= maxDiscountPercent).map(percent => (
                <button
                  key={percent}
                  onClick={() => handleQuickDiscount(percent)}
                  className={clsx(
                    'px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                    discountType === 'percent' && parseFloat(inputValue) === percent
                      ? 'bg-bv-red-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  )}
                >
                  {percent}%
                </button>
              ))}
              {maxDiscountPercent > 0 && !QUICK_DISCOUNTS.includes(maxDiscountPercent) && (
                <button
                  onClick={() => handleQuickDiscount(maxDiscountPercent)}
                  className={clsx(
                    'px-4 py-2 rounded-lg text-sm font-medium transition-colors',
                    discountType === 'percent' && parseFloat(inputValue) === maxDiscountPercent
                      ? 'bg-bv-red-600 text-white'
                      : 'bg-bv-red-100 text-bv-red-600 hover:bg-bv-red-200'
                  )}
                >
                  Max ({maxDiscountPercent}%)
                </button>
              )}
            </div>
          </div>

          {/* Calculation Preview */}
          {parseFloat(inputValue) > 0 && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-4">
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">Discount</span>
                <span className="font-medium text-green-600">
                  -{calculations.discountPercent.toFixed(1)}% (₹{calculations.discountAmount.toLocaleString('en-IN')})
                </span>
              </div>
              <div className="flex justify-between mt-2 pt-2 border-t border-green-200">
                <span className="font-medium text-gray-900">Final Price</span>
                <span className="font-bold text-green-700">
                  ₹{calculations.finalPrice.toLocaleString('en-IN')}
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 flex justify-between">
          {item.discountAmount > 0 && (
            <button
              onClick={handleClearDiscount}
              className="text-red-600 hover:text-red-700 text-sm font-medium"
            >
              Remove Discount
            </button>
          )}
          <div className="flex gap-3 ml-auto">
            <button onClick={onClose} className="btn-outline">
              Cancel
            </button>
            <button
              onClick={handleApply}
              disabled={calculations.exceedsLimit || parseFloat(inputValue) < 0}
              className="btn-primary"
            >
              Apply Discount
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default DiscountModal;
