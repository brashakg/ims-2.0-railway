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
import { billQuoteKey, usePOSStore } from '../../stores/posStore';
import { orderLinesOf } from './orderPayload';

// The order-line vocabulary and builder live in orderPayload (pure: posStore
// reads the bill's fingerprint from it without importing this file).
export { mapCategory, orderLinesOf } from './orderPayload';

/** Ask the server for the live cart's bill and keep it on the store. Throws
 *  when the server cannot be asked; a cart edited while the call was in
 *  flight is left for the next call (its quote would be stale). */
// Whether the server guarantees a re-pressed sale replays (its unique
// idempotency index is in place) -- read from the last quote. Only then may
// the till promise "it will not be charged twice".
let lastReplayGuaranteed = false;
export const replayGuaranteed = (): boolean => lastReplayGuaranteed;

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
  lastReplayGuaranteed = Boolean((q as any)?.replay_guaranteed);
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
