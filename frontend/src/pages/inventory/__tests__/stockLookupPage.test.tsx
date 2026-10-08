// D7b stock lookup screen (owner ruling 2026-09-29), panel round 2:
//   - the Price column is what the till charges (posPriceGuard), not the raw
//     offer_price: an offer of 0 or none bills the MRP at the till, so the
//     screen showed "Rs 0" / "-" while the till charged Rs 12,990;
//   - after a scan (Enter) the box is selected, so the next scan replaces the
//     text instead of being appended to it ("...A2BV91...A3" matched nothing).
// Round 7: the till REFUSES an offer above MRP and a zero or NaN price
// (posPriceGuard), so those rows read '-'; a re-typed offer||mrp chain would
// show Rs 15,990 / Rs 0 / Rs NaN, a price the till never charges.
// Round 5: a shop the till does not count reads 'not tracked here', a lens
// 'see Power Grid' - never a 0 the till would not keep.
// Round 6: the line type is the till's own mapCategory (imported, never
// re-typed: 'LENSES' and 'SVC' are its spellings too), checked against the
// guard's lists the server sends; only a role the Power Grid's route gate
// admits is sent there.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mockGet = vi.fn();
vi.mock('../../../services/api/client', () => ({ default: { get: (...a: unknown[]) => mockGet(...a) } }));
// productIntake's scan half pulls the services barrel; the price guard needs none of it.
vi.mock('../../../services/api', () => ({ inventoryApi: {} }));
// The signed-in roles; hasRole answers as AuthContext's does (ADMINs pass all).
let roles: string[] = ['CASHIER'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    hasRole: (want: string[]) =>
      roles.includes('SUPERADMIN') || roles.includes('ADMIN') || want.some((r) => roles.includes(r)),
  }),
}));

import StockLookupPage from '../StockLookupPage';
import { POWER_GRID_ROLES, STOCK_LOOKUP_ROLES } from '../inventoryRoles';

// What GET /inventory/lookup sends: orders/stock's own sets, as the server
// reads them (pinned there against the real guard by test_counter_stock_lookup.py).
const GUARD_LISTS = {
  not_counted_item_types: ['CONSULT', 'CONSULTATION', 'EYE_CHECKUP', 'EYE_EXAM', 'EYE_TEST', 'LENS', 'OPTOMETRY', 'SERVICE'],
  lens_grid_item_types: ['LENS'],
};

const item = (product_id: string, mrp: number, offer_price: number | null) => ({
  product_id, sku: product_id, name: `Frame ${product_id}`, mrp, offer_price,
  stores: [{ store_id: 'S1', store_name: 'Dhanbad', available: 1, in_transit: 0, tracked: true }],
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <StockLookupPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByLabelText('Search stock') as HTMLInputElement;
}

const priceOf = (name: string) => {
  const row = screen.getByText(name).closest('tr')!;
  return within(row).getAllByRole('cell')[4].textContent; // Product, Colour, Size, MRP, Price
};

describe('Stock lookup screen', () => {
  beforeEach(() => {
    roles = ['CASHIER'];
    mockGet.mockReset();
    mockGet.mockResolvedValue({
      data: {
        store_id: 'S1',
        items: [
          item('ZERO', 12990, 0), item('NONE', 12990, null), item('OFFER', 12990, 11990),
          item('OVER', 12990, 15990), item('NOPRICE', 0, 0), item('NAN', NaN, NaN),
        ],
      },
    });
  });

  it('shows the price the till charges: MRP when there is no offer', async () => {
    const box = renderPage();
    fireEvent.change(box, { target: { value: 'CA8895' } });
    fireEvent.submit(box.closest('form')!);
    await waitFor(() => expect(screen.getByText('Frame ZERO')).toBeTruthy());
    expect(priceOf('Frame ZERO')).toBe('₹12,990');
    expect(priceOf('Frame NONE')).toBe('₹12,990');
    expect(priceOf('Frame OFFER')).toBe('₹11,990');
  });

  it('shows no price where the till refuses it: offer above MRP, zero or NaN', async () => {
    const box = renderPage();
    fireEvent.change(box, { target: { value: 'CA8895' } });
    fireEvent.submit(box.closest('form')!);
    await waitFor(() => expect(screen.getByText('Frame OVER')).toBeTruthy());
    expect(priceOf('Frame OVER')).toBe('-');
    expect(priceOf('Frame NOPRICE')).toBe('-');
    expect(priceOf('Frame NAN')).toBe('-');
  });

  it('selects the box after a scan so the next scan replaces it', async () => {
    const box = renderPage();
    fireEvent.change(box, { target: { value: 'BV91FA3858A2' } });
    fireEvent.submit(box.closest('form')!);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(box.selectionStart).toBe(0);
    expect(box.selectionEnd).toBe('BV91FA3858A2'.length);
  });

  it('asks again when the same barcode is scanned twice', async () => {
    // Round 5: the same scan is the same query key, so the screen kept the
    // first answer - a frame sold at the till since still read 1 here.
    const sold = { ...item('X', 12990, 11990), stores: [{ ...item('X', 0, 0).stores[0], available: 0 }] };
    mockGet.mockReset();
    mockGet
      .mockResolvedValueOnce({ data: { store_id: 'S1', items: [item('X', 12990, 11990)] } })
      .mockResolvedValueOnce({ data: { store_id: 'S1', items: [sold] } });
    const box = renderPage();
    const scan = () => {
      fireEvent.change(box, { target: { value: 'BV91FA3858A2' } });
      fireEvent.submit(box.closest('form')!);
    };
    const here = () => within(screen.getByText('Frame X').closest('tr')!).getAllByRole('cell')[5].textContent;
    scan();
    await waitFor(() => expect(here()).toBe('1'));
    scan();
    await waitFor(() => expect(here()).toBe('0'));
    expect(mockGet).toHaveBeenCalledTimes(2);
  });

  const search = async (reply: object, name: string) => {
    mockGet.mockReset();
    mockGet.mockResolvedValue({ data: { store_id: 'S1', ...GUARD_LISTS, ...reply } });
    const box = renderPage();
    fireEvent.change(box, { target: { value: 'F' } });
    fireEvent.submit(box.closest('form')!);
    await waitFor(() => expect(screen.getByText(name)).toBeTruthy());
  };
  const shopCells = (name: string) =>
    within(screen.getByText(name).closest('tr')!).getAllByRole('cell').slice(5).map((c) => c.textContent);
  const bokaro = { store_id: 'S2', store_name: 'Bokaro', available: 0, in_transit: 0, tracked: false };
  const product = (id: string, category: string) => ({
    ...item(id, 3000, 3000), category, stores: [...item(id, 0, 0).stores, bokaro],
  });

  // Every spelling below is one the till's mapCategory takes; the expected
  // cells are what the till does with it (GET /inventory/sellable).
  const COUNTED = ['1', 'not tracked here'];
  const NOT_COUNTED = ['not tracked here', 'not tracked here'];
  const LENS_POINTER = ['counted by power - ask the optometrist or a manager'];
  it.each([
    ['FRAME', COUNTED],
    ['SUNGLASS', COUNTED],
    ['SUNGLASSES', COUNTED],
    ['CONTACT_LENS', COUNTED],
    ['COLORED_CONTACT_LENS', COUNTED],
    ['OPTICAL_LENS', LENS_POINTER],
    ['LENSES', LENS_POINTER],
    ['LS', LENS_POINTER],
    ['SERVICES', NOT_COUNTED],
    ['SERVICE', NOT_COUNTED],
    ['SVC', NOT_COUNTED],
  ])('a %s product reads as the till sells it', async (category, cells) => {
    await search({ items: [product('P', category)] }, 'Frame P');
    expect(shopCells('Frame P')).toEqual(cells);
  });

  // Round 7: the server caps the rows (50 hits, 200 per family), so a big
  // contact-lens model is cut; the screen says so instead of looking whole.
  const CUT = 'Showing 1 of 300 powers - scan the box or type the power to narrow';
  it('says when the caps cut the list, and how big it is', async () => {
    await search({ items: [product('P', 'CONTACT_LENS')], total: 300, truncated: true }, 'Frame P');
    expect(screen.getByText(CUT)).toBeTruthy();
  });
  it('says nothing about a cut when the list is whole', async () => {
    await search({ items: [product('P', 'CONTACT_LENS')], total: 1, truncated: false }, 'Frame P');
    expect(screen.queryByText(/^Showing/)).toBeNull();
  });

  it.each(STOCK_LOOKUP_ROLES.map((r) => [r]))(
    'sends a %s to the Power Grid only if its route gate admits them',
    async (role) => {
      roles = [role];
      await search({ items: [product('L', 'OPTICAL_LENS')] }, 'Frame L');
      const admitted = role === 'SUPERADMIN' || role === 'ADMIN' || POWER_GRID_ROLES.includes(role);
      const link = within(screen.getByText('Frame L').closest('tr')!).queryByRole('link', { name: 'see Power Grid' });
      expect(link?.getAttribute('href') ?? null).toBe(admitted ? '/inventory/power-grid' : null);
      if (!admitted) expect(shopCells('Frame L')).toEqual(LENS_POINTER);
    },
  );
});
