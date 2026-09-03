// ============================================================================
// "Complete sale" on the general counter must actually complete the sale
// ============================================================================
// Owner, on the live screen: "complete sale button on general counter not
// doing anything".
//
// This screen had NO test of any kind, which is how a dead checkout shipped.
// The three ways a button can appear to do nothing are all pinned here:
//   1. it throws, and the handler has a `try/finally` with NO `catch`, so the
//      failure is swallowed and nothing is rendered;
//   2. it fails and says so in a banner at the very TOP of a page that scrolls,
//      while the button sits at the BOTTOM -- on a shop iPad the message is
//      off-screen, so the operator sees nothing happen;
//   3. it succeeds but the screen never advances, so the till looks unchanged
//      and the operator presses it again.
//
// The second half pins what the counter carries onto the bill and how it
// browses: the typed note reaches the ONE bill-note field (tagged for a home
// delivery), hold/recall runs the SHARED control against real storage, and the
// grid caps at the shared result limit and says so.

// jsdom's localStorage in this runner has no working setItem; the repo's
// established answer is a complete Map-backed stand-in installed before the
// import graph touches storage (same helper as HeldBillsScoping.test.ts).
// Hold/recall below runs the real posStore park + the real useHeldBills read
// against it.
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

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['CASHIER'] },
  }),
}));

const submitPosOrder = vi.fn();
vi.mock('../../../../components/pos/submitOrder', () => ({
  submitPosOrder: (...a: unknown[]) => submitPosOrder(...a),
}));

// The hand-over doors a take-away sale goes through after the submit (owner
// 2026-09-04). Exercised in generalCounterHandsOver.test.tsx; here they just
// answer so nothing reaches the network.
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    markReady: vi.fn().mockResolvedValue({ status: 'READY' }),
    deliverOrder: vi.fn().mockResolvedValue({ status: 'DELIVERED' }),
  },
}));

// MUTABLE: the browse-grid test hands the counter a product list; every
// checkout test leaves it empty. Reset in beforeEach.
let productRows: unknown[] = [];
vi.mock('../../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: productRows, isLoading: false }),
}));
vi.mock('../../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

// Leaf components. Each is replaced by the smallest thing that still lets the
// surface render -- none of them is what this file is about. HeldBillsControls
// is deliberately NOT on this list: hold/recall is exercised for real below.
vi.mock('../../../../components/pos/WalkoutComplianceBanner', () => ({ default: () => null }));
vi.mock('../../../../components/pos/WalkinWalkoutControls', () => ({ WalkinWalkoutControls: () => null }));
// The card echoes the one prop this file cares about: the till's clear-pick door.
vi.mock('../../../../components/pos/CustomerCardWithLoyalty', () => ({
  CustomerCardWithLoyalty: ({ onChange }: { onChange?: () => void }) => (
    <button type="button" onClick={onChange}>change-customer</button>
  ),
}));
vi.mock('../../../../components/pos/POSCart', () => ({ CartSidebar: () => <div>cart</div> }));
vi.mock('../../../../components/pos/DiscountModal', () => ({
  DiscountModal: () => null,
  toDiscountItem: (x: unknown) => x,
}));
vi.mock('../../../../components/pos/BillDiscountCard', () => ({ BillDiscountCard: () => null }));
vi.mock('../../../../components/pos/POSPayment', () => ({ StepPayment: () => <div>payment</div> }));
vi.mock('../../../../components/pos/POSInvoice', () => ({
  StepComplete: () => <div>legacy-step-complete</div>,
}));
// The GOOD completion screen (server-read totals, PDF, sends), in its COUNTER
// stage. A successful sale must land HERE, not on the legacy StepComplete.
vi.mock('../SaleCompleteScreen', () => ({
  CounterCompleteScreen: (props: { orderId: string }) => (
    <div>counter-complete-screen:{props.orderId}</div>
  ),
}));
vi.mock('../../../../components/pos/BarcodeScanner', () => ({ BarcodeScanner: () => null }));
vi.mock('../../../../components/pos/SalespersonPicker', () => ({ SalespersonPicker: () => null }));
vi.mock('../../../../components/pos/CustomerSearchBar', () => ({
  CustomerSearchBar: () => null,
  createAndSelectCustomer: vi.fn(),
  selectCustomerHit: vi.fn(),
}));
vi.mock('../../../../components/customers/AddCustomerModal', () => ({
  AddCustomerModal: () => null,
}));
vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));

import { MemoryRouter } from 'react-router-dom';
import { GeneralCounterSurface } from '../GeneralCounterSurface';
import { MAX_PRODUCT_RESULTS } from '../ProductResultsStrip';
import { usePOSStore } from '../../../../stores/posStore';

function renderCounter() {
  return render(
    <MemoryRouter>
      <GeneralCounterSurface />
    </MemoryRouter>,
  );
}

/** A sunglass on the counter: the whole point of this till. */
function putSomethingInTheCart() {
  usePOSStore.getState().addToCart({
    id: 'p-1',
    product_id: 'p-1',
    name: 'Ray-Ban Aviator',
    category: 'SUNGLASS',
    quantity: 1,
    unit_price: 5000,
    price: 5000,
    mrp: 5000,
    discount_percent: 0,
    discount_amount: 0,
    tax_rate: 18,
  } as never);
}

const completeButton = () => screen.getByRole('button', { name: /complete sale|saving/i });

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  submitPosOrder.mockReset();
  productRows = [];
  localStorage.setItem('ims-held-bills', '[]');
});

describe('the general counter can actually complete a sale', () => {
  it('is disabled on an empty cart', () => {
    // Deliberate, and not the bug -- but it is the control that gives the next
    // test its meaning: a button that were ALWAYS disabled would also "do
    // nothing", and these two together tell those cases apart.
    renderCounter();
    expect((completeButton() as HTMLButtonElement).disabled).toBe(true);
  });

  it('is pressable once there is something in the cart', () => {
    putSomethingInTheCart();
    renderCounter();
    expect((completeButton() as HTMLButtonElement).disabled).toBe(false);
  });

  it('calls the shared submit brain when pressed', async () => {
    // THE REPORT: "not doing anything". If the handler never reaches the
    // submit, nothing below matters.
    submitPosOrder.mockResolvedValue({ ok: true, orderId: 'o-1', orderNumber: 'ORD-1' });
    putSomethingInTheCart();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(submitPosOrder).toHaveBeenCalledTimes(1));
  });

  it('lands on the completion screen when the sale succeeds', async () => {
    // The shared submit sets step 'complete' itself, and this screen renders
    // off that. If it did not, a successful sale would leave the till looking
    // untouched and the operator would ring it up twice.
    submitPosOrder.mockImplementation(async () => {
      usePOSStore.getState().setStep('complete');
      return { ok: true, orderId: 'o-1', orderNumber: 'ORD-1' };
    });
    putSomethingInTheCart();
    renderCounter();

    fireEvent.click(completeButton());
    // The GOOD completion screen in its COUNTER (tax invoice) stage, fed the
    // server's order id -- NOT the legacy StepComplete (which recomputes
    // totals in the browser and gets no PDF, WhatsApp or scorecard).
    await waitFor(() => expect(screen.getByText('counter-complete-screen:o-1')).toBeTruthy());
    expect(screen.queryByText('legacy-step-complete')).toBeNull();
  });

  it('SHOWS the reason when the sale is refused, instead of failing silently', async () => {
    submitPosOrder.mockResolvedValue({
      ok: false,
      error: 'Payment incomplete. Add payments or enable "Advance payment only".',
    });
    putSomethingInTheCart();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(screen.getByText(/Payment incomplete/)).toBeTruthy());
  });

  it('SHOWS something when the submit throws, rather than swallowing it', async () => {
    // The handler was `try { ... } finally { ... }` with no `catch`. A thrown
    // error (a network stack blowing up, a helper going undefined) therefore
    // escaped, the finally cleared the spinner, and the operator saw a button
    // that did nothing at all. That is indistinguishable from a dead button.
    submitPosOrder.mockRejectedValue(new Error('boom'));
    putSomethingInTheCart();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(submitPosOrder).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText(/could not|failed|error/i)).toBeTruthy(),
    );
  });

  it('leaves the till usable after a refusal, so the sale can be retried', async () => {
    // The spinner must clear. A button stuck on "Saving..." is the other way
    // this screen can look dead.
    submitPosOrder.mockResolvedValue({ ok: false, error: 'Payment incomplete.' });
    putSomethingInTheCart();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(screen.getByText(/Payment incomplete/)).toBeTruthy());
    expect((completeButton() as HTMLButtonElement).disabled).toBe(false);
  });
});

describe('what the counter carries onto the bill', () => {
  it('sends the typed note on the ONE bill-note field, tagged for a home delivery', async () => {
    // cart_note is the same field the optical till's delivery row writes and
    // the packing desk reads. Staff had no box to type into on this counter.
    submitPosOrder.mockResolvedValue({ ok: true, orderId: 'o-1' });
    putSomethingInTheCart();
    renderCounter();

    fireEvent.change(screen.getByLabelText('Note for this bill'), {
      target: { value: 'gift wrap please' },
    });
    fireEvent.click(screen.getByRole('button', { name: /home delivery/i }));
    fireEvent.click(completeButton());

    await waitFor(() => expect(submitPosOrder).toHaveBeenCalledTimes(1));
    const sent = submitPosOrder.mock.calls[0][0] as { cart_note: string };
    expect(sent.cart_note).toBe('[HOME DELIVERY] gift wrap please');
  });

  it('holds the bill and recalls it through the SHARED hold/recall control', async () => {
    // The store auto-parks a cart when the screen idles on EVERY surface; a
    // cart parked on this counter used to be unrecoverable from this counter.
    putSomethingInTheCart();
    renderCounter();

    fireEvent.click(screen.getByRole('button', { name: /hold bill/i }));
    expect(usePOSStore.getState().cart).toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: /^held/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Recall' }));
    expect(usePOSStore.getState().cart.map((i) => i.name)).toEqual(['Ray-Ban Aviator']);
  });

  it('lets a wrong customer pick be cleared from the card', () => {
    usePOSStore.getState().setCustomer({ id: 'c-1', name: 'Asha', phone: '9876543210' } as never);
    renderCounter();
    fireEvent.click(screen.getByText('change-customer'));
    expect(usePOSStore.getState().customer).toBeNull();
  });

  it('discards the bill directly, but only past the confirm', () => {
    // "Start over" used to be Hold -> Held -> Discard, three taps through a
    // modal. The confirm is the guard: a mis-tap must not lose a sale.
    putSomethingInTheCart();
    renderCounter();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);

    fireEvent.click(screen.getByRole('button', { name: /discard bill/i }));
    expect(usePOSStore.getState().cart).toHaveLength(1);

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: /discard bill/i }));
    expect(usePOSStore.getState().cart).toHaveLength(0);
  });
});

describe('the general counter browse grid', () => {
  it('caps the grid at the shared result limit and says so', () => {
    // A capped list must SAY it is capped, or stock is silently hidden. The
    // limit is the strip's constant, so the two tills can never disagree.
    productRows = Array.from({ length: MAX_PRODUCT_RESULTS + 1 }, (_, i) => ({
      product_id: `p-${i}`,
      name: `Watch ${i}`,
      sku: `W${i}`,
      mrp: 1000,
      offer_price: 1000,
      stock: 5,
    }));
    renderCounter();

    // (No ^ anchor: the card's accessible name starts with its stock badge.)
    expect(screen.getAllByRole('button', { name: /Watch \d+/ })).toHaveLength(
      MAX_PRODUCT_RESULTS,
    );
    expect(screen.getByText(new RegExp(`first ${MAX_PRODUCT_RESULTS}`))).toBeTruthy();
  });
});
