// ============================================================================
// The till's bill shows its Round off line (owner ruling 2026-10-08)
// ============================================================================
// The cart asks the server for the bill (POST /orders/quote) and shows the
// paise the SERVER rounded it by on a separate "Round off" line. When there is
// a round off, Subtotal / Discount / GST show their paise too, so the figures
// on screen add up to the Total. Nothing is rounded locally: with no quote yet
// there is no Round off line at all.

import { render, screen, waitFor } from '@testing-library/react';
import { it, expect, beforeEach, vi } from 'vitest';

vi.mock('../../../services/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    orderApi: { ...(actual.orderApi as object), quoteBill: vi.fn() },
  };
});

import { orderApi } from '../../../services/api';
import { CartSidebar } from '../POSCart';
import { usePOSStore } from '../../../stores/posStore';

const mockedQuote = vi.mocked(orderApi.quoteBill);

const add = (unit_price: number, category = 'FRAME') =>
  usePOSStore.getState().addToCart({
    product_id: `p-${category}-${unit_price}`,
    name: category,
    sku: 'S',
    category,
    unit_price,
    mrp: unit_price,
    quantity: 1,
    is_optical: false,
  } as never);

const serverBill = (grand_total: number, round_off: number, tax: number) => ({
  subtotal: grand_total - round_off, taxable: grand_total - round_off - tax, tax,
  total_discount: 0, grand_total, round_off,
});

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  mockedQuote.mockReset();
});

it("shows the server's rounded-up bill with a + Round off line that adds up", async () => {
  mockedQuote.mockResolvedValue(serverBill(1001, 0.5, 47.64));
  add(1000.5);
  render(<CartSidebar />);
  expect((await screen.findByTestId('cart-round-off')).textContent).toBe('+₹0.50');
  // 1,000.50 + 0.50 reads as the 1,001 total.
  expect(screen.getByTestId('cart-subtotal').textContent).toBe('₹1,000.50');
  expect(screen.getByTestId('cart-total').textContent).toBe('₹1,001');
  expect(screen.getByText('₹47.64')).toBeTruthy();
});

it("shows the server's rounded-down bill with a − Round off line that adds up", async () => {
  mockedQuote.mockResolvedValue(serverBill(1500, -0.49, 123.97));
  add(999.99);
  add(500.5, 'SUNGLASS');
  render(<CartSidebar />);
  expect((await screen.findByTestId('cart-round-off')).textContent).toBe('−₹0.49');
  expect(screen.getByTestId('cart-subtotal').textContent).toBe('₹1,500.49');
  expect(screen.getByTestId('cart-total').textContent).toBe('₹1,500');
});

it('shows no Round off line on a whole-rupee bill', async () => {
  mockedQuote.mockResolvedValue(serverBill(1000, 0, 47.62));
  add(1000);
  render(<CartSidebar />);
  await waitFor(() => expect(mockedQuote).toHaveBeenCalled());
  expect(screen.queryByText('Round off')).toBeNull();
});

it('never rounds the bill itself while the quote is on its way', async () => {
  mockedQuote.mockReturnValue(new Promise(() => undefined));
  add(1000.49);
  render(<CartSidebar />);
  await waitFor(() => expect(mockedQuote).toHaveBeenCalled());
  expect(screen.queryByText('Round off')).toBeNull();
  expect(usePOSStore.getState().getGrandTotal()).toBe(1000.49);
});
