// ============================================================================
// IMS 2.0 - Goods back: the goods leg of a Shopify refund
// ============================================================================
// Owner ruling 2026-09-28 keeps a DELIVERED order DELIVERED when Shopify
// refunds it, so the confirm restocks nothing for it (the customer has the
// goods). When the goods physically come back, a person presses Goods back on
// the refund row -- before or after the confirm, once. The counter return door
// refuses such an order (it would refund the money a second time), so this
// button is the only way the unit gets back on a shelf.
//
// Drives the REAL page with the api module mocked. BOTH DIRECTIONS: the button
// is on an open row and a posted row, and absent on a rejected row, an
// unmatched row and a row whose goods are already back.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../../services/api/onlineStore', () => ({
  refundReviewsApi: {
    list: vi.fn(),
    confirm: vi.fn(),
    reject: vi.fn(),
    goodsBack: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

import RefundReviewsPage from '../RefundReviewsPage';
import { refundReviewsApi } from '../../../services/api/onlineStore';

const row = (review_id: string, status: string, extra: Record<string, unknown> = {}) => ({
  review_id,
  status,
  order_id: `ord-${review_id}`,
  order_number: `BV-${review_id}`,
  gross_refund: 999,
  ...extra,
});

describe('Goods back on a Shopify refund row', () => {
  beforeEach(() => {
    vi.mocked(refundReviewsApi.list).mockResolvedValue({
      reviews: [
        row('open', 'PENDING'),
        row('posted', 'POSTED', { resolved: true }),
        row('rejected', 'REJECTED', { resolved: true }),
        row('unmatched', 'UNMATCHED', { order_id: null }),
        row('done', 'POSTED', { resolved: true, goods_back_at: '2026-09-29T10:00:00Z' }),
      ],
      total: 5,
      available: true,
      reason: null,
      scope: 'all-stores',
    });
  });

  it('offers Goods back only where the goods can still come back, and sends it', async () => {
    const user = userEvent.setup();
    render(<RefundReviewsPage />);
    await screen.findByText('BV-open');
    await user.click(screen.getByRole('button', { name: 'All' }));
    await screen.findByText('BV-rejected');

    const buttons = screen.getAllByRole('button', { name: /Goods back/ });
    expect(buttons).toHaveLength(2);

    await user.click(buttons[1]);
    await waitFor(() => expect(refundReviewsApi.goodsBack).toHaveBeenCalledWith('posted'));
  });
});
