// ============================================================================
// IMS 2.0 - THE order body the till sends (pure: no store, no network)
// ============================================================================
// The sale, "Ask a manager to approve" and the quote all build from here, and
// THE bill fingerprint is derived from the very body the sale sends -- so
// "is this the same bill?" (the bill key, the approval check, a held bill's
// key) can never be answered from a second, drifting copy.

import { canonicalCategory } from '../../utils/categoryNormalize';

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
    stock_id: item.stock_id,
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

/** THE order body the till sends -- for the sale, and for "Ask a manager to
    approve" (the same bill, so the server prices exactly what will be sold).
    WHO approved a discount is the server's decision: no approver name is
    ever sent from here (owner 2026-10-18). */
export function orderPayloadOf(store: any, discountApprovalId?: string | null) {
  return {
    customer_id: store.customer?.id,
    // BILL-TO-MEMBER P1: send the selected member so the order bills to a
    // member, not the bare account. Omitted for walk-ins (the backend
    // synthesizes a Primary for the synthetic account).
    patient_id: store.patient?.id || undefined,
    store_id: store.store_id,
    order_type: store.sale_type,
    salesperson_id: store.salesperson_id,
    salesperson_name: store.salesperson_name,
    items: orderLinesOf(store.cart),
    notes: store.cart_note || undefined,
    // Phase 6.7 — pass delivery + cart-discount fields through to backend
    delivery_date: store.delivery_date || undefined,
    delivery_time_slot: store.delivery_time_slot || undefined,
    delivery_priority: store.delivery_priority || 'NORMAL',
    cart_discount_percent: store.cart_discount_percent || 0,
    cart_discount_amount: store.cart_discount_amount || 0,
    cart_discount_reason: store.cart_discount_reason || undefined,
    // Over the seller's limit the sale carries a manager-approved request id
    // for exactly this bill.
    discount_approval_id: discountApprovalId || undefined,
  };
}

/** THE bill fingerprint: the order body the sale sends, minus the manager's
    approval id (asking a manager does not make it another bill) and minus the
    sale type (worked out again at each press from the Rx). The member stays:
    another family member's identical bill is another sale (money review 17).
    A reload keeps the bill's key because the saved draft keeps the customer,
    member and Rx while an unconfirmed press of it is pending (posStore). */
export function billFingerprint(store: any): string {
  const { order_type: _saleType, ...body } = orderPayloadOf(store);
  return JSON.stringify(body);
}
