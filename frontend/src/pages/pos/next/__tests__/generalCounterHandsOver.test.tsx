// ============================================================================
// A completed take-away counter sale is HANDED OVER (owner ruling 2026-09-04)
// ============================================================================
// The goods leave with the customer and a tax invoice is issued, so the sale
// ends DELIVERED with the CASHIER (the signed-in user) on the handover record
// -- through the server's existing /ready + /deliver doors, never a status
// write of its own. Pinned here:
//   1. a take-away sale calls /ready then /deliver, naming the SIGNED-IN user
//      (not the bill's salesperson) as the person who handed over;
//   2. a home-delivery bill is NOT handed over at the till (the packing desk
//      still has it);
//   3. when the hand-over step fails after a successful sale the failure is
//      LOUD, and the sale still lands on its completion screen -- the money is
//      recorded and the customer has the goods, so nothing is un-completed or
//      re-submitted;
//   4. /deliver is never attempted when /ready was refused.

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
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['SALES_STAFF'] },
  }),
}));

const submitPosOrder = vi.fn();
vi.mock('../../../../components/pos/submitOrder', () => ({
  submitPosOrder: (...a: unknown[]) => submitPosOrder(...a),
}));

const markReady = vi.fn();
const deliverOrder = vi.fn();
vi.mock('../../../../services/api/sales', () => ({
  orderApi: {
    markReady: (...a: unknown[]) => markReady(...a),
    deliverOrder: (...a: unknown[]) => deliverOrder(...a),
  },
}));

vi.mock('../../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
}));
vi.mock('../../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../../components/pos/WalkoutComplianceBanner', () => ({ default: () => null }));
vi.mock('../../../../components/pos/WalkinWalkoutControls', () => ({ WalkinWalkoutControls: () => null }));
vi.mock('../../../../components/pos/HeldBillsControls', () => ({ HeldBillsControls: () => null }));
vi.mock('../../../../components/pos/CustomerCardWithLoyalty', () => ({
  CustomerCardWithLoyalty: () => null,
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
// The completion screen echoes the one prop this file cares about.
vi.mock('../SaleCompleteScreen', () => ({
  CounterCompleteScreen: (props: { orderId: string; delivered?: boolean }) => (
    <div>counter-complete-screen:{props.orderId}:delivered={String(!!props.delivered)}</div>
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
import { usePOSStore } from '../../../../stores/posStore';

function renderCounter() {
  return render(
    <MemoryRouter>
      <GeneralCounterSurface />
    </MemoryRouter>,
  );
}

function ringUpAWatch() {
  const s = usePOSStore.getState();
  s.addToCart({
    id: 'p-1',
    product_id: 'p-1',
    name: 'Titan watch',
    category: 'WATCH',
    quantity: 1,
    unit_price: 5000,
    price: 5000,
    mrp: 5000,
    discount_percent: 0,
    discount_amount: 0,
    tax_rate: 18,
  } as never);
  // The bill's salesperson is SOMEONE ELSE -- the handover must name the
  // signed-in cashier, never this attribution.
  s.setSalesperson('u-seller', 'Rekha');
}

const completeButton = () => screen.getByRole('button', { name: /complete sale|saving/i });
const completionScreen = (delivered: boolean) =>
  screen.getByText(`counter-complete-screen:o-1:delivered=${delivered}`);

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  submitPosOrder.mockReset().mockResolvedValue({ ok: true, orderId: 'o-1', orderNumber: 'ORD-1' });
  markReady.mockReset().mockResolvedValue({ order_id: 'o-1', status: 'READY' });
  deliverOrder.mockReset().mockResolvedValue({ order_id: 'o-1', status: 'DELIVERED' });
});

describe('a completed take-away counter sale is handed over', () => {
  it('takes the paid sale through /ready then /deliver, naming the signed-in cashier', async () => {
    ringUpAWatch();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(deliverOrder).toHaveBeenCalledTimes(1));

    expect(markReady).toHaveBeenCalledWith('o-1');
    expect(markReady.mock.invocationCallOrder[0]).toBeLessThan(
      deliverOrder.mock.invocationCallOrder[0],
    );
    expect(deliverOrder).toHaveBeenCalledWith('o-1', {
      handover: { delivered_by_id: 'u-1', delivered_by_name: 'Meena' },
    });
    // The salesperson attribution is not what went on the handover ...
    expect(JSON.stringify(deliverOrder.mock.calls[0][1])).not.toContain('u-seller');
    // ... and it is still on the bill for the completion screen.
    expect(usePOSStore.getState().salesperson_id).toBe('u-seller');
    expect(completionScreen(true)).toBeTruthy();
    expect(screen.queryByText(/could NOT be marked delivered/)).toBeNull();
  });

  it('does NOT hand over a home-delivery bill -- the packing desk still has it', async () => {
    ringUpAWatch();
    renderCounter();

    fireEvent.click(screen.getByRole('button', { name: /home delivery/i }));
    fireEvent.click(completeButton());
    await waitFor(() => expect(completionScreen(false)).toBeTruthy());

    expect(markReady).not.toHaveBeenCalled();
    expect(deliverOrder).not.toHaveBeenCalled();
  });

  it('is LOUD when /deliver is refused, and the sale still stands on its completion screen', async () => {
    deliverOrder.mockRejectedValue(
      Object.assign(new Error('Rs 500.00 is still due on this order.'), {
        detail: 'Rs 500.00 is still due on this order.',
      }),
    );
    ringUpAWatch();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(completionScreen(false)).toBeTruthy());

    const banner = screen.getByText(/could NOT be marked delivered/);
    expect(banner.textContent).toContain('ORD-1');
    expect(banner.textContent).toContain('Rs 500.00 is still due on this order.');
    expect(banner.textContent).toMatch(/Orders screen/);
    // The sale was NOT re-submitted or undone.
    expect(submitPosOrder).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('legacy-step-complete')).toBeNull();
  });

  it('treats a non-DELIVERED answer from /deliver as a failure, not a success', async () => {
    deliverOrder.mockResolvedValue({ order_id: 'o-1', status: 'READY' });
    ringUpAWatch();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(completionScreen(false)).toBeTruthy());
    expect(screen.getByText(/could NOT be marked delivered/)).toBeTruthy();
  });

  it('never attempts /deliver when /ready was refused', async () => {
    markReady.mockRejectedValue(new Error('Cannot mark as ready - current status is DRAFT.'));
    ringUpAWatch();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(completionScreen(false)).toBeTruthy());

    expect(deliverOrder).not.toHaveBeenCalled();
    expect(screen.getByText(/could NOT be marked delivered/).textContent).toContain(
      'current status is DRAFT',
    );
  });

  it('keeps the submit warning AND the hand-over failure on screen together', async () => {
    submitPosOrder.mockResolvedValue({
      ok: true,
      orderId: 'o-1',
      orderNumber: 'ORD-1',
      warning: 'Order saved, but CASH Rs 5,000 did NOT record against it.',
    });
    deliverOrder.mockRejectedValue(new Error('boom'));
    ringUpAWatch();
    renderCounter();

    fireEvent.click(completeButton());
    await waitFor(() => expect(completionScreen(false)).toBeTruthy());
    const text = screen.getByText(/did NOT record against it/).textContent || '';
    expect(text).toMatch(/could NOT be marked delivered/);
  });
});
