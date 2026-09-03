// ============================================================================
// IMS 2.0 - POS order submit (THE single submit path)
// ============================================================================
// Extracted VERBATIM from POSLayout.handleCreateOrder (Wave 4) so the classic
// POS and the new one-surface POS share ONE submit brain — payload assembly,
// idempotency semantics, deferred loyalty redeem, tender recording via
// paymentBody, and the workshop-job auto-create. Two live surfaces with two
// copies of this sequence is the repo's documented dominant defect class;
// never re-inline it. Callers own only UI concerns: minting/holding the
// idempotency key, surfacing error/warning strings, and opening the fitting
// modal when `fittingJobId` comes back.

import { orderApi, loyaltyApi, workshopApi } from '../../services/api';
import { canonicalCategory } from '../../utils/categoryNormalize';
import type { CashTenderCapture } from '../../stores/posStore';
import { buildPaymentBody } from './paymentBody';

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

export interface SubmitPosOrderResult {
  ok: boolean;
  orderId?: string;
  orderNumber?: string;
  /** Set when a workshop job was auto-created: the caller opens the fitting
      modal (which advances to 'complete' itself) instead of setStep. */
  fittingJobId?: string;
  fittingCoating?: string;
  /** Non-fatal notice (order IS created) — e.g. workshop auto-create failed. */
  warning?: string;
  /** Fatal — order was NOT created. */
  error?: string;
}

/** `store` is the bound posStore instance (usePOSStore()). Validations run
    here too so both surfaces refuse identically. */
export async function submitPosOrder(
  store: any,
  idempotencyKey: string,
): Promise<SubmitPosOrderResult> {
  if (store.sale_type === 'prescription_order') {
    const hasLens = (store.cart || []).some((i: any) =>
      canonicalCategory(i.category) === 'OPTICAL_LENS' || i.lens_details || i.is_optical
    );
    if (!hasLens) {
      return {
        ok: false,
        error: 'Prescription order requires at least one lens item. Add lenses or switch to Quick Sale.',
      };
    }
  }

  if (store.getBalance() > 0.01 && !store.is_advance_payment) {
    return { ok: false, error: 'Payment incomplete. Add payments or enable "Advance payment only".' };
  }

  // OVER-TENDER. Only under-payment was checked, so a bill whose total DROPPED
  // after the tender was entered sailed through: the one-screen surfaces show
  // the discount controls and the payment card at the same time, so applying a
  // discount after taking the cash is a two-tap sequence. The order was then
  // created at the NEW lower total, every addPayment below was refused by the
  // server with "amount exceeds balance due", and the catch swallowed it - an
  // order saved as fully OUTSTANDING with the cash physically in the drawer,
  // surfacing at day-end as an unexplained surplus. Refuse BEFORE creating
  // anything, so nothing is half-saved.
  const overTendered = -store.getBalance();
  if (overTendered > 0.01) {
    return {
      ok: false,
      error:
        `Tendered ₹${Math.round(store.getTotalPaid()).toLocaleString('en-IN')} is ` +
        `₹${Math.round(overTendered).toLocaleString('en-IN')} more than the bill ` +
        `total of ₹${Math.round(store.getGrandTotal()).toLocaleString('en-IN')}. ` +
        `If a discount was applied after the payment, re-enter the payment.`,
    };
  }

  try {
    const result = await orderApi.createOrder({
      customer_id: store.customer?.id,
      // BILL-TO-MEMBER P1: send the selected member so the order bills to a
      // member, not the bare account. Omitted for walk-ins (the backend
      // synthesizes a Primary for the synthetic account).
      patient_id: store.patient?.id || undefined,
      store_id: store.store_id,
      order_type: store.sale_type,
      salesperson_id: store.salesperson_id,
      salesperson_name: store.salesperson_name,
      visufit_id: store.visufit_id || undefined,
      items: (store.cart || []).map((item: any) => ({
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
      })),
      notes: store.cart_note || undefined,
      // Phase 6.7 — pass delivery + cart-discount fields through to backend
      delivery_date: store.delivery_date || undefined,
      delivery_time_slot: store.delivery_time_slot || undefined,
      delivery_priority: store.delivery_priority || 'NORMAL',
      cart_discount_percent: store.cart_discount_percent || 0,
      cart_discount_amount: store.cart_discount_amount || 0,
      cart_discount_reason: store.cart_discount_reason || undefined,
      cart_discount_approved_by: store.cart_discount_approved_by || undefined,
    } as any, idempotencyKey || undefined);

    if (!result?.order_id) {
      return { ok: false, error: 'Order created but no ID returned. Check order list.' };
    }

    // POS-3: loyalty points are only atomically debited AFTER the order
    // is confirmed (a failed order create burns nothing). /loyalty/redeem
    // runs now with the real order_id so the ledger is linked; the server's
    // guarded find_one_and_update is THE one burn path — never re-implement
    // it client-side. BURN FIRST, TENDER SECOND: the redeem response is the
    // only authority on what the burn was actually worth (the server may cap
    // points to balance / order value), and the LOYALTY tender recorded on
    // the order below copies its numbers exactly. Recording the tender first
    // would bank rupees against a burn that can still refuse or shrink —
    // the same class as the delivery-door bug (payment recorded, action
    // refused). If the redeem fails, NO LOYALTY leg is posted: the points
    // are untouched and the order truthfully shows that amount still due,
    // surfaced as a loud warning — never silently.
    const pendingLoyalty = store.pendingLoyaltyRedeem;
    let loyaltyLeg: { amount: number; reference: string } | null = null;
    let loyaltyWarning: string | undefined;
    if (pendingLoyalty && store.customer?.id) {
      try {
        const redeemed = await loyaltyApi.redeem({
          customer_id: String(store.customer.id),
          order_id: result.order_id,
          points: pendingLoyalty.points,
          order_value: pendingLoyalty.orderValue,
        });
        loyaltyLeg = {
          amount: redeemed.rupee_value,
          reference: `${redeemed.redeemed_points}pts txn ${redeemed.txn_id}`,
        };
        const shortfall =
          Math.round((pendingLoyalty.rupeeValue - redeemed.rupee_value) * 100) / 100;
        if (shortfall > 0.01) {
          loyaltyWarning =
            `Loyalty redemption was capped at ₹${Math.round(redeemed.rupee_value).toLocaleString('en-IN')} ` +
            `(₹${Math.round(pendingLoyalty.rupeeValue).toLocaleString('en-IN')} was applied on screen) — ` +
            `collect the remaining ₹${Math.round(shortfall).toLocaleString('en-IN')} before closing the bill.`;
        }
      } catch {
        loyaltyWarning =
          `Loyalty redeem failed — points were NOT debited, and ` +
          `₹${Math.round(pendingLoyalty.rupeeValue).toLocaleString('en-IN')} is still due on the order. ` +
          `Collect it another way, or re-apply the points from the Orders screen.`;
      }
      store.clearPendingLoyaltyRedeem();
    }

    // The optional cash-accountability capture attaches to the FIRST cash
    // leg only — the customer handed one wad over once; attaching it to a
    // second cash leg would double the note-by-note ledger.
    let cashCapture: CashTenderCapture | null = store.cash_tender;
    const unrecordedPayments: string[] = [];
    for (const p of (store.payments || [])) {
      // The LOYALTY tender IS posted to the order. The points were burned by
      // /loyalty/redeem above — but that call only debits the loyalty account
      // and writes the loyalty ledger; it never touches the ORDER, so an
      // order that skips this leg bills the customer the same rupees again
      // (points burned AND full amount owing). The leg carries the redeem
      // response's amount, never the UI line's: only the server knows what
      // the burn was actually worth after caps. No successful burn -> no leg
      // (warned via loyaltyWarning above, or as unrecorded below).
      let leg = p;
      if (p.method === 'LOYALTY') {
        if (!loyaltyLeg) {
          if (!loyaltyWarning) {
            // A LOYALTY line with no burn behind it (state drift): nothing
            // was debited, so nothing may be recorded — but never silently.
            unrecordedPayments.push(`LOYALTY ₹${Math.round(p.amount).toLocaleString('en-IN')}`);
          }
          continue;
        }
        leg = { ...p, amount: loyaltyLeg.amount, reference: loyaltyLeg.reference };
        loyaltyLeg = null; // one burn backs at most one recorded leg
      }
      try {
        const body = buildPaymentBody(leg, leg.method === 'CASH' ? cashCapture : null);
        if (leg.method === 'CASH') cashCapture = null;
        await orderApi.addPayment(result.order_id, body as any);
      } catch {
        // Still does not block the order - it exists, and a tender can be
        // recorded against it later. But it is no longer SILENT: money taken
        // at the counter that never reached the order is exactly what a
        // day-end blind count cannot explain. (For a LOYALTY leg the points
        // ARE burned and the REDEEM ledger row is linked to this order_id,
        // so staff can record the tender from the Orders screen.)
        unrecordedPayments.push(`${leg.method} ₹${Math.round(leg.amount).toLocaleString('en-IN')}`);
      }
    }
    store.setCashTender(null);
    store.setOrderResult(result.order_id, result.order_number);
    // Money taken at the counter that never reached the order is exactly what
    // a day-end blind count cannot explain. Never return success silently.
    const warningParts: string[] = [];
    if (unrecordedPayments.length) {
      warningParts.push(
        `Order saved, but ${unrecordedPayments.join(' and ')} did NOT record against it. `
          + `Add the payment from the Orders screen before closing the till.`,
      );
    }
    if (loyaltyWarning) warningParts.push(loyaltyWarning);
    const paymentWarning = warningParts.length ? warningParts.join(' ') : undefined;

    // Phase 6.8 — auto-create workshop job + prompt sales to fill
    // fitting details. Only fires for Rx orders that actually ship a
    // lens. Earlier code matched category==='RX_LENSES' which never
    // matched the real catalog (categories are OPTICAL_LENS /
    // SPECTACLE_LENS). We also no longer silently swallow errors.
    const cartItems = store.cart || [];
    const frameItem = cartItems.find((i: any) => ['FRAME', 'SUNGLASS'].includes(canonicalCategory(i.category)));
    const lensItem = cartItems.find(
      (i: any) => canonicalCategory(i.category) === 'OPTICAL_LENS' || !!i.lens_details,
    );
    if (store.sale_type === 'prescription_order' && store.prescription && (frameItem || lensItem)) {
      try {
        const expectedDate = new Date();
        expectedDate.setDate(expectedDate.getDate() + 5);
        const jobResp = await workshopApi.createJob({
          order_id: result.order_id,
          frame_details: frameItem ? {
            product_id: frameItem.product_id,
            name: frameItem.name,
            sku: frameItem.sku,
            brand: frameItem.brand,
          } : {},
          lens_details: lensItem?.lens_details || {
            product_id: lensItem?.product_id,
            name: lensItem?.name,
          },
          prescription_id: store.prescription.id || '',
          fitting_instructions: cartItems
            .filter((i: any) => i.item_note)
            .map((i: any) => `${i.name}: ${i.item_note}`)
            .join('; ') || undefined,
          special_notes: store.cart_note || undefined,
          expected_date: expectedDate.toISOString().split('T')[0],
        });
        // Fitting modal path: the caller opens the modal with this jobId —
        // the POS stays in its current step; the modal advances to
        // 'complete' from its onSave / onBack handlers.
        if (jobResp?.job_id) {
          return {
            ok: true,
            orderId: result.order_id,
            orderNumber: result.order_number,
            warning: paymentWarning,
            fittingJobId: jobResp.job_id,
            fittingCoating: (lensItem?.lens_details?.coatings || []).join(', ') || '',
          };
        }
      } catch (e) {
        // Non-fatal — the order IS created. Surface a warning so staff
        // can manually create the workshop job / call IT if necessary.
        // eslint-disable-next-line no-console
        console.warn('[POS] Workshop job auto-create failed:', e);
        store.setStep('complete');
        return {
          ok: true,
          orderId: result.order_id,
          orderNumber: result.order_number,
          warning: [
            paymentWarning,
            'Order saved, but workshop job auto-create failed — please add it manually from the Workshop page.',
          ]
            .filter(Boolean)
            .join(' '),
        };
      }
    }

    store.setStep('complete');
    return {
      ok: true,
      orderId: result.order_id,
      orderNumber: result.order_number,
      warning: paymentWarning,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, error: 'Failed to create order: ' + (msg || 'Network error') };
  }
}
