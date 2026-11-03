// ============================================================================
// The three things the new billing surface could not do, which kept the old
// /pos alive
// ============================================================================
// The legacy-till retirement inventory found the blockers all here:
//
//   G1  an Rx sale created NO workshop job. submitOrder builds the job only
//       for sale_type === 'prescription_order', and only the classic till ever
//       set that. This is how the shop makes most of its money, and the lab
//       never heard about it.
//   G2  a DEPOSIT sale was impossible: submitOrder refuses a partly-paid bill
//       unless is_advance_payment, which only the classic review panel set --
//       so the delivery counter had nothing to collect on.
//   G3  a wrong customer pick could only be undone by reloading the page.
//
// sale_type is DERIVED, never asked: Rx on the bill + something the lab makes
// or fits (frame/sunglass or a lens line -- the job creator's own predicate,
// workshopItemsOf) => prescription order; anything else => quick sale. Each
// test below reads the state the surface actually handed to the submit brain.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['CASHIER'] },
    hasRole: () => true,
  }),
}));

// Keep the REAL workshopItemsOf (the predicate under test) and stub only the
// submit itself.
const submitPosOrder = vi.fn();
vi.mock('../../../../components/pos/submitOrder', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../components/pos/submitOrder')>()),
  submitPosOrder: (...a: unknown[]) => submitPosOrder(...a),
}));

// F46: the cart's sellable counts. Returns nothing unless a test hands it a map.
const useSellableStock = vi.fn();
vi.mock('../../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
  useSellableStock: (...a: unknown[]) => useSellableStock(...a),
}));
vi.mock('../../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

// Leaf components: the smallest thing that still lets the surface render.
vi.mock('../../../../components/pos/WalkoutComplianceBanner', () => ({ default: () => null }));
vi.mock('../../../../components/pos/WalkinWalkoutControls', () => ({ WalkinWalkoutControls: () => null }));
vi.mock('../../../../components/pos/HeldBillsControls', () => ({ HeldBillsControls: () => null }));
vi.mock('../../../../components/pos/LensDetailsModal', () => ({
  addManualLensToCart: vi.fn(),
  LensDetailsModal: () => null,
}));
vi.mock('../../../../components/pos/NewPrescriptionAtTill', () => ({
  NewPrescriptionAtTill: () => null,
}));
// The card echoes the ONE prop this file cares about: the till's clear-pick door.
vi.mock('../../../../components/pos/CustomerCardWithLoyalty', () => ({
  CustomerCardWithLoyalty: ({ onChange }: { onChange?: () => void }) => (
    <button type="button" onClick={onChange}>change-customer</button>
  ),
}));
// The cart echoes the stock counts it was handed (F46).
vi.mock('../../../../components/pos/POSCart', () => ({
  CartSidebar: ({ stock }: { stock?: { sellable: Record<string, number | null> } }) => (
    <div>cart{stock ? `:${JSON.stringify(stock.sellable)}` : ''}</div>
  ),
}));
vi.mock('../../../../components/pos/DiscountModal', () => ({
  DiscountModal: () => null,
  toDiscountItem: (x: unknown) => x,
}));
vi.mock('../../../../components/pos/BillDiscountCard', () => ({ BillDiscountCard: () => null }));
vi.mock('../../../../components/pos/POSPayment', () => ({ StepPayment: () => <div>payment</div> }));
vi.mock('../../../../components/pos/POSInvoice', () => ({
  StepComplete: (p: { onReset?: () => void }) => <button type="button" onClick={p.onReset}>New Sale</button>,
}));
vi.mock('../../../../components/pos/BarcodeScanner', () => ({ BarcodeScanner: () => null }));
vi.mock('../../../../components/pos/PrescriptionSelectModal', () => ({
  PrescriptionSelectModal: () => null,
}));
vi.mock('../../../../components/customers/AddCustomerModal', () => ({ AddCustomerModal: () => null }));
vi.mock('../../../../components/pos/SalespersonPicker', () => ({ SalespersonPicker: () => null }));
vi.mock('../../../../components/pos/CustomerSearchBar', () => ({
  CustomerSearchBar: () => null,
  createAndSelectCustomer: vi.fn(),
  selectCustomerHit: vi.fn(),
}));
vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../SaleCompleteScreen', () => ({
  default: (p: { orderId: string; onDone?: () => void }) => (
    <div>sale-complete:{p.orderId}<button type="button" onClick={p.onDone}>Done</button></div>
  ),
}));
// The strip reports the store it was handed (F46: its tiles' counts are keyed by it).
const stripStores = vi.fn();
vi.mock('../ProductResultsStrip', () => ({
  default: ({ storeId }: { storeId: string }) => {
    stripStores(storeId);
    return null;
  },
}));
vi.mock('../DeliveryOptionsRow', () => ({ default: () => null }));

import { BillingSurface } from '../BillingSurface';
import { usePOSStore } from '../../../../stores/posStore';
import { billKeyEntry, billKeyFor } from '../../../../components/pos/posBillKey';

type Line = 'FRAME' | 'OPTICAL_LENS' | 'ACCESSORIES' | 'CONTACT_LENS';

/** A bill on the till: customer, optional Rx, and the given lines. */
function seed(opts: { rx?: boolean; lines: Line[] }) {
  const s = usePOSStore.getState();
  s.setCustomer({ id: 'c-1', name: 'Asha Verma', phone: '9876543210' } as never);
  if (opts.rx) s.setPrescription({ id: 'rx-1', rightEye: {}, leftEye: {} } as never);
  opts.lines.forEach((category, i) =>
    s.addToCart({
      id: `p-${i}`,
      product_id: `p-${i}`,
      name: category,
      category,
      quantity: 1,
      unit_price: 1000,
      price: 1000,
      mrp: 1000,
      tax_rate: 5,
    } as never),
  );
}

const completeSale = async () => {
  fireEvent.click(screen.getByRole('button', { name: /complete sale|saving/i }));
  await waitFor(() => expect(submitPosOrder).toHaveBeenCalledTimes(1));
  return submitPosOrder.mock.calls[0][0] as { sale_type: string; is_advance_payment: boolean };
};

beforeEach(() => {
  usePOSStore.getState().resetTransaction('discard');
  submitPosOrder.mockReset().mockResolvedValue({ ok: true, orderId: 'o-1' });
  useSellableStock.mockReset().mockReturnValue({ data: undefined });
  stripStores.mockReset();
});

describe("F46: the cart hears what this shop can sell", () => {
  it("asks for the cart lines' counts at the till's store and hands them to the cart", () => {
    seed({ lines: ['FRAME'] });
    useSellableStock.mockReturnValue({ data: { store_id: 'BV-BOK-01', sellable: { 'p-0': 0 } } });
    render(<BillingSurface />);
    expect(useSellableStock).toHaveBeenCalledWith(
      'BV-BOK-01',
      [expect.objectContaining({ product_id: 'p-0' })],
    );
    expect(screen.getByText('cart:{"p-0":0}')).toBeTruthy();
  });

  it("keys the cart and the strip by the signed-in shop, never a till draft's leftover store", () => {
    // posStore.store_id survives in the persisted 'ims-pos-draft' (the retired
    // till set it). Complete sale checks the signed-in shop, so the stock
    // figures must be that shop's.
    usePOSStore.getState().setStoreId('BV-OTHER-02');
    try {
      seed({ lines: ['FRAME'] });
      render(<BillingSurface />);
      expect(useSellableStock).toHaveBeenCalled();
      for (const [storeId] of useSellableStock.mock.calls) expect(storeId).toBe('BV-BOK-01');
      expect(stripStores).toHaveBeenCalled();
      for (const [storeId] of stripStores.mock.calls) expect(storeId).toBe('BV-BOK-01');
    } finally {
      usePOSStore.getState().setStoreId('');
    }
  });
});

describe('G1: the sale type the lab depends on is derived from the bill', () => {
  it('Rx + frame => prescription order (a workshop job will be created)', async () => {
    seed({ rx: true, lines: ['FRAME'] });
    render(<BillingSurface />);
    expect((await completeSale()).sale_type).toBe('prescription_order');
  });

  it('Rx + lens line => prescription order', async () => {
    seed({ rx: true, lines: ['OPTICAL_LENS'] });
    render(<BillingSurface />);
    expect((await completeSale()).sale_type).toBe('prescription_order');
  });

  it('frame with no Rx => quick sale', async () => {
    seed({ lines: ['FRAME'] });
    render(<BillingSurface />);
    expect((await completeSale()).sale_type).toBe('quick_sale');
  });

  it('Rx with only accessories or contact lenses => quick sale, never a dead end', async () => {
    // A prescription order without a lens/frame is REFUSED by the submit
    // brain ("requires at least one lens item") and this surface has no
    // switch to flip back -- so it must never be typed that way.
    seed({ rx: true, lines: ['ACCESSORIES', 'CONTACT_LENS'] });
    render(<BillingSurface />);
    expect((await completeSale()).sale_type).toBe('quick_sale');
  });
});

describe('G2: a deposit sale', () => {
  it('flags the order advance-only so a part payment is accepted and the balance waits for delivery', async () => {
    seed({ lines: ['FRAME'] });
    render(<BillingSurface />);
    const toggle = screen.getByRole('button', { name: /advance only/i });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect((await completeSale()).is_advance_payment).toBe(true);
  });

  it('is off by default -- a full-payment bill is unchanged', async () => {
    seed({ lines: ['FRAME'] });
    render(<BillingSurface />);
    expect((await completeSale()).is_advance_payment).toBe(false);
  });
});

describe('G3: a wrong customer pick can be cleared', () => {
  it('Change on the card clears the customer (and with it the member and Rx)', () => {
    seed({ rx: true, lines: [] });
    render(<BillingSurface />);
    fireEvent.click(screen.getByText('change-customer'));
    expect(usePOSStore.getState().customer).toBeNull();
    expect(usePOSStore.getState().prescription).toBeNull();
  });
});

describe('money reviews 26-28: the optical till uses THE settle rule', () => {
  const fp = () => usePOSStore.getState().ensureBillId();
  // The runner's localStorage keeps nothing; the bill keys need a real store.
  beforeEach(() => {
    const mem = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => void mem.set(k, String(v)),
      removeItem: (k: string) => void mem.delete(k),
      clear: () => mem.clear(),
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('keeps the key pending when a pending bill is refused before the order door', async () => {
    seed({ lines: ['FRAME'] });
    billKeyFor(fp()); // an earlier press lost its answer
    submitPosOrder.mockReset().mockResolvedValueOnce({ ok: false, error: 'Could not get the bill total from the server: x' });
    render(<BillingSurface />);
    await completeSale();
    expect(billKeyEntry(fp())?.pending).toBe(true);
  });

  it('keeps a pending key on a refusal at the order door too; after the sale, Done clears it', async () => {
    seed({ lines: ['FRAME'] });
    billKeyFor(fp());
    submitPosOrder.mockReset()
      .mockResolvedValueOnce({ ok: false, error: 'Failed to create order: x', sent: true, definite: true })
      .mockResolvedValueOnce({ ok: true, orderId: 'o-1' });
    render(<BillingSurface />);
    await completeSale();
    expect(billKeyEntry(fp())?.pending).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /complete sale|saving/i }));
    await waitFor(() => expect(submitPosOrder).toHaveBeenCalledTimes(2));
    const sold = usePOSStore.getState().bill_id || '';
    expect(billKeyEntry(sold)?.pending).toBe(true); // money review 31: kept until Done
    fireEvent.click(await screen.findByRole('button', { name: 'Done' }));
    expect(billKeyEntry(sold)).toBeNull();
  });
});

describe('money review 31: Done on the Rx completion screen drops only the bill that sold', () => {
  beforeEach(() => {
    const mem = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => void mem.set(k, String(v)),
      removeItem: (k: string) => void mem.delete(k),
      clear: () => mem.clear(),
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('a pending bill recalled while the completion screen shows stays reachable after Done', async () => {
    seed({ rx: true, lines: ['FRAME'] });
    submitPosOrder.mockReset().mockResolvedValueOnce({ ok: true, orderId: 'o-1', fittingJobId: 'J-1' });
    render(<BillingSurface />);
    await completeSale();
    await screen.findByRole('button', { name: 'Done' });
    usePOSStore.setState({ bill_id: 'recalled-pending' } as any); // a pending bill recalled meanwhile
    billKeyFor('recalled-pending');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(usePOSStore.getState().bill_id).toBe('recalled-pending');
    expect(billKeyEntry('recalled-pending')?.pending).toBe(true);
  });
});

describe('money review 31: New Sale after the optical till is left and opened again', () => {
  beforeEach(() => {
    const mem = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => void mem.set(k, String(v)),
      removeItem: (k: string) => void mem.delete(k),
      clear: () => mem.clear(),
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('resets the sold bill and clears its key (the ref is gone after a remount)', async () => {
    seed({ lines: ['FRAME'] });
    submitPosOrder.mockReset().mockImplementationOnce(async () => {
      usePOSStore.getState().setStep('complete');
      return { ok: true, orderId: 'o-6' };
    });
    const view = render(<BillingSurface />);
    await completeSale();
    const sold = usePOSStore.getState().bill_id || '';
    expect(billKeyEntry(sold)?.pending).toBe(true);
    view.unmount();
    render(<BillingSurface />);
    fireEvent.click(screen.getByRole('button', { name: 'New Sale' }));
    expect(usePOSStore.getState().cart).toEqual([]);
    expect(billKeyEntry(sold)).toBeNull();
  });
});
