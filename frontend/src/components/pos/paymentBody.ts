// ============================================================================
// Payment body builder — the ONE place a PaymentEntry becomes an HTTP body.
// ============================================================================
// POS SAFETY IS ABSOLUTE. `method`, `amount`, `reference`, `voucher_code` and
// the EMI fields are built EXACTLY as POSLayout's inline code always built
// them — byte-identical for every tender (POSPaymentBody.test.tsx is the
// differential over all payment flows). The ONLY addition is the optional
// cash-accountability record on a CASH leg (owner ruling 2026-08-25):
// `tendered_amount` / `change_amount` / `cash_tendered` ride ALONGSIDE
// `amount` as an attached record — the backend (orders.py cash_leg_record)
// recomputes everything money-bearing from `amount` + `method` alone, so
// nothing here can move a rupee. No capture -> the body is unchanged.

import type { PaymentEntry, CashTenderCapture } from '../../stores/posStore';

export function buildPaymentBody(
  p: PaymentEntry,
  cashTender?: CashTenderCapture | null,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    method: p.method,
    amount: p.amount,
    reference: p.reference,
    voucher_code: p.voucherCode,
  };
  // EMI requires emi_months on the backend (else 400). Forward the
  // tenure/provider the POS already captured — without this every EMI
  // payment silently failed and the order stayed unpaid.
  // POS-2: also pass emi_principal (financed balance) so the backend
  // builds the schedule on the loan amount, not the down-payment.
  if (p.method === 'EMI') {
    body.emi_months = p.emiTenure;
    body.emi_provider = p.emiProvider;
    if (p.emiBalance && p.emiBalance > 0) {
      body.emi_principal = p.emiBalance;
    }
  }
  if (p.method === 'CASH' && cashTender) {
    const tendered = Math.round((cashTender.tendered_amount || 0) * 100) / 100;
    if (tendered > 0) {
      body.tendered_amount = tendered;
      // Change is anchored to THIS LEG (tendered − leg amount), the identity
      // the backend checks. Short/negative -> omitted: blank is not zero, and
      // an unknown change is not an imbalance (cash_leg_identity -> None).
      const legChange = Math.round((tendered - p.amount) * 100) / 100;
      if (legChange >= 0) body.change_amount = legChange;
    }
    const rows = (cashTender.rows || []).filter((r) => (r.pieces || 0) > 0);
    if (rows.length > 0) {
      body.cash_tendered = { rows, state: 'COUNTED' };
    }
  }
  return body;
}
