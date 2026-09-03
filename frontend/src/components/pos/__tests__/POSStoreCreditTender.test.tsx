// ============================================================================
// POS store-credit TENDER — the spend side of refund credit notes
// ============================================================================
// The defect: every refund ISSUES store credit and the till DISPLAYS the
// balance, but no tender could SPEND it (the button labelled "Store credit"
// even opened the khata card). These tests pin the new tender's money rules:
//
//   * the leg is capped at min(available credit, bill balance) — never more
//     than the credit, never more than what the bill still owes;
//   * typing more than the available balance is refused, no leg is added;
//   * walk-ins (no credit account) never see the control;
//   * a TARGET surface (the delivery counter settling an ORDER) only gets the
//     control when it hands in the ORDER's customerId — DeliverySurface does
//     not today, so a cart-bound balance can never paper over a shortfall
//     that should require the manager's credit-delivery approval.
//
// Assertions read the PAYMENT LEGS (posStore / the target's addPayment), not
// button states — a leg with the wrong amount is the bug, not a grey button.

import { render, screen, act, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Complete Map-backed localStorage for the posStore persist middleware.
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

// GST runtime resolver -> static, no /health fetch (posStore totals use it).
vi.mock('../../../constants/gstRuntime', () => ({
  resolveGstRate: () => 5,
  isInclusivePricing: () => true,
  loadHsnRates: vi.fn(),
  loadPricingMode: vi.fn(),
}));

// Barrel mock: StepPayment reads the store detail for the EMI rate.
vi.mock('../../../services/api', () => ({
  storeApi: { getStore: vi.fn().mockResolvedValue({}) },
}));

// The scoped balance read the control performs (GET .../store-credit/ledger).
const ledgerMock = vi.fn();
vi.mock('../../../services/api/customers', () => ({
  customerApi: { getStoreCreditLedger: (...a: unknown[]) => ledgerMock(...a) },
  customersApi: { getCreditSummary: vi.fn().mockResolvedValue(null) },
}));

// Sibling controls owned by other agents — irrelevant to these assertions.
vi.mock('../LoyaltyRedeemControl', () => ({ LoyaltyRedeemControl: () => null }));

import { StepPayment, type PaymentTarget } from '../POSPayment';
import { usePOSStore } from '../../../stores/posStore';

function seedCart(total: number, customerId = 'cust-1') {
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.addToCart({
      product_id: 'p1', name: 'Frame A', sku: 'FR-1', category: 'FRAMES',
      unit_price: total, mrp: total, quantity: 1, is_optical: false,
    } as any);
    usePOSStore.setState({ customer: { id: customerId, name: 'Asha' } as any });
  });
}

async function openStoreCredit() {
  fireEvent.click(screen.getByRole('button', { name: 'Store credit' }));
  // Balance fetched (scoped, server-side) before anything is spendable.
  await waitFor(() => expect(ledgerMock).toHaveBeenCalled());
}

const storeCreditLegs = () =>
  (usePOSStore.getState().payments || []).filter((p: any) => p.method === 'STORE_CREDIT');

beforeEach(() => {
  localStorage.clear();
  ledgerMock.mockReset();
});
afterEach(cleanup);

describe('POS store-credit tender (cart mode)', () => {
  it('KEY: applies a STORE_CREDIT leg capped at the BILL when credit exceeds it', async () => {
    ledgerMock.mockResolvedValue({ customer_id: 'cust-1', balance: 5000, entries: [] });
    seedCart(1200);
    render(<StepPayment />);
    await openStoreCredit();
    await screen.findByText('₹5,000');

    // Empty amount -> the prefidged cap: min(5000 credit, 1200 due) = 1200.
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    const legs = storeCreditLegs();
    expect(legs).toHaveLength(1);
    expect(legs[0].amount).toBe(1200);
    expect(legs[0].method).toBe('STORE_CREDIT');
  });

  it('KEY: applies a STORE_CREDIT leg capped at the CREDIT when the bill exceeds it', async () => {
    ledgerMock.mockResolvedValue({ customer_id: 'cust-1', balance: 300, entries: [] });
    seedCart(1200);
    render(<StepPayment />);
    await openStoreCredit();
    await screen.findByText('₹300');

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    const legs = storeCreditLegs();
    expect(legs).toHaveLength(1);
    expect(legs[0].amount).toBe(300); // never more than the balance
  });

  it('KEY: typing MORE than the available credit is refused — no leg added', async () => {
    ledgerMock.mockResolvedValue({ customer_id: 'cust-1', balance: 300, entries: [] });
    seedCart(1200);
    render(<StepPayment />);
    await openStoreCredit();
    await screen.findByText('₹300');

    // The CONTROL's own input (placeholder carries its cap: min(300, 1200));
    // the generic tender row's input says "Amount (max ₹1,200)".
    fireEvent.change(screen.getByPlaceholderText('Amount (max ₹300)'), { target: { value: '900' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(await screen.findByText(/Only ₹300 of store credit is available/)).toBeTruthy();
    expect(storeCreditLegs()).toHaveLength(0);
  });

  it('an unreadable balance is fail-CLOSED: shows no credit, Apply disabled', async () => {
    ledgerMock.mockRejectedValue(new Error('network'));
    seedCart(1200);
    render(<StepPayment />);
    await openStoreCredit();
    expect(await screen.findByText('This customer has no store credit.')).toBeTruthy();
    expect(storeCreditLegs()).toHaveLength(0);
  });

  it('walk-in customers never see the store-credit control', () => {
    ledgerMock.mockResolvedValue({ balance: 9999, entries: [] });
    seedCart(1200, 'walkin-77');
    render(<StepPayment />);
    expect(screen.queryByRole('button', { name: 'Store credit' })).toBeNull();
    expect(ledgerMock).not.toHaveBeenCalled();
  });
});

describe('POS store-credit tender (target mode — an ORDER being settled)', () => {
  const mkTarget = (over: Partial<PaymentTarget> = {}): PaymentTarget => ({
    due: 800,
    payments: [],
    addPayment: vi.fn(),
    removePayment: vi.fn(),
    storeId: 'BV-BOK-01',
    ...over,
  });

  it('KEY: WITHOUT customerId (the delivery counter today) store credit is hidden — it cannot paper over a manager-gated shortfall', () => {
    ledgerMock.mockResolvedValue({ balance: 9999, entries: [] });
    render(<StepPayment target={mkTarget()} />);
    expect(screen.queryByText('Store credit')).toBeNull();
    expect(ledgerMock).not.toHaveBeenCalled();
  });

  it("WITH the ORDER's customerId the control renders and adds the leg via the target, capped at the order's due", async () => {
    ledgerMock.mockResolvedValue({ customer_id: 'cust-9', balance: 5000, entries: [] });
    const target = mkTarget({ customerId: 'cust-9' });
    render(<StepPayment target={target} />);
    await screen.findByText('₹5,000');
    expect(ledgerMock).toHaveBeenCalledWith('cust-9');

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(target.addPayment).toHaveBeenCalledTimes(1);
    expect(vi.mocked(target.addPayment).mock.calls[0][0]).toMatchObject({
      method: 'STORE_CREDIT',
      amount: 800, // min(5000 credit, 800 due) — the ORDER's balance, not a cart's
    });
  });
});
