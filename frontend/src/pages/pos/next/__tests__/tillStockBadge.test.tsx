// ============================================================================
// F46: a till tile says how many THIS shop can sell, and never hides the colour
// ============================================================================
// Audit (sales staff, /pos/new): the 0-stock 'Havana' tile looked identical to
// the 8-in-stock 'Black' one -- no quantity on either, and both names cut to
// 'Carrera Ca 8895 Re...' so the colour that tells them apart was gone.
// Clicking Havana put it in the cart; only Complete sale refused it.
//
// The number on the tile is the oversell guard's own (GET /inventory/sellable
// -> orders/stock.sellable_units, pinned by backend
// tests/test_till_sellable_stock.py). null = the guard does not gate that row,
// so the tile must not call it out of stock either.

import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getSellable = vi.fn();
const getProducts = vi.fn();
vi.mock('../../../../services/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../services/api')>()),
  inventoryApi: { getSellable: (...a: unknown[]) => getSellable(...a) },
  productApi: { getProducts: (...a: unknown[]) => getProducts(...a) },
}));

import { ProductCard, ProductResultsStrip } from '../ProductResultsStrip';
import { usePOSStore } from '../../../../stores/posStore';

const HAVANA = {
  product_id: 'FR-HAVANA',
  name: 'Carrera Ca 8895 Rectangle Unisex Eyeglasses Frame - Havana',
  sku: 'FRCA8895HAV',
  brand: 'Carrera',
  category: 'FRAME',
  mrp: 6990,
  offer_price: 5990,
};
const BLACK = { ...HAVANA, product_id: 'FR-BLACK', sku: 'FRCA8895BLK', name: HAVANA.name.replace('Havana', 'Black') };

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  getSellable.mockReset();
  getProducts.mockReset();
});

describe.each(['strip', 'grid'] as const)('the %s tile', (layout) => {
  const card = (stock?: number | null) =>
    render(<ProductCard product={HAVANA} layout={layout} stock={stock} onPick={() => undefined} />);
  const button = () => screen.getByRole('button') as HTMLButtonElement;

  it('says how many this shop has and stays pickable', () => {
    card(8);
    expect(screen.getByText('8 in stock')).toBeTruthy();
    expect(button().disabled).toBe(false);
  });

  it('says Out of stock and cannot be picked (oversell BLOCKS)', () => {
    card(0);
    expect(screen.getAllByText('Out of stock')).toHaveLength(1);
    expect(button().disabled).toBe(true);
  });

  it('shows no figure and blocks nothing when the guard does not gate the row', () => {
    for (const stock of [null, undefined]) {
      const r = card(stock);
      expect(screen.queryByText(/in stock/i)).toBeNull();
      expect(button().disabled).toBe(false);
      r.unmount();
    }
  });

  it('keeps the colour whole on its own line; only the model is cut', () => {
    card(8);
    const colour = screen.getByText('Havana');
    expect(colour.className).not.toMatch(/truncate|line-clamp/);
    expect(screen.getByText('Carrera Ca 8895 Rectangle Unisex Eyeglasses Frame').className).toMatch(
      /truncate|line-clamp/,
    );
  });
});

describe('the till strip', () => {
  it("asks for this shop's sellable counts and badges each tile with them", async () => {
    getProducts.mockResolvedValue({ products: [BLACK, HAVANA] });
    getSellable.mockResolvedValue({ store_id: 'BV-BOK-01', sellable: { 'FR-BLACK': 8, 'FR-HAVANA': 0 } });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ProductResultsStrip storeId="BV-BOK-01" query="" />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText('8 in stock')).toBeTruthy());
    expect(getSellable).toHaveBeenCalledWith('BV-BOK-01', ['FR-BLACK', 'FR-HAVANA'], ['FRAME', 'FRAME']);
    const havana = screen.getByText('Havana').closest('button') as HTMLButtonElement;
    const black = screen.getByText('Black').closest('button') as HTMLButtonElement;
    expect(havana.textContent).toMatch(/Out of stock/);
    expect(havana.disabled).toBe(true);
    expect(black.disabled).toBe(false);
  });
});
