// ============================================================================
// IMS 2.0 - Order Summary Component
// ============================================================================

import clsx from 'clsx';

interface OrderSummaryProps {
  subtotal: number;
  totalDiscount: number;
  gstAmount: number;
  grandTotal: number;
  amountPaid?: number;
  balanceDue?: number;
  compact?: boolean;
}

export function OrderSummary({
  subtotal,
  totalDiscount,
  gstAmount,
  grandTotal,
  amountPaid,
  balanceDue,
  compact = false,
}: OrderSummaryProps) {
  const formatCurrency = (amount: number) => {
    return `₹${amount.toLocaleString('en-IN', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    })}`;
  };

  return (
    <div className={clsx('space-y-2', compact && 'text-sm')}>
      {/* Subtotal */}
      <div className="flex justify-between text-gray-600">
        <span>Subtotal</span>
        <span>{formatCurrency(subtotal)}</span>
      </div>

      {/* Discount */}
      {totalDiscount > 0 && (
        <div className="flex justify-between text-green-600">
          <span>Discount</span>
          <span>-{formatCurrency(totalDiscount)}</span>
        </div>
      )}

      {/* GST */}
      <div className="flex justify-between text-gray-600">
        <span>GST (18%)</span>
        <span>{formatCurrency(gstAmount)}</span>
      </div>

      {/* Grand Total */}
      <div className={clsx(
        'flex justify-between pt-2 border-t border-gray-200',
        !compact && 'text-lg'
      )}>
        <span className="font-semibold text-gray-900">Grand Total</span>
        <span className="font-bold text-gray-900">{formatCurrency(grandTotal)}</span>
      </div>

      {/* Payment Info (if provided) */}
      {amountPaid !== undefined && balanceDue !== undefined && (
        <>
          {amountPaid > 0 && (
            <div className="flex justify-between text-gray-600">
              <span>Amount Paid</span>
              <span className="text-green-600">{formatCurrency(amountPaid)}</span>
            </div>
          )}
          {balanceDue > 0 && (
            <div className="flex justify-between pt-1">
              <span className="font-medium text-red-600">Balance Due</span>
              <span className="font-bold text-red-600">{formatCurrency(balanceDue)}</span>
            </div>
          )}
          {balanceDue <= 0 && amountPaid > 0 && (
            <div className="flex justify-between pt-1">
              <span className="font-medium text-green-600">Fully Paid</span>
              <span className="font-bold text-green-600">✓</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default OrderSummary;
