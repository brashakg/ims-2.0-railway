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

vi.mock('../../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
}));
vi.mock('../../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

// Leaf components. Each is replaced by the smallest thing that still lets the
// surface render -- none of them is what this file is about.
vi.mock('../../../../components/pos/WalkoutComplianceBanner', () => ({ default: () => null }));
vi.mock('../../../../components/pos/WalkinWalkoutControls', () => ({ WalkinWalkoutControls: () => null }));
vi.mock('../../../../components/pos/CustomerCardWithLoyalty', () => ({ CustomerCardWithLoyalty: () => null }));
vi.mock('../../../../components/pos/POSCart', () => ({ CartSidebar: () => <div>cart</div> }));
vi.mock('../../../../components/pos/DiscountModal', () => ({
  DiscountModal: () => null,
  toDiscountItem: (x: unknown) => x,
}));
vi.mock('../../../../components/pos/BillDiscountCard', () => ({ BillDiscountCard: () => null }));
vi.mock('../../../../components/pos/POSPayment', () => ({ StepPayment: () => <div>payment</div> }));
vi.mock('../../../../components/pos/POSInvoice', () => ({
  StepComplete: () => <div>sale-complete-screen</div>,
}));
vi.mock('../../../../components/pos/BarcodeScanner', () => ({ BarcodeScanner: () => null }));
vi.mock('../../../../components/pos/SalespersonPicker', () => ({ SalespersonPicker: () => null }));
vi.mock('../../../../components/pos/CustomerSearchBar', () => ({ CustomerSearchBar: () => null }));
vi.mock('../PosWidgets', () => ({ PosWidgets: () => null }));

import { MemoryRouter } from 'react-router-dom';
import { GeneralCounterSurface } from '../GeneralCounterSurface';
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
    await waitFor(() => expect(screen.getByText('sale-complete-screen')).toBeTruthy());
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
    // The handler is `try { ... } finally { ... }` with no `catch`. A thrown
    // error (a network stack blowing up, a helper going undefined) therefore
    // escapes, the finally clears the spinner, and the operator sees a button
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
