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
