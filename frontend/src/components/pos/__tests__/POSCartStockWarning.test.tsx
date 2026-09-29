// ============================================================================
// F46: the cart line warns the moment an item is not in stock at this shop
// ============================================================================
// Audit: a 0-stock frame went into the cart at Rs 5,990 with no warning; only
// Complete sale refused it -- after the customer had been quoted. The line now
// says so as soon as it is added. The count is the oversell guard's own
// (GET /inventory/sellable); the guard at Complete sale is unchanged, this is
// a warning, not a second block. Quantities of one product ADD UP across
// lines, exactly as the guard sums them.

import { render, screen } from '@testing-library/react';
import { it, expect, beforeEach } from 'vitest';
import { CartSidebar } from '../POSCart';
import { usePOSStore } from '../../../stores/posStore';

const line = (id: string, product_id: string, quantity = 1) =>
  usePOSStore.getState().addToCart({
    id,
    product_id,
    name: `Carrera ${id}`,
    quantity,
    unit_price: 5990,
    price: 5990,
    mrp: 6990,
    tax_rate: 5,
  } as never);

beforeEach(() => usePOSStore.getState().resetTransaction());

it('warns on a line that is not in stock at this shop', () => {
  line('l-1', 'FR-HAVANA');
  render(<CartSidebar sellable={{ 'FR-HAVANA': 0 }} />);
  expect(screen.getByRole('alert').textContent).toMatch(/not in stock at this shop/i);
});

it('warns when the quantity passes what this shop has', () => {
  line('l-1', 'FR-BLACK', 2);
  render(<CartSidebar sellable={{ 'FR-BLACK': 1 }} />);
  expect(screen.getByRole('alert').textContent).toMatch(/only 1 in stock at this shop/i);
});

it('adds up two lines of the same product, as the sale guard does', () => {
  line('l-1', 'FR-BLACK');
  line('l-2', 'FR-BLACK');
  render(<CartSidebar sellable={{ 'FR-BLACK': 1 }} />);
  expect(screen.getAllByRole('alert')).toHaveLength(2);
});

it('stays quiet when there is enough, when the guard does not gate it, or before the count arrives', () => {
  line('l-1', 'FR-BLACK', 2);
  line('l-2', 'SVC-EYE-TEST');
  const r = render(<CartSidebar sellable={{ 'FR-BLACK': 8, 'SVC-EYE-TEST': null }} />);
  expect(screen.queryByRole('alert')).toBeNull();
  r.unmount();
  render(<CartSidebar />);
  expect(screen.queryByRole('alert')).toBeNull();
});
