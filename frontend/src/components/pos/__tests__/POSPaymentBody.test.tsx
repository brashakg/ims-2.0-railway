// ============================================================================
// POS SAFETY DIFFERENTIAL — buildPaymentBody vs the inline code it replaced
// ============================================================================
// POS is revenue-critical. The 2026-08-25 owner ruling adds an OPTIONAL
// cash-accountability record to the CASH payment leg; everything money-bearing
// must be BYTE-IDENTICAL to what POSLayout inlined before. `legacyBody` below
// is that inline code, copied VERBATIM from the pre-change POSLayout.tsx
// (origin/main ea4ba40, lines 678-696). The differential runs every tender
// shape through both and compares the serialized bodies:
//
//   1. capture ABSENT  -> identical for EVERY method, CASH included.
//   2. capture PRESENT -> non-CASH methods identical (capture ignored);
//      CASH gains ONLY the additive keys, with the original keys untouched.
//
// If someone edits buildPaymentBody so any amount, reference or EMI field
// drifts from what POS always sent, case 1 dies on the exact method + diff.

import { describe, it, expect } from 'vitest';
import { buildPaymentBody } from '../paymentBody';
import type { PaymentEntry, CashTenderCapture } from '../../../stores/posStore';

// --- VERBATIM legacy construction (pre-change POSLayout.tsx) ----------------
function legacyBody(p: PaymentEntry): Record<string, unknown> {
  const body: Record<string, unknown> = {
    method: p.method,
    amount: p.amount,
    reference: p.reference,
    voucher_code: p.voucherCode,
  };
  if (p.method === 'EMI') {
    body.emi_months = p.emiTenure;
    body.emi_provider = p.emiProvider;
    if (p.emiBalance && p.emiBalance > 0) {
      body.emi_principal = p.emiBalance;
    }
  }
  return body;
}
// ----------------------------------------------------------------------------

const ts = '2026-08-30T10:00:00Z';

// Every tender shape POS can produce, with DISTINGUISHABLE values (no two
// fields share a number, so a field swapped for another cannot pass).
const MATRIX: PaymentEntry[] = [
  { method: 'CASH', amount: 851.25, timestamp: ts },
  { method: 'CASH', amount: 902.5, reference: 'drawer-2', timestamp: ts },
  { method: 'UPI', amount: 1203.75, reference: 'upi-ref-77', timestamp: ts },
  { method: 'CARD', amount: 1404.5, reference: '4321', timestamp: ts },
  { method: 'BANK_TRANSFER', amount: 15005, reference: 'NEFT-9', timestamp: ts },
  { method: 'CREDIT', amount: 1606.25, timestamp: ts },
  { method: 'VOUCHER', amount: 407.75, voucherCode: 'VC-2024-A', timestamp: ts },
  { method: 'GIFT_VOUCHER', amount: 308.5, voucherCode: 'GV-11', reference: 'GV-11', timestamp: ts },
  { method: 'LOYALTY', amount: 209.25, timestamp: ts },
  // EMI with a financed balance -> emi_principal present
  { method: 'EMI', amount: 5010, emiTenure: 9, emiProvider: 'HDFC', downPayment: 5010, emiBalance: 12040, monthlyEMI: 1403, processingFee: 240.8, timestamp: ts },
  // EMI with zero balance -> emi_principal ABSENT (the `&& > 0` guard)
  { method: 'EMI', amount: 6020, emiTenure: 12, emiProvider: 'ICICI', emiBalance: 0, timestamp: ts },
  // EMI with balance undefined -> emi_principal ABSENT
  { method: 'EMI', amount: 7030, emiTenure: 6, emiProvider: 'Axis', timestamp: ts },
];

const CAPTURE: CashTenderCapture = {
  tendered_amount: 1000,
  rows: [
    { face: 500, kind: 'note', pieces: 2 },
    { face: 100, kind: 'note', pieces: 0 }, // untouched row: must be dropped
  ],
};

describe('POS payment body differential (owner ruling 2026-08-25)', () => {
  it('with no capture, every method produces the EXACT legacy body', () => {
    for (const p of MATRIX) {
      expect(JSON.stringify(buildPaymentBody(p, null)), `method ${p.method} amount ${p.amount}`)
        .toBe(JSON.stringify(legacyBody(p)));
      // undefined capture arg (the non-CASH call shape in POSLayout) too
      expect(JSON.stringify(buildPaymentBody(p))).toBe(JSON.stringify(legacyBody(p)));
    }
  });

  it('a capture never changes a NON-CASH body by one byte', () => {
    for (const p of MATRIX.filter((m) => m.method !== 'CASH')) {
      expect(JSON.stringify(buildPaymentBody(p, CAPTURE))).toBe(JSON.stringify(legacyBody(p)));
    }
  });

  it('on a CASH leg the capture is PURELY ADDITIVE: legacy keys byte-identical', () => {
    const p = MATRIX[0]; // CASH 851.25
    const body = buildPaymentBody(p, CAPTURE);
    const legacy = legacyBody(p);
    for (const k of Object.keys(legacy)) {
      expect(JSON.stringify(body[k]), `legacy key ${k}`).toBe(JSON.stringify(legacy[k]));
    }
    // The additions the backend reads (orders.py OrderPaymentCreate).
    expect(body.tendered_amount).toBe(1000);
    // change anchored to THIS LEG: 1000 - 851.25
    expect(body.change_amount).toBe(148.75);
    expect(body.cash_tendered).toEqual({
      rows: [{ face: 500, kind: 'note', pieces: 2 }],
      state: 'COUNTED',
    });
    // And nothing else appeared.
    expect(Object.keys(body).sort()).toEqual(
      [...Object.keys(legacy), 'tendered_amount', 'change_amount', 'cash_tendered'].sort(),
    );
  });

  it('tendered below the leg amount omits change_amount (unknown is not zero)', () => {
    const p: PaymentEntry = { method: 'CASH', amount: 851.25, timestamp: ts };
    const body = buildPaymentBody(p, { tendered_amount: 800 });
    expect(body.tendered_amount).toBe(800);
    expect('change_amount' in body).toBe(false);
    expect('cash_tendered' in body).toBe(false); // no rows given
  });

  it('a zero-tendered capture with only a note count sends the count alone', () => {
    const p: PaymentEntry = { method: 'CASH', amount: 500, timestamp: ts };
    const body = buildPaymentBody(p, {
      tendered_amount: 0,
      rows: [{ face: 500, kind: 'note', pieces: 1 }],
    });
    expect('tendered_amount' in body).toBe(false);
    expect('change_amount' in body).toBe(false);
    expect(body.cash_tendered).toEqual({
      rows: [{ face: 500, kind: 'note', pieces: 1 }],
      state: 'COUNTED',
    });
  });
});
