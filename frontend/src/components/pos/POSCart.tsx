// ============================================================================
// IMS 2.0 - POS Cart Sidebar
// ============================================================================
// Extracted from POSLayout.tsx — displays cart items, quantities, totals
// in the right sidebar during products/review/prescription steps.

import { ShoppingCart, X } from 'lucide-react';
import { usePOSStore } from '../../stores/posStore';

export function CartSidebar() {
  const store = usePOSStore();
  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-gray-700">
        <h3 className="font-semibold text-white flex items-center gap-2"><ShoppingCart className="w-4 h-4" /> Cart ({(store.cart || []).length})</h3>
        {store.salesperson_name && <p className="text-[10px] text-gray-400 mt-0.5">Sales: {store.salesperson_name}</p>}
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {(store.cart || []).map(item => (
          <div key={item.id} className="bg-gray-800 rounded-lg p-3">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0"><p className="text-sm font-medium text-white truncate">{item.name}</p><p className="text-xs text-gray-500">{item.brand}</p>
                {item.lens_details && <p className="text-xs text-purple-500">{item.lens_details.type}</p>}
              </div>
              <button onClick={() => store.removeFromCart(item.id)} className="text-gray-400 hover:text-red-500 ml-2"><X className="w-3.5 h-3.5" /></button>
            </div>
            <div className="flex items-center justify-between mt-2">
              <div className="flex items-center gap-1">
                <button onClick={() => store.updateQuantity(item.id, item.quantity - 1)} className="w-6 h-6 rounded bg-gray-800 border text-xs hover:bg-gray-700">-</button>
                <input type="number" min="1" max="99" value={item.quantity}
                  aria-label={`Quantity for ${item.name}`}
                  onChange={(e) => { const v = parseInt(e.target.value) || 1; store.updateQuantity(item.id, Math.max(1, Math.min(99, v))); }}
                  onFocus={(e) => e.target.select()}
                  className="w-10 text-center text-xs font-medium border border-gray-700 rounded px-1 py-0.5 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" />
                <button onClick={() => store.updateQuantity(item.id, item.quantity + 1)} className="w-6 h-6 rounded bg-gray-800 border text-xs hover:bg-gray-700">+</button>
              </div>
              <div className="text-right">{item.discount_percent > 0 && <span className="text-xs text-green-600 mr-1">-{item.discount_percent}%</span>}<span className="text-sm font-semibold">{'\u20B9'}{Math.round(item.line_total).toLocaleString('en-IN')}</span></div>
            </div>
            {/* Item-level notes: PD, fitting, tint, coating */}
            {item.is_optical && (
              <input placeholder="PD / Fitting / Tint notes..." value={item.notes || ''} onChange={(e) => store.updateItemNote(item.id, e.target.value)}
                className="mt-1.5 w-full px-2 py-1 text-[10px] border border-gray-700 rounded bg-gray-800 placeholder:text-gray-300 focus:border-purple-300 focus:ring-1 focus:ring-purple-200" />
            )}
          </div>
        ))}
      </div>
      <div className="border-t border-gray-700 p-4 space-y-1 text-sm">
        <div className="flex justify-between text-gray-500"><span>Subtotal</span><span>{'\u20B9'}{Math.round(store.getSubtotal()).toLocaleString('en-IN')}</span></div>
        {store.getTotalDiscount() > 0 && <div className="flex justify-between text-green-600"><span>Discount</span><span>-{'\u20B9'}{Math.round(store.getTotalDiscount()).toLocaleString('en-IN')}</span></div>}
        <div className="flex justify-between text-gray-500"><span>GST</span><span>{'\u20B9'}{Math.round(store.getGrandTotal() - store.getSubtotal() + store.getTotalDiscount()).toLocaleString('en-IN')}</span></div>
        <div className="flex justify-between font-bold text-base pt-1 border-t border-gray-700"><span>Total (incl. GST)</span><span className="text-bv-gold-600">{'\u20B9'}{Math.round(store.getGrandTotal()).toLocaleString('en-IN')}</span></div>
      </div>
    </div>
  );
}

export default CartSidebar;
