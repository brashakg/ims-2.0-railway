// ============================================================================
// IMS 2.0 - The till's bill total comes from the server (POST /orders/quote)
// ============================================================================
// Owner ruling 2026-10-08: every till bill is rounded to the nearest rupee.
// The cashier collects BEFORE the order exists, so the till must collect the
// rupee the order will bill -- and only the server knows it: its line math
// and GST engine are the ones create uses. A till-side copy of that math
// (rounding its own per-line paise) once quoted a Rs 265 frame with 1.5% line
// and 2.5% bill discount at Rs 254 while create billed Rs 255. So the till
// never rounds a bill: it asks the quote door (same pattern as the Returns
// screen's POST /returns/quote) and shows its own figure only as an estimate
// while the quote is in flight. submitPosOrder takes a fresh quote before it
// checks the tenders, so money is always checked against the server's rupee.

import { useEffect } from 'react';
import { orderApi } from '../../services/api';
import { canonicalCategory } from '../../utils/categoryNormalize';
import { billQuoteKey, usePOSStore } from '../../stores/posStore';

export function mapCategory(cat: string): string {
  // item_type vocabulary for the order payload (drives backend GST item_type-
  // wins). Canonicalise the input first so EVERY category spelling (short code,
  // plural, canonical) resolves; outputs are unchanged from the legacy map.
  const canonical = canonicalCategory(cat);
  const map: Record<string, string> = {
    FRAME: 'FRAME', SUNGLASS: 'SUNGLASS', OPTICAL_LENS: 'LENS',
    CONTACT_LENS: 'CONTACT_LENS', COLORED_CONTACT_LENS: 'CONTACT_LENS',
    ACCESSORIES: 'ACCESSORY', WATCH: 'WATCH', SMARTWATCH: 'SMARTWATCH', SERVICES: 'SERVICE',
  };
  return map[canonical] || canonical || cat;
}

/** THE order lines the till sends -- to create the order AND to price it, so
 *  the quote is always for exactly the lines the order will carry. */
export function orderLinesOf(cart: any[]) {
  return (cart || []).map((item: any) => ({
    item_type: mapCategory(item.category),
    product_id: item.product_id,
    product_name: item.name,
    sku: item.sku,
    brand: item.brand,
    subbrand: item.subbrand,
    category: item.category,
    quantity: item.quantity,
    unit_price: item.unit_price,
    discount_percent: item.discount_percent,
    discount_reason: item.discount_reason || undefined,
    prescription_id: item.linked_prescription_id,
    lens_details: item.lens_details,
    item_note: item.item_note || undefined,
  }));
}

/** Ask the server for the live cart's bill and keep it on the store. Throws
 *  when the server cannot be asked; a cart edited while the call was in
 *  flight is left for the next call (its quote would be stale). */
export async function refreshBillQuote(): Promise<void> {
  const s = usePOSStore.getState();
  const key = billQuoteKey(s);
  if (!(s.cart || []).length) {
    s.setBillQuote(null);
    return;
  }
  const q = await orderApi.quoteBill({
    items: orderLinesOf(s.cart),
    cart_discount_percent: s.cart_discount_percent || 0,
  });
  if (billQuoteKey(usePOSStore.getState()) === key) {
    usePOSStore.getState().setBillQuote({
      key,
      grand_total: q.grand_total,
      round_off: q.round_off,
      tax: q.tax,
      total_discount: q.total_discount,
    });
  }
}

/** Keep the cart's quote fresh while the cashier edits it (debounced). A
 *  failure leaves the estimate on screen; Pay takes its own fresh quote and
 *  refuses without one. */
export function useBillQuote(): void {
  const key = usePOSStore((s) => billQuoteKey(s));
  useEffect(() => {
    if (usePOSStore.getState().getQuotedBill()) return;
    const timer = setTimeout(() => {
      refreshBillQuote().catch(() => undefined);
    }, 250);
    return () => clearTimeout(timer);
  }, [key]);
}
