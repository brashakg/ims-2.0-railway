// ============================================================================
// ONE product card for both tills
// ============================================================================
// The counter's browse grid had re-typed the strip's card and the two drifted
// five ways: the id chain was missing plain `id`, the offer price was read a
// second way, the stock spelling chain was re-typed, there was no low-stock
// badge, and no result cap. These pin the reads that were wrong on the counter
// copy, on the shared card both tills now render. The result cap is pinned
// where it is applied (generalCounterCompleteSale.test.tsx).

import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import { ProductCard, productIdOf, stockOf } from '../ProductResultsStrip';
import { usePOSStore } from '../../../../stores/posStore';

beforeEach(() => usePOSStore.getState().resetTransaction());

describe('the shared product reads', () => {
  it('falls back to plain `id` -- an order-shaped join carries no product_id/_id', () => {
    expect(productIdOf({ id: 'x' })).toBe('x');
    expect(productIdOf({ product_id: 'p', id: 'x' })).toBe('p');
  });

  it('tells "no stock figure" apart from zero', () => {
    expect(stockOf({})).toBeNull();
    expect(stockOf({ stock: 0 })).toBe(0);
    expect(stockOf({ quantity: 2 })).toBe(2);
    expect(stockOf({ stock_available: 3 })).toBe(3);
  });
});

describe('the shared card', () => {
  const base = { name: 'Titan Neo', sku: 'T1', mrp: 5000, offer_price: 4500 };
  const card = (product: Record<string, unknown>) =>
    render(<ProductCard product={product} layout="grid" onPick={() => undefined} />);
  const button = () => screen.getByRole('button') as HTMLButtonElement;

  it('marks a row already in the cart even when the row only carries `id`', () => {
    usePOSStore.getState().addToCart({
      id: 'p-1',
      product_id: 'p-1',
      name: 'Titan Neo',
      quantity: 1,
      unit_price: 4500,
      price: 4500,
      mrp: 5000,
      tax_rate: 18,
    } as never);
    card({ ...base, id: 'p-1' });
    expect(screen.getByText('In cart')).toBeTruthy();
    expect(button().disabled).toBe(true);
  });

  it('badges low stock, blocks zero stock, and does not block a row with no figure', () => {
    const low = card({ ...base, stock: 2 });
    expect(screen.getByText('2 left')).toBeTruthy();
    expect(button().disabled).toBe(false);
    low.unmount();

    const out = card({ ...base, stock: 0 });
    expect(screen.getByText('Out of stock')).toBeTruthy();
    expect(button().disabled).toBe(true);
    out.unmount();

    card(base);
    expect(screen.queryByText(/left|Out/)).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it('prices the card off the same offer chain the cart line will carry', () => {
    card({ ...base, offer_price: undefined, offerPrice: 4200 });
    expect(screen.getByText('₹4,200')).toBeTruthy();
    expect(screen.getByText('₹5,000')).toBeTruthy();
  });
});
