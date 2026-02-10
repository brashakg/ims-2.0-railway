import { useState, useMemo } from 'react';
import { Percent, Tag, TrendingDown } from 'lucide-react';
import type { CartItem, Customer } from './POSLayout';

interface BillingEngineProps {
  cartItems: CartItem[];
  selectedCustomer: Customer | null;
  onUpdateQuantity: (productId: string, quantity: number) => void;
  onRemoveItem: (productId: string) => void;
  onApplyDiscount: (productId: string, discountPercent: number) => void;
  onComplete: (billData: any) => void;
  onBack: () => void;
}

interface BillCalculation {
  subtotal: number;
  item_discount: number;
  subtotal_after_item_discount: number;
  order_discount: number;
  order_discount_amount: number;
  taxable_amount: number;
  cgst_amount: number;
  sgst_amount: number;
  igst_amount: number;
  total_gst: number;
  roundoff_amount: number;
  total_amount: number;
}

export function BillingEngine({
  cartItems,
  selectedCustomer,
  onUpdateQuantity,
  onRemoveItem,
  onComplete,
  onBack,
}: BillingEngineProps) {
  const [orderDiscountPercent, setOrderDiscountPercent] = useState(0);
  const [useIGST, setUseIGST] = useState(false);
  const [applyCoupon, setApplyCoupon] = useState('');

  // Calculate bill
  const billCalculation = useMemo((): BillCalculation => {
    // Subtotal with item-level discounts
    let subtotal = 0;
    let itemDiscount = 0;

    cartItems.forEach(item => {
      const itemSubtotal = item.unit_price * item.quantity;
      subtotal += itemSubtotal;
      if (item.discount_percent) {
        itemDiscount += itemSubtotal * (item.discount_percent / 100);
      }
    });

    const subtotalAfterItemDiscount = subtotal - itemDiscount;

    // Order-level discount
    const orderDiscountAmount = subtotalAfterItemDiscount * (orderDiscountPercent / 100);
    const taxableAmount = subtotalAfterItemDiscount - orderDiscountAmount;

    // GST Calculation (18% standard for optical goods in India)
    // Assuming CGST + SGST = 18% for intra-state
    // IGST = 18% for inter-state
    const gstRate = 0.18;
    let cgstAmount = 0;
    let sgstAmount = 0;
    let igstAmount = 0;

    if (useIGST) {
      igstAmount = taxableAmount * gstRate;
    } else {
      // Split GST equally (9% CGST + 9% SGST)
      cgstAmount = taxableAmount * (gstRate / 2);
      sgstAmount = taxableAmount * (gstRate / 2);
    }

    const totalGst = cgstAmount + sgstAmount + igstAmount;

    // Calculate total before round-off
    let totalBeforeRoundoff = taxableAmount + totalGst;

    // Round-off to nearest rupee
    const roundoffAmount = Math.round(totalBeforeRoundoff) - totalBeforeRoundoff;
    const totalAmount = Math.round(totalBeforeRoundoff);

    return {
      subtotal,
      item_discount: itemDiscount,
      subtotal_after_item_discount: subtotalAfterItemDiscount,
      order_discount: orderDiscountPercent,
      order_discount_amount: orderDiscountAmount,
      taxable_amount: taxableAmount,
      cgst_amount: cgstAmount,
      sgst_amount: sgstAmount,
      igst_amount: igstAmount,
      total_gst: totalGst,
      roundoff_amount: roundoffAmount,
      total_amount: totalAmount,
    };
  }, [cartItems, orderDiscountPercent, useIGST]);

  const handleApplyCoupon = () => {
    // Mock coupon codes
    const coupons: { [key: string]: number } = {
      'SAVE10': 10,
      'SUMMER': 15,
      'LOYAL': 20,
    };

    if (coupons[applyCoupon]) {
      setOrderDiscountPercent(coupons[applyCoupon]);
      setApplyCoupon('');
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Items Review */}
      <div className="space-y-4">
        <h3 className="text-lg font-bold text-white">Order Items</h3>
        {cartItems.map(item => (
          <div key={item.product_id} className="bg-gray-700 rounded-lg p-4">
            <div className="flex justify-between items-start mb-2">
              <div>
                <p className="font-semibold text-white">{item.name}</p>
                <p className="text-xs text-gray-400">{item.sku}</p>
              </div>
              <button
                onClick={() => onRemoveItem(item.product_id)}
                className="text-red-400 hover:text-red-300 text-sm"
              >
                Remove
              </button>
            </div>

            <div className="grid grid-cols-3 gap-4 mb-3">
              <div>
                <p className="text-xs text-gray-400">Unit Price</p>
                <p className="text-white font-medium">₹{item.unit_price.toLocaleString('en-IN')}</p>
              </div>
              <div>
                <p className="text-xs text-gray-400">Qty</p>
                <div className="flex gap-1">
                  <button
                    onClick={() => onUpdateQuantity(item.product_id, item.quantity - 1)}
                    className="bg-gray-800 text-white px-2 py-1 rounded text-sm hover:bg-gray-600"
                  >
                    -
                  </button>
                  <span className="bg-gray-800 text-white px-2 py-1 rounded text-sm w-8 text-center">
                    {item.quantity}
                  </span>
                  <button
                    onClick={() => onUpdateQuantity(item.product_id, item.quantity + 1)}
                    className="bg-gray-800 text-white px-2 py-1 rounded text-sm hover:bg-gray-600"
                  >
                    +
                  </button>
                </div>
              </div>
              <div>
                <p className="text-xs text-gray-400">Amount</p>
                <p className="text-white font-medium">
                  ₹{(item.unit_price * item.quantity).toLocaleString('en-IN')}
                </p>
              </div>
            </div>

            {/* Item Discount */}
            {item.discount_percent && (
              <div className="bg-gray-800 rounded px-3 py-2 flex items-center gap-2">
                <Percent className="w-4 h-4 text-amber-500" />
                <span className="text-sm text-amber-300">
                  {item.discount_percent}% discount: -₹{(item.unit_price * item.quantity * item.discount_percent / 100).toFixed(2)}
                </span>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Discounts Section */}
      <div className="bg-gray-700 rounded-lg p-4 space-y-3">
        <h4 className="font-semibold text-white flex items-center gap-2">
          <Tag className="w-4 h-4" />
          Discounts & Promotions
        </h4>

        {/* Coupon Code */}
        <div className="space-y-2">
          <label className="text-sm text-gray-300">Coupon Code</label>
          <div className="flex gap-2">
            <input
              type="text"
              value={applyCoupon}
              onChange={(e) => setApplyCoupon(e.target.value.toUpperCase())}
              placeholder="e.g., SAVE10, SUMMER"
              className="flex-1 bg-gray-800 text-white border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={handleApplyCoupon}
              className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded font-medium text-sm transition-colors"
            >
              Apply
            </button>
          </div>
          <p className="text-xs text-gray-400">Available: SAVE10 (10%), SUMMER (15%), LOYAL (20%)</p>
        </div>

        {/* Order Discount % */}
        <div className="space-y-2">
          <label className="text-sm text-gray-300">Order Discount %</label>
          <input
            type="number"
            value={orderDiscountPercent}
            onChange={(e) => setOrderDiscountPercent(Math.max(0, Math.min(100, parseFloat(e.target.value) || 0)))}
            min="0"
            max="100"
            className="w-full bg-gray-800 text-white border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-blue-500"
          />
          <p className="text-xs text-gray-400">Discount Amount: ₹{billCalculation.order_discount_amount.toFixed(2)}</p>
        </div>

        {/* GST Type Selection */}
        <div className="space-y-2">
          <label className="text-sm text-gray-300">Tax Type</label>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                checked={!useIGST}
                onChange={() => setUseIGST(false)}
                className="w-4 h-4"
              />
              <span className="text-sm text-gray-300">CGST + SGST (Intra-state)</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                checked={useIGST}
                onChange={() => setUseIGST(true)}
                className="w-4 h-4"
              />
              <span className="text-sm text-gray-300">IGST (Inter-state)</span>
            </label>
          </div>
        </div>
      </div>

      {/* Bill Summary */}
      <div className="bg-gradient-to-b from-blue-900 to-purple-900 rounded-lg p-4 space-y-2 text-sm">
        <div className="flex justify-between text-gray-300">
          <span>Subtotal</span>
          <span>₹{billCalculation.subtotal.toFixed(2)}</span>
        </div>

        {billCalculation.item_discount > 0 && (
          <div className="flex justify-between text-amber-300">
            <span>Item Discounts</span>
            <span>-₹{billCalculation.item_discount.toFixed(2)}</span>
          </div>
        )}

        <div className="flex justify-between text-gray-300">
          <span>Subtotal After Discounts</span>
          <span>₹{billCalculation.subtotal_after_item_discount.toFixed(2)}</span>
        </div>

        {billCalculation.order_discount_amount > 0 && (
          <div className="flex justify-between text-amber-300">
            <span>Order Discount ({billCalculation.order_discount}%)</span>
            <span>-₹{billCalculation.order_discount_amount.toFixed(2)}</span>
          </div>
        )}

        <div className="border-t border-purple-700 pt-2 my-2">
          <div className="flex justify-between text-gray-300">
            <span>Taxable Amount</span>
            <span>₹{billCalculation.taxable_amount.toFixed(2)}</span>
          </div>
        </div>

        {useIGST ? (
          <div className="flex justify-between text-gray-300">
            <span>IGST (18%)</span>
            <span>₹{billCalculation.igst_amount.toFixed(2)}</span>
          </div>
        ) : (
          <>
            <div className="flex justify-between text-gray-300">
              <span>CGST (9%)</span>
              <span>₹{billCalculation.cgst_amount.toFixed(2)}</span>
            </div>
            <div className="flex justify-between text-gray-300">
              <span>SGST (9%)</span>
              <span>₹{billCalculation.sgst_amount.toFixed(2)}</span>
            </div>
          </>
        )}

        {Math.abs(billCalculation.roundoff_amount) > 0.01 && (
          <div className="flex justify-between text-gray-300">
            <span>Round-off Adjustment</span>
            <span>₹{billCalculation.roundoff_amount.toFixed(2)}</span>
          </div>
        )}

        <div className="border-t border-purple-700 pt-2 mt-2 flex justify-between font-bold text-lg text-green-400">
          <span>Total Amount</span>
          <span>₹{billCalculation.total_amount}</span>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex gap-4">
        <button
          onClick={onBack}
          className="flex-1 bg-gray-700 hover:bg-gray-600 text-white py-3 rounded-lg font-semibold transition-colors"
        >
          Back to Catalog
        </button>
        <button
          onClick={() => onComplete({
            ...billCalculation,
            bill_number: `BIL-${Date.now()}`,
            customer: selectedCustomer,
            items: cartItems,
          })}
          className="flex-1 bg-green-600 hover:bg-green-700 text-white py-3 rounded-lg font-semibold transition-colors flex items-center justify-center gap-2"
        >
          <TrendingDown className="w-5 h-5" />
          Proceed to Payment
        </button>
      </div>
    </div>
  );
}

export default BillingEngine;
