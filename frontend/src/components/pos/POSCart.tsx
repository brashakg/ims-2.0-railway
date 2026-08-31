// ============================================================================
// IMS 2.0 - POS Cart Sidebar
// ============================================================================
// Extracted from POSLayout.tsx — displays cart items, quantities, totals
// in the right column during products / review / prescription steps.
// Styled with the new design tokens (light surface) to match the rest of
// the app after Phase 1.6 POS re-skin.

import { ShoppingCart, X } from 'lucide-react';
import { usePOSStore, type CartLineItem } from '../../stores/posStore';

/** Next free pair label for a cart ("Pair 1", "Pair 2", …). Gaps left by a
    removed pair are reused, so labels stay small and stable. */
export function nextPairId(cart: { pair_id?: string }[]): string {
  const used = new Set(cart.map((i) => i.pair_id).filter(Boolean) as string[]);
  let n = 1;
  while (used.has(`Pair ${n}`)) n += 1;
  return `Pair ${n}`;
}

/** `onOpenDiscount` is OPTIONAL on purpose. The classic surface opens the
 *  discount modal from its review step, so it passes nothing and its cart is
 *  byte-identical to before. The new one-screen surfaces have no review step -
 *  the cart IS the review - so they pass a handler and each line grows a
 *  Discount control. */
export function CartSidebar({
  onOpenDiscount,
}: {
  onOpenDiscount?: (line: CartLineItem) => void;
} = {}) {
  const store = usePOSStore();
  const opticalCount = (store.cart || []).filter((i) => i.is_optical).length;
  const pairIds = Array.from(
    new Set((store.cart || []).map((i) => i.pair_id).filter(Boolean) as string[])
  ).sort();
  const subtotal = store.getSubtotal();
  const grand = store.getGrandTotal();
  const totalDiscount = store.getTotalDiscount();
  // GST-inclusive: GST is the tax extracted from WITHIN the (inclusive)
  // grand total, not added on top. See posStore.getTax.
  const gst = store.getTax();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div
        style={{
          padding: '14px 16px',
          borderBottom: '1px solid var(--line)',
          background: 'var(--surface)',
        }}
      >
        <h3
          style={{
            margin: 0,
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--ink)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <ShoppingCart className="w-4 h-4" />
          Cart · {(store.cart || []).length}
        </h3>
        {store.salesperson_name && (
          <p
            className="mono"
            style={{ margin: '4px 0 0', fontSize: 10.5, color: 'var(--ink-4)' }}
          >
            Sales: {store.salesperson_name}
          </p>
        )}
      </div>

      {/* Line items */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        {(store.cart || []).length === 0 && (
          <div
            style={{
              color: 'var(--ink-4)',
              fontSize: 12,
              textAlign: 'center',
              padding: '32px 12px',
            }}
          >
            Empty cart
          </div>
        )}
        {(store.cart || []).map((item) => (
          <div
            key={item.id}
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: 8,
              padding: 10,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p
                  style={{
                    margin: 0,
                    fontSize: 13,
                    fontWeight: 500,
                    color: 'var(--ink)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {item.name}
                </p>
                <p style={{ margin: '2px 0 0', fontSize: 11, color: 'var(--ink-4)' }}>{item.brand}</p>
                {item.lens_details && (
                  <p style={{ margin: '2px 0 0', fontSize: 11, color: 'var(--info)' }}>
                    {item.lens_details.type}
                  </p>
                )}
              </div>
              <button
                onClick={() => store.removeFromCart(item.id)}
                className="btn icon ghost sm"
                style={{ marginLeft: 6 }}
                aria-label={`Remove ${item.name}`}
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginTop: 8,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <button
                  onClick={() => store.updateQuantity(item.id, item.quantity - 1)}
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: 4,
                    border: '1px solid var(--line-strong)',
                    background: 'var(--surface)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    cursor: 'pointer',
                  }}
                  aria-label="Decrease quantity"
                >
                  −
                </button>
                <input
                  type="number"
                  min="1"
                  max="99"
                  value={item.quantity}
                  aria-label={`Quantity for ${item.name}`}
                  onChange={(e) => {
                    const v = parseInt(e.target.value) || 1;
                    store.updateQuantity(item.id, Math.max(1, Math.min(99, v)));
                  }}
                  onFocus={(e) => e.target.select()}
                  style={{
                    width: 38,
                    textAlign: 'center',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    border: '1px solid var(--line-strong)',
                    borderRadius: 4,
                    padding: '2px 4px',
                    background: 'var(--surface)',
                    color: 'var(--ink)',
                  }}
                />
                <button
                  onClick={() => store.updateQuantity(item.id, item.quantity + 1)}
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: 4,
                    border: '1px solid var(--line-strong)',
                    background: 'var(--surface)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    cursor: 'pointer',
                  }}
                  aria-label="Increase quantity"
                >
                  +
                </button>
              </div>
              <div style={{ textAlign: 'right' }}>
                {item.discount_percent > 0 && (
                  <span style={{ fontSize: 11, color: 'var(--ok)', marginRight: 6 }}>
                    −{item.discount_percent}%
                  </span>
                )}
                <span
                  className="figure"
                  style={{
                    fontSize: 13,
                    color: 'var(--ink)',
                  }}
                >
                  ₹{Math.round(item.line_total).toLocaleString('en-IN')}
                </span>
              </div>
            </div>
            {onOpenDiscount && (
              <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                <button
                  type="button"
                  onClick={() => onOpenDiscount(item)}
                  style={{
                    minHeight: 32,
                    padding: '0 10px',
                    borderRadius: 6,
                    fontSize: 11,
                    fontWeight: 500,
                    cursor: 'pointer',
                    border: '1px solid var(--line)',
                    background: 'var(--surface)',
                    color: 'var(--ink-2)',
                  }}
                >
                  {item.discount_percent > 0 ? `Discount ${item.discount_percent}%` : 'Discount'}
                </button>
                {item.discount_percent > 0 && item.discount_reason && (
                  <span style={{ fontSize: 11, color: 'var(--ink-3)' }} title={item.discount_reason}>
                    {item.discount_reason.slice(0, 28)}
                  </span>
                )}
              </div>
            )}
            {/* PAIR LINKING (owner spec 4): one bill can carry several
                Rx+frame PAIRS for the same customer. Shown only when the cart
                actually holds more than one optical line — a single-pair sale
                (the common case) sees no extra chrome. */}
            {item.is_optical && opticalCount > 1 && (
              <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {pairIds.map((pid) => (
                  <button
                    key={pid}
                    type="button"
                    onClick={() => store.setLinePair(item.id, item.pair_id === pid ? null : pid)}
                    style={{
                      minHeight: 30,
                      padding: '0 10px',
                      borderRadius: 6,
                      fontSize: 11,
                      fontWeight: 500,
                      cursor: 'pointer',
                      border: '1px solid var(--line)',
                      background: item.pair_id === pid ? 'var(--ink)' : 'var(--surface)',
                      color: item.pair_id === pid ? '#fff' : 'var(--ink-2)',
                    }}
                  >
                    {pid}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => store.setLinePair(item.id, nextPairId(store.cart || []))}
                  style={{
                    minHeight: 30,
                    padding: '0 10px',
                    borderRadius: 6,
                    fontSize: 11,
                    cursor: 'pointer',
                    border: '1px dashed var(--line-strong, var(--line))',
                    background: 'transparent',
                    color: 'var(--ink-3)',
                  }}
                >
                  + New pair
                </button>
              </div>
            )}
            {item.is_optical && (
              <input
                placeholder="PD / Fitting / Tint notes…"
                value={item.item_note || ''}
                onChange={(e) => store.updateItemNote(item.id, e.target.value)}
                style={{
                  marginTop: 6,
                  width: '100%',
                  padding: '4px 8px',
                  fontSize: 10.5,
                  border: '1px solid var(--line)',
                  borderRadius: 4,
                  background: 'var(--surface-2)',
                  color: 'var(--ink)',
                }}
              />
            )}
          </div>
        ))}
      </div>

      {/* Totals footer */}
      <div
        style={{
          borderTop: '1px solid var(--line)',
          padding: 14,
          background: 'var(--surface-2)',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          fontSize: 12.5,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--ink-3)' }}>
          <span>Subtotal</span>
          <span className="figure" style={{ fontSize: 'inherit' }}>₹{Math.round(subtotal).toLocaleString('en-IN')}</span>
        </div>
        {totalDiscount > 0 && (
          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--ok)' }}>
            <span>Discount</span>
            <span className="figure" style={{ fontSize: 'inherit' }}>−₹{Math.round(totalDiscount).toLocaleString('en-IN')}</span>
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--ink-3)' }}>
          <span>GST</span>
          <span className="figure" style={{ fontSize: 'inherit' }}>₹{Math.round(gst).toLocaleString('en-IN')}</span>
        </div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontWeight: 600,
            fontSize: 14,
            paddingTop: 8,
            marginTop: 4,
            borderTop: '1px solid var(--line-strong)',
            color: 'var(--ink)',
          }}
        >
          <span>Total (incl. GST)</span>
          <span className="figure" style={{ color: 'var(--bv)', fontSize: 'inherit' }}>
            ₹{Math.round(grand).toLocaleString('en-IN')}
          </span>
        </div>
      </div>
    </div>
  );
}

export default CartSidebar;
