// ============================================================================
// F46: a till tile says how many THIS shop can sell, and never hides the colour
// ============================================================================
// Audit (sales staff, /pos/new): the 0-stock 'Havana' tile looked identical to
// the 8-in-stock 'Black' one -- no quantity on either, and both names cut to
// 'Carrera Ca 8895 Re...' so the colour that tells them apart was gone.
// Clicking Havana put it in the cart; only Complete sale refused it.
//
// The number on the tile is the oversell guard's own (GET /inventory/sellable
// asks orders/stock._assert_serialized_stock_available itself, pinned by
// backend tests/test_till_sellable_stock.py). null = the guard does not gate
// that row, so the tile must not call it out of stock either. The guard at
// Complete sale stays the authority; the figure is kept as fresh as a read
// can be (see 'the figure keeps up' below).

import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query';
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

// The App's own QueryClient defaults (App.tsx): every other query there is
// fresh for 5 minutes. The till's stock figure must not inherit that.
const appClient = () =>
  new QueryClient({ defaultOptions: { queries: { staleTime: 1000 * 60 * 5, retry: 1 } } });
const strip = (client: QueryClient, storeId = 'BV-BOK-01') => (
  <QueryClientProvider client={client}>
    <ProductResultsStrip storeId={storeId} query="" />
  </QueryClientProvider>
);
const tile = (colour: string) => screen.getByText(colour).closest('button') as HTMLButtonElement;
const answer = (sellable: Record<string, number | null>, store_id = 'BV-BOK-01') =>
  getSellable.mockResolvedValue({ store_id, sellable, canonical: {} });

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  getSellable.mockReset();
  getProducts.mockReset();
});

describe.each(['strip', 'grid'] as const)('the %s tile', (layout) => {
  // A card is handed the screen's ONE answer and reads its own row from it.
  const card = (stock?: number | null, product: Record<string, unknown> = HAVANA, id = HAVANA.product_id) =>
    render(
      <ProductCard
        product={product}
        layout={layout}
        stock={stock === undefined ? undefined : { store_id: 'BV-BOK-01', sellable: { [id]: stock } }}
        onPick={() => undefined}
      />,
    );
  const button = () => screen.getByRole('button') as HTMLButtonElement;

  it("finds its figure by the id the stock hook asks with (productIdOf), even a bare _id", () => {
    // The lookup lives in the card, so both tills read a row the same way the
    // hook asked for it. A row the counter knows only by _id must still block.
    const { product_id: _drop, ...bare } = HAVANA;
    card(0, { ...bare, _id: '66f1c0ffee00000000000001' }, '66f1c0ffee00000000000001');
    expect(screen.getByText('Out of stock')).toBeTruthy();
    expect(button().disabled).toBe(true);
  });

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
    expect(getSellable).toHaveBeenCalledWith(['FR-BLACK', 'FR-HAVANA'], ['FRAME', 'FRAME']);
    const havana = screen.getByText('Havana').closest('button') as HTMLButtonElement;
    const black = screen.getByText('Black').closest('button') as HTMLButtonElement;
    expect(havana.textContent).toMatch(/Out of stock/);
    expect(havana.disabled).toBe(true);
    expect(black.disabled).toBe(false);
  });

  it('sends each row as the order item_type the sale guard judges (a stock lens is LENS)', async () => {
    // A stock lens sent as its raw category would be gated as a frame and could
    // show 'Out of stock', while the guard never gates LENS lines at all.
    const LENS = { ...HAVANA, product_id: 'LN-1', name: 'Essilor Crizal 1.56 - Clear', category: 'OPTICAL_LENS' };
    getProducts.mockResolvedValue({ products: [LENS, BLACK] });
    answer({ 'FR-BLACK': 8, 'LN-1': null });
    render(strip(appClient()));
    await waitFor(() => expect(screen.getByText('8 in stock')).toBeTruthy());
    expect(getSellable).toHaveBeenCalledWith(['FR-BLACK', 'LN-1'], ['FRAME', 'LENS']);
  });

  it('asks for a row by the same id the tile looks its figure up by (productIdOf), even a bare _id', async () => {
    const { product_id: _drop, ...bare } = BLACK;
    getProducts.mockResolvedValue({ products: [{ ...bare, _id: '66f1c0ffee00000000000001' }] });
    answer({ '66f1c0ffee00000000000001': 2 });
    render(strip(appClient()));
    await waitFor(() => expect(screen.getByText('2 in stock')).toBeTruthy());
    expect(getSellable).toHaveBeenCalledWith(['66f1c0ffee00000000000001'], ['FRAME']);
  });
});

describe('the figure keeps up (it is never 5 minutes old)', () => {
  it('re-reads the moment this till completes a sale', async () => {
    getProducts.mockResolvedValue({ products: [BLACK] });
    answer({ 'FR-BLACK': 1 });
    render(strip(appClient()));
    await waitFor(() => expect(screen.getByText('1 in stock')).toBeTruthy());

    answer({ 'FR-BLACK': 0 }); // that last unit was just sold here
    act(() => usePOSStore.getState().setOrderResult('o-1', 'BV/INV/0001')); // submitPosOrder, every sale
    await waitFor(() => expect(tile('Black').disabled).toBe(true));
    expect(getSellable).toHaveBeenCalledTimes(2);
  });

  it('a tile that comes back on screen never shows the old figure, and reads afresh', async () => {
    const client = appClient();
    getProducts.mockResolvedValue({ products: [BLACK] });
    answer({ 'FR-BLACK': 0 });
    const first = render(strip(client));
    await waitFor(() => expect(tile('Black').disabled).toBe(true));
    first.unmount();
    await act(() => new Promise((r) => setTimeout(r, 20))); // the till shows something else

    answer({ 'FR-BLACK': 1 }); // a receipt put one back meanwhile
    render(strip(client));
    await screen.findByText('Black');
    expect(screen.queryByText('Out of stock')).toBeNull();
    expect(tile('Black').disabled).toBe(false);
    await waitFor(() => expect(screen.getByText('1 in stock')).toBeTruthy());
    expect(getSellable).toHaveBeenCalledTimes(2);
  });

  it('a tile left on screen re-reads when the till is looked at again', async () => {
    getProducts.mockResolvedValue({ products: [BLACK] });
    answer({ 'FR-BLACK': 0 });
    render(strip(appClient()));
    await waitFor(() => expect(tile('Black').disabled).toBe(true));

    answer({ 'FR-BLACK': 1 });
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    try {
      await waitFor(() => expect(screen.getByText('1 in stock')).toBeTruthy());
    } finally {
      focusManager.setFocused(undefined);
    }
  });

  it('a tile left on screen catches up with other tills every 30 seconds', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    try {
      getProducts.mockResolvedValue({ products: [BLACK] });
      answer({ 'FR-BLACK': 1 });
      render(strip(appClient()));
      await waitFor(() => expect(screen.getByText('1 in stock')).toBeTruthy());

      answer({ 'FR-BLACK': 0 }); // another till sold it
      act(() => vi.advanceTimersByTime(30_000));
      await waitFor(() => expect(tile('Black').disabled).toBe(true));
    } finally {
      vi.useRealTimers();
    }
  });

  it("never shows another shop's answer: a switch shows the screen's shop a moment later", async () => {
    // The server answers for the store in the sign-in token. A store switch
    // moves the screen first and the token after, so the first answer on the
    // new screen can still be the old shop's: it must never reach a tile.
    getProducts.mockResolvedValue({ products: [BLACK] });
    answer({ 'FR-BLACK': 3 }, 'BV-BOK-01'); // the token has not moved yet
    render(strip(appClient(), 'BV-DHN-01'));
    await screen.findByText('Black');
    await waitFor(() => expect(getSellable).toHaveBeenCalledTimes(1));
    answer({ 'FR-BLACK': 0 }, 'BV-DHN-01'); // now it has
    await act(() => new Promise((r) => setTimeout(r, 100))); // BOK's answer has landed
    expect(screen.queryByText(/in stock/)).toBeNull();
    expect(tile('Black').disabled).toBe(false);

    await waitFor(() => expect(tile('Black').disabled).toBe(true), { timeout: 3_000 });
    expect(getSellable).toHaveBeenCalledTimes(2);
  });

  it.each(['BV-BOK-01', null])(
    'a screen whose token never names its shop (%s) shows nothing and asks a few times, not every 2 s',
    async (tokenStore) => {
      // An AREA_MANAGER at a shop outside their list (switch_store 403s), or
      // a token with no store: the answer never names the screen's shop.
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
      try {
        getProducts.mockResolvedValue({ products: [BLACK] });
        answer({ 'FR-BLACK': 3 }, tokenStore as string);
        render(strip(appClient(), 'BV-DHN-01'));
        await act(() => vi.advanceTimersByTimeAsync(29_000));
        expect(screen.getByText('Black')).toBeTruthy();
        expect(screen.queryByText(/in stock/)).toBeNull();
        expect(getSellable.mock.calls.length).toBeGreaterThan(1); // it did ask again
        expect(getSellable.mock.calls.length).toBeLessThanOrEqual(4); // 1 + 3 retries
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it("never shows another shop's counts while a store switch re-reads", async () => {
    const client = appClient();
    getProducts.mockResolvedValue({ products: [BLACK] });
    answer({ 'FR-BLACK': 8 });
    const r = render(strip(client, 'BV-BOK-01'));
    await waitFor(() => expect(screen.getByText('8 in stock')).toBeTruthy());

    getSellable.mockReturnValue(new Promise(() => undefined)); // the other shop's answer is on its way
    r.rerender(strip(client, 'BV-DHN-01'));
    await waitFor(() => expect(getSellable).toHaveBeenCalledTimes(2));
    await screen.findByText('Black');
    expect(screen.queryByText('8 in stock')).toBeNull();
  });
});
