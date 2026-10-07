// D7b stock lookup screen (owner ruling 2026-09-29), panel round 2:
//   - the Price column is what the till charges (posPriceGuard), not the raw
//     offer_price: an offer of 0 or none bills the MRP at the till, so the
//     screen showed "Rs 0" / "-" while the till charged Rs 12,990;
//   - after a scan (Enter) the box is selected, so the next scan replaces the
//     text instead of being appended to it ("...A2BV91...A3" matched nothing).
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const mockGet = vi.fn();
vi.mock('../../../services/api/client', () => ({ default: { get: (...a: unknown[]) => mockGet(...a) } }));
// productIntake's scan half pulls the services barrel; the price guard needs none of it.
vi.mock('../../../services/api', () => ({ inventoryApi: {} }));

import StockLookupPage from '../StockLookupPage';

const item = (product_id: string, mrp: number, offer_price: number | null) => ({
  product_id, sku: product_id, name: `Frame ${product_id}`, mrp, offer_price,
  stores: [{ store_id: 'S1', store_name: 'Dhanbad', available: 1, in_transit: 0 }],
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <StockLookupPage />
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
    mockGet.mockReset();
    mockGet.mockResolvedValue({
      data: {
        store_id: 'S1',
        items: [item('ZERO', 12990, 0), item('NONE', 12990, null), item('OFFER', 12990, 11990)],
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
});
