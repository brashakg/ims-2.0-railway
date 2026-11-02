// ============================================================================
// IMS 2.0 - Website 1: an online order whose IMS total is not what Shopify
// charged wears a red chip on the Online Orders list (never silently accepted).
// ============================================================================

import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

const row = vi.hoisted(() => (sid: string, check: Record<string, unknown>) => ({
  order_id: `ord-${sid}`,
  order_number: `ONL-${sid}`,
  shopify_order_id: sid,
  customer_name: `Buyer ${sid}`,
  grand_total: 999,
  currency: 'INR',
  status: 'CONFIRMED',
  payment_status: 'PAID',
  map_status: 'MAPPED',
  items_count: 1,
  shopify_totals_check: check,
}));

vi.mock('../../../services/api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: {
        orders: [
          row('1', { status: 'MISMATCH', shopify_total: 950, ims_total: 999 }),
          row('2', { status: 'MATCH', shopify_total: 999, ims_total: 999 }),
        ],
        failed: [],
        total: 2,
      },
    }),
    post: vi.fn(),
  },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ hasRole: () => true }),
}));

import OnlineOrdersPage from '../OnlineOrdersPage';

describe('Online Orders list - Shopify total check', () => {
  it('flags only the order whose total differs from Shopify', async () => {
    render(
      <MemoryRouter>
        <OnlineOrdersPage />
      </MemoryRouter>,
    );
    await screen.findByText('Buyer 1');
    const chips = screen.getAllByText('TOTAL DIFFERS FROM SHOPIFY');
    expect(chips).toHaveLength(1);
    expect(chips[0].getAttribute('title')).toMatch(/Shopify charged/);
  });
});
