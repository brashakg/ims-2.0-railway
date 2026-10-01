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
import { CartSidebar, stockWarning } from '../POSCart';
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
  render(<CartSidebar stock={{ store_id: 'S', sellable: { 'FR-HAVANA': 0 } }} />);
  expect(screen.getByRole('alert').textContent).toMatch(/not in stock at this shop/i);
});

it('warns when the quantity passes what this shop has', () => {
  line('l-1', 'FR-BLACK', 2);
  render(<CartSidebar stock={{ store_id: 'S', sellable: { 'FR-BLACK': 1 } }} />);
  expect(screen.getByRole('alert').textContent).toMatch(/only 1 in stock at this shop/i);
});

it('adds up two lines of the same product, as the sale guard does', () => {
  line('l-1', 'FR-BLACK');
  line('l-2', 'FR-BLACK');
  render(<CartSidebar stock={{ store_id: 'S', sellable: { 'FR-BLACK': 1 } }} />);
  expect(screen.getAllByRole('alert')).toHaveLength(2);
});

it('stays quiet when there is enough, when the guard does not gate it, or before the count arrives', () => {
  line('l-1', 'FR-BLACK', 2);
  line('l-2', 'SVC-EYE-TEST');
  const r = render(<CartSidebar stock={{ store_id: 'S', sellable: { 'FR-BLACK': 8, 'SVC-EYE-TEST': null } }} />);
  expect(screen.queryByRole('alert')).toBeNull();
  r.unmount();
  render(<CartSidebar />);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('stays quiet on the last unit: one in the cart, one on the shelf', () => {
  expect(stockWarning(1, 1)).toBeNull();
  expect(stockWarning(1, 2)).toMatch(/only 1 in stock/i);
  line('l-1', 'FR-BLACK');
  render(<CartSidebar stock={{ store_id: 'S', sellable: { 'FR-BLACK': 1 } }} />);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('adds up a picked line and a scanned line of one product under the id the guard uses', () => {
  // A tile row that carried only its Mongo _id, then the same frame scanned
  // (resolveBarcode gives the canonical product_id): the guard sums both under
  // FR-BLACK and refuses 2 > 1, so the cart must warn on both lines.
  line('l-1', '66f1c0ffee00000000000001');
  line('l-2', 'FR-BLACK');
  render(
    <CartSidebar
      stock={{
        store_id: 'S',
        sellable: { '66f1c0ffee00000000000001': 1, 'FR-BLACK': 1 },
        canonical: { '66f1c0ffee00000000000001': 'FR-BLACK', 'FR-BLACK': 'FR-BLACK' },
      }}
    />,
  );
  expect(screen.getAllByRole('alert')).toHaveLength(2);
});

it("reads a line's figure under the id the cart asked by, not the canonical one", () => {
  // The count comes back keyed by the id the cart sent (a bare Mongo _id);
  // `canonical` is only for adding lines up. Looking the figure up under
  // FR-BLACK finds nothing and hides the warning the guard will act on.
  line('l-1', '66f1c0ffee00000000000001');
  render(
    <CartSidebar
      stock={{
        store_id: 'S',
        sellable: { '66f1c0ffee00000000000001': 0 },
        canonical: { '66f1c0ffee00000000000001': 'FR-BLACK' },
      }}
    />,
  );
  expect(screen.getByRole('alert').textContent).toMatch(/not in stock at this shop/i);
});
