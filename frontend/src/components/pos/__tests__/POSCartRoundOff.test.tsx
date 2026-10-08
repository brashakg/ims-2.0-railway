// ============================================================================
// The till's bill shows its Round off line (owner ruling 2026-10-08)
// ============================================================================
// A bill that is not a whole rupee is rounded to the nearest one and the cart
// totals show the paise it moved on a separate "Round off" line. A bill that
// is already whole shows no such line.

import { render, screen } from '@testing-library/react';
import { it, expect, beforeEach } from 'vitest';
import { CartSidebar } from '../POSCart';
import { usePOSStore } from '../../../stores/posStore';

const add = (unit_price: number) =>
  usePOSStore.getState().addToCart({
    product_id: `p-${unit_price}`,
    name: 'Frame',
    sku: 'S',
    category: 'FRAME',
    unit_price,
    mrp: unit_price,
    quantity: 1,
    is_optical: false,
  } as never);

beforeEach(() => usePOSStore.getState().resetTransaction());

it('shows a rounded-up bill with a + Round off line', () => {
  add(1000.5);
  render(<CartSidebar />);
  expect(screen.getByText('Round off')).toBeTruthy();
  expect(screen.getByTestId('cart-round-off').textContent).toBe('+₹0.50');
});

it('shows a rounded-down bill with a − Round off line', () => {
  add(1000.49);
  render(<CartSidebar />);
  expect(screen.getByTestId('cart-round-off').textContent).toBe('−₹0.49');
});

it('shows no Round off line on a whole-rupee bill', () => {
  add(1000);
  render(<CartSidebar />);
  expect(screen.queryByText('Round off')).toBeNull();
});
