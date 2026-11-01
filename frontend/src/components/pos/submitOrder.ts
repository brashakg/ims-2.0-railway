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
import { refreshBillQuote, replayGuaranteed } from './billQuote';
import { orderPayloadOf } from './orderPayload';
import { TILL_SALE_UNCONFIRMED, TILL_SALE_UNCONFIRMED_CHECK } from './posBillKey';
import type { DiscountApprovalNeed } from './DiscountApprovalPanel';
import { getErrorDetail, getErrorMessage, getErrorStatus } from '../../utils/errorHandler';
import { WRITE_UNCONFIRMED_MESSAGE } from '../../services/api/client';

// The order body, its lines and THE bill fingerprint live in orderPayload.
export { mapCategory, orderPayloadOf, billFingerprint } from './orderPayload';

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
  /** Fatal, and actionable: the discount is over the seller's own limit. The
      caller asks a manager (DiscountApprovalPanel) and resubmits with the
      approved request's id. */
  discountApproval?: DiscountApprovalNeed;
  /** Fatal: the approval sent with this try cannot be used (already spent,
      expired, or for another bill) -- the caller drops it. */
  discountApprovalRejected?: boolean;
}

/** The cart lines a workshop job is built from. ONE predicate: the job
 *  creator below and BillingSurface's sale_type derivation both read it, so
 *  "does this sale need the lab" can never be answered two ways. */
export function workshopItemsOf(cart: any[]): { frameItem?: any; lensItem?: any } {
  const items = cart || [];
  return {
    frameItem: items.find((i: any) =>
      ['FRAME', 'SUNGLASS'].includes(canonicalCategory(i.category)),
    ),
    lensItem: items.find(
      (i: any) => canonicalCategory(i.category) === 'OPTICAL_LENS' || !!i.lens_details,
    ),
  };
}

/** "Ask a manager to approve": re-send THIS bill with request_approval. The
    SERVER prices it and raises the discount_override request from its own
    figures (the cashier adds only a note). Never creates an order. */
export async function askManagerForDiscount(
  store: any,
  note?: string,
): Promise<{ requestId: string; status?: string }> {
  const data = await orderApi.createOrder({
    ...orderPayloadOf(store),
    request_approval: true,
    approval_note: note?.trim() || undefined,
  } as any);
  return { requestId: data.request_id, status: data.status };
}

/** Refusal codes that mean "the approval sent with this try is spent, gone or
    for another bill" -- the till drops it and asks again. */
const DEAD_APPROVAL_CODES = new Set([
  'DISCOUNT_APPROVAL_INVALID',
  'DISCOUNT_APPROVAL_USED',
  'DISCOUNT_APPROVAL_EXPIRED',
  'DISCOUNT_APPROVAL_NOT_FOUND',
]);

/** The server's refusal, in words staff can act on ('' when there is none). */
// Tenders that only RECORD money taken at the counter: re-sending one with its
// key is safe. Store credit, vouchers and loyalty debit an account.
const RECORD_ONLY_TENDERS = new Set(['CASH', 'CARD', 'UPI']);

export { TILL_SALE_UNCONFIRMED, TILL_SALE_UNCONFIRMED_CHECK } from './posBillKey';

function serverReason(e: unknown): string {
  const d = getErrorDetail(e) as { reason?: unknown } | undefined;
  if (d && typeof d === 'object' && d.reason === 'insufficient_balance') return 'Not enough loyalty points';
  return getErrorMessage(e, '');
}

/** `store` is the bound posStore instance (usePOSStore()). Validations run
    here too so both surfaces refuse identically. */
export async function submitPosOrder(
  store: any,
  idempotencyKey: string,
  /** The request id of a manager-approved discount_override approval, when a
      refused sale is retried after one (owner 2026-10-18). */
  discountApprovalId?: string | null,
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

  // THE bill total is the SERVER's (POST /orders/quote, priced by create's own
  // math and rounded to the rupee there): take a fresh quote now, so the
  // tenders below are checked against the rupee this order will bill. The
  // till never rounds a bill itself, and never collects against an estimate.
  try {
    await refreshBillQuote();
  } catch (err) {
    // The server's own reason (a role that may not bill, a line it refuses),
    // or the API client's network text -- never a guess at the connection.
    const msg = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      error: 'Could not get the bill total from the server: ' + (msg || 'Network error'),
    };
  }
  if (!store.getQuotedBill()) {
    return { ok: false, error: 'The bill changed while it was being priced. Check the total and try again.' };
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
  // The total can also drop by paise: a payment added from the till's estimate
  // before the server's rounded total arrived. So the figures keep their paise
  // (Math.round showed "₹0 more than the bill").
  const overTendered = -store.getBalance();
  if (overTendered > 0.01) {
    const rs = (v: number) =>
      '₹' +
      v.toLocaleString('en-IN', {
        minimumFractionDigits: Number.isInteger(v) ? 0 : 2,
        maximumFractionDigits: 2,
      });
    return {
      ok: false,
      error:
        `Tendered ${rs(store.getTotalPaid())} is ${rs(overTendered)} more than the ` +
        `bill total of ${rs(store.getGrandTotal())}. The total changed after the ` +
        `payment was added. Remove the payment and add it again.`,
    };
  }

  try {
    const result = await orderApi.createOrder(
      orderPayloadOf(store, discountApprovalId) as any,
      idempotencyKey || undefined,
    );

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
      } catch (e) {
        const why = serverReason(e);
        const status = getErrorStatus(e);
        const refused = status !== undefined && status >= 400 && status < 500;
        loyaltyWarning = refused
          ? `Loyalty redeem failed${why ? ` (${why})` : ''} — this attempt took NO points, and ` +
            `₹${Math.round(pendingLoyalty.rupeeValue).toLocaleString('en-IN')} is still due on the order. ` +
            `Collect it another way (if an earlier attempt could not be confirmed, check the customer's points first).`
          // No refusal came back: the burn may have landed. Never claim it did not.
          : `The loyalty redeem could not be confirmed — check the customer's points before collecting ` +
            `₹${Math.round(pendingLoyalty.rupeeValue).toLocaleString('en-IN')} another way. If the points ` +
            `were taken, ask an admin to record the loyalty payment on this order.`;
      }
      store.clearPendingLoyaltyRedeem();
    }

    // The optional cash-accountability capture attaches to the FIRST cash
    // leg only — the customer handed one wad over once; attaching it to a
    // second cash leg would double the note-by-note ledger.
    let cashCapture: CashTenderCapture | null = store.cash_tender;
    const unrecordedPayments: string[] = [];
    const unconfirmedPayments: string[] = [];
    for (const [legIndex, p] of (store.payments || []).entries()) {
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
      // Each leg has its own key (the server dedupes a replay of it). Only a
      // record-only tender (cash, card, UPI) whose answer was lost is sent
      // ONCE more automatically; a leg that DEBITS something (store credit, a
      // gift voucher, loyalty points) never is -- the till says to check.
      const legKey = idempotencyKey ? `${idempotencyKey}:leg:${legIndex}` : undefined;
      const autoRetry = Boolean(legKey) && RECORD_ONLY_TENDERS.has(String(leg.method || '').toUpperCase());
      const body = buildPaymentBody(leg, leg.method === 'CASH' ? cashCapture : null);
      if (leg.method === 'CASH') cashCapture = null;
      const label = `${leg.method} ₹${Math.round(leg.amount).toLocaleString('en-IN')}`;
      let failure: unknown = null;
      for (let attempt = 0; attempt < (autoRetry ? 2 : 1); attempt += 1) {
        try {
          await orderApi.addPayment(result.order_id, body as any, legKey);
          failure = null;
          break;
        } catch (e) {
          failure = e;
          const st = getErrorStatus(e);
          if (st !== undefined && st < 500) break; // a refusal: never re-sent
        }
      }
      if (failure !== null) {
        const st = getErrorStatus(failure);
        if (st !== undefined && st >= 400 && st < 500) {
          // Refused: it did NOT record. Still does not block the order - a
          // tender can be added later - but never SILENT: money taken at the
          // counter that never reached the order is what a day-end blind
          // count cannot explain. (For a LOYALTY leg the points ARE burned
          // and the REDEEM row is linked to this order_id; the server keeps
          // that burn unclaimed for a later LOYALTY tender.)
          const why = serverReason(failure);
          unrecordedPayments.push(`${label}${why ? ` (${why})` : ''}`);
        } else {
          // No answer, or a server error: it may have recorded.
          unconfirmedPayments.push(label);
        }
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
          + `Add the payment from the Orders screen before closing the till.`
          // The Orders screen takes cash / card / UPI / bank only: a loyalty
          // leg cannot be added there, so say what IS true.
          + (unrecordedPayments.some((u) => u.startsWith('LOYALTY'))
            ? ` Loyalty points cannot be added there - ask an admin; the redeemed points stay reserved for this bill.`
            : ''),
      );
    }
    if (unconfirmedPayments.length) {
      warningParts.push(
        `Order saved, but ${unconfirmedPayments.join(' and ')} could not be confirmed - check the `
          + `order's payments before adding it again.`,
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
    const { frameItem, lensItem } = workshopItemsOf(cartItems);
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
    const { code, detail } = (err || {}) as { code?: string; detail?: unknown };
    if (code === 'DISCOUNT_NEEDS_APPROVAL') {
      return { ok: false, error: msg, discountApproval: detail as DiscountApprovalNeed };
    }
    if (code && DEAD_APPROVAL_CODES.has(code)) {
      return { ok: false, error: msg, discountApprovalRejected: true };
    }
    if (msg === WRITE_UNCONFIRMED_MESSAGE) {
      return { ok: false, error: replayGuaranteed() ? TILL_SALE_UNCONFIRMED : TILL_SALE_UNCONFIRMED_CHECK };
    }
    return { ok: false, error: 'Failed to create order: ' + (msg || 'Network error') };
  }
}
