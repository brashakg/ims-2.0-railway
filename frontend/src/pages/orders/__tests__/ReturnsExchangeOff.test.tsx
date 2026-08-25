// ============================================================================
// The Exchange button is OFF, and the screen says what to do instead
// ============================================================================
// An exchange put the returned frame back on the shelf and took the price
// difference at the till, and never took the REPLACEMENT out of stock and never
// billed it -- a phantom frame plus a sale in no revenue figure, no GST return
// and no Tally export, on every exchange. Owner ruling 2026-08-25: switch it off
// first; staff do a return, then a fresh sale.
//
// The server refuses it as well, so this file is about the HALF THE OWNER ASKED
// FOR BY NAME: "not an error code, not a disabled button with no explanation".
// A cashier who is only stopped at the END of the wizard has already told the
// customer the swap is happening. So the tile must be visibly off AND carry the
// two steps in words, and clicking it must not quietly put the screen into
// exchange mode.
//
// This drives the REAL page. A test over the options constant alone would pass
// while the button carried on rendering enabled.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-cashier', roles: ['CASHIER'], activeStoreId: 'ZZ-SOLO' },
  }),
}));

vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn().mockResolvedValue([]) },
  productApi: { searchProducts: vi.fn().mockResolvedValue([]) },
}));

vi.mock('../../../services/api/returns', () => ({
  returnsApi: {
    create: vi.fn(),
    quote: vi.fn(),
    list: vi.fn().mockResolvedValue({ returns: [] }),
  },
}));

import ReturnsPage from '../ReturnsPage';

// By the tile's LABEL, not its accessible name: the accessible name folds in
// the description, and the Exchange description now quotes "Return & Refund".
const tile = (label: string): HTMLButtonElement => {
  const el = screen.getByText(label).closest('button');
  if (!el) throw new Error(`no tile button for ${label}`);
  return el as HTMLButtonElement;
};

describe('Returns screen: the exchange door is closed', () => {
  it('renders the Exchange choice as switched off', () => {
    render(<ReturnsPage />);
    expect(tile('Exchange')).toBeDisabled();
  });

  it('tells staff the two steps to take instead, in words', () => {
    render(<ReturnsPage />);
    const text = tile('Exchange').textContent || '';
    // Both halves, in order: take it back as a return, THEN sell the new one.
    expect(text).toMatch(/return\s*&?\s*refund/i);
    expect(text).toMatch(/normal sale/i);
  });

  it('does not switch the screen into exchange mode when clicked', async () => {
    render(<ReturnsPage />);
    const exchange = tile('Exchange');
    await userEvent.click(exchange);
    // The selected tile is the only one painted with the BV-red accent.
    expect(exchange.className).not.toMatch(/bv-red/);
    expect(tile('Return & Refund').className).toMatch(/bv-red/);
  });

  it('keeps the other two paths available', () => {
    render(<ReturnsPage />);
    expect(tile('Return & Refund')).toBeEnabled();
    expect(tile('Store Credit')).toBeEnabled();
  });
});
