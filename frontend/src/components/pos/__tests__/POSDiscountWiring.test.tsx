// Discounts on the new one-screen POS (owner spec 3).
//
// Until this wiring you could not apply a discount on /pos/new or /pos/counter
// AT ALL: the cart DISPLAYED a discount but DiscountModal was only ever opened
// from POSLayout, the classic surface. An optical shop cannot bill without
// discounts, so this was the last hard blocker on switching /pos over.
//
// The rules themselves are NOT retested here - the cap check and the
// compulsory reason live in DiscountModal and the server re-checks against
// canonical pricing_caps. What is tested is the WIRING, and that the classic
// cart is byte-identical to before.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', discountCap: 10, roles: ['SALES_STAFF'] } }),
}));

import { usePOSStore } from '../../../stores/posStore';
import { CartSidebar } from '../POSCart';
import { BillDiscountCard } from '../BillDiscountCard';
import { toDiscountItem } from '../DiscountModal';
import { submitPosOrder } from '../submitOrder';

const line = (id: string) => ({
  id,
  product_id: 'p-' + id,
  name: 'Frame ' + id,
  sku: 'SKU' + id,
  category: 'FRAME',
  unit_price: 1000,
  mrp: 1000,
  quantity: 1,
  is_optical: true,
  discount_percent: 0,
  discount_amount: 0,
  line_total: 1000,
}) as any;

describe('POS discount wiring', () => {
  beforeEach(() => {
    usePOSStore.setState({
      cart: [line('a')],
      cart_discount_percent: 0,
      cart_discount_amount: 0,
      cart_discount_reason: null,
      cart_discount_approved_by: null,
    });
  });

  it('leaves the CLASSIC cart untouched — no trigger without a handler', () => {
    // The classic surface opens the modal from its review step and passes
    // nothing. If this ever renders a button, the classic cart has changed.
    render(<CartSidebar />);
    expect(screen.queryByRole('button', { name: /discount/i })).toBeNull();
  });

  it('offers a per-line discount on the new surfaces and hands back the line', () => {
    const onOpen = vi.fn();
    render(<CartSidebar onOpenDiscount={onOpen} />);
    fireEvent.click(screen.getByRole('button', { name: /^discount$/i }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen.mock.calls[0][0].id).toBe('a');
  });

  it('clamps the bill discount to the role cap', () => {
    render(<BillDiscountCard />);
    const pct = screen.getByLabelText('Overall discount percent') as HTMLInputElement;
    // 40% from a 10%-cap user must not reach the store.
    fireEvent.change(pct, { target: { value: '40' } });
    expect(usePOSStore.getState().cart_discount_percent).toBe(10);
  });

  it('asks for a reason before a bill discount is usable', () => {
    render(<BillDiscountCard />);
    fireEvent.change(screen.getByLabelText('Overall discount percent'), {
      target: { value: '5' },
    });
    // A discount with no offer behind it needs a written reason (owner ruling).
    expect(screen.getByText(/Required — at least 4 characters/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Overall discount reason'), {
      target: { value: 'festival offer' },
    });
    expect(screen.queryByText(/Required — at least 4 characters/)).toBeNull();
    expect(usePOSStore.getState().cart_discount_reason).toBe('festival offer');
  });

  // ── Findings from the money review ─────────────────────────────────────────

  it('refuses to discount an item HQ has already discounted', () => {
    // offer_price below MRP means HQ set the price. The order-create door
    // refuses a further store discount with a 403 for non-admins, and does NOT
    // refuse it for an ADMIN - which would sell below the HQ floor. Either way
    // the control must not be offered, or the cashier quotes a price, takes the
    // cash, and only then finds the sale cannot be saved.
    usePOSStore.setState({
      cart: [{ ...line('a'), mrp: 8990, unit_price: 7192, offer_price: 7192 } as any],
    });
    render(<CartSidebar onOpenDiscount={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /discount/i })).toBeNull();
    expect(screen.getByText(/HQ offer/i)).toBeTruthy();
  });

  it('names the product in the discount modal payload', () => {
    // The modal renders `productName`. The mapper used to write `name`, leaving
    // the subtitle blank - on a bill with two same-priced frames nothing said
    // which line was being discounted, and their caps differ by brand.
    const mapped = toDiscountItem({ ...line('a'), name: 'Cartier Panthere' } as any) as any;
    expect(mapped.productName).toBe('Cartier Panthere');
  });

  it('refuses an over-tender instead of saving an order with the cash missing', async () => {
    // A discount applied AFTER the tender was entered leaves payments stale.
    // Only under-payment used to be checked, so the order was created at the
    // NEW lower total, the server refused every payment as "exceeds balance
    // due", and the catch swallowed it: order fully outstanding, cash in the
    // drawer, unexplained surplus at day-end.
    usePOSStore.setState({
      cart: [line('a')],
      payments: [{ id: 'p1', method: 'CASH', amount: 10000 }] as any,
      cart_discount_percent: 0,
      cart_discount_amount: 0,
    });
    const store = usePOSStore.getState();
    store.setCartDiscount(10, 'regular customer');
    const after = usePOSStore.getState();
    expect(after.getBalance()).toBeLessThan(0);

    const res = await submitPosOrder(usePOSStore.getState() as any, 'idem-test');
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/more than the bill total/i);
  });

  it('carries the reason through when the percent is edited afterwards', () => {
    // Regression guard: setCartDiscount takes the reason as its SECOND
    // argument, so a percent-only call wipes it unless the caller passes the
    // reason back. A silently blanked reason is a sale the server refuses at
    // the very end, after the customer is already waiting.
    render(<BillDiscountCard />);
    fireEvent.change(screen.getByLabelText('Overall discount percent'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText('Overall discount reason'), {
      target: { value: 'damaged box' },
    });
    fireEvent.change(screen.getByLabelText('Overall discount percent'), {
      target: { value: '7' },
    });
    expect(usePOSStore.getState().cart_discount_percent).toBe(7);
    expect(usePOSStore.getState().cart_discount_reason).toBe('damaged box');
  });
});
