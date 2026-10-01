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

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

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

  it('sends staff to Goods back, never to a manual add, when the confirm put nothing back', async () => {
    // A manual add plus a later Goods back on the same refund counts one frame twice.
    vi.mocked(refundReviewsApi.confirm).mockResolvedValue({
      review_id: 'open',
      status: 'POSTED',
      result: { status: 'credited', restock_applied: false, return_id: 'RET-9' },
    } as never);
    const user = userEvent.setup();
    render(<RefundReviewsPage />);
    await user.click(await screen.findByRole('button', { name: /Confirm/ }));

    await waitFor(() => expect(toast.warning).toHaveBeenCalled());
    const said = String(vi.mocked(toast.warning).mock.calls[0][0]);
    expect(said).toMatch(/press Goods back/);
    expect(said).not.toMatch(/receiving shop/);
    const banner = await screen.findByText(/IMS has no record of them/);
    expect(banner.textContent).toMatch(/press Goods\s+back/);
    expect(banner.textContent).not.toMatch(/Add them at the receiving shop/);
  });

  it('hands a historical order frame to stock-in, never says it was put back', async () => {
    // Owner 2026-10-01: an order imported from Shopify's history predates IMS
    // stock, so Goods back books the frame and a task sends it to stock-in.
    vi.mocked(refundReviewsApi.goodsBack).mockResolvedValueOnce({
      review_id: 'open',
      result: { status: 'stock_in', stock_in_store_id: 'BV-GANGA-01' },
    } as never);
    const user = userEvent.setup();
    render(<RefundReviewsPage />);
    await screen.findByText('BV-open');
    await user.click(screen.getAllByRole('button', { name: /Goods back/ })[0]);

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const said = String(vi.mocked(toast.success).mock.calls.at(-1)?.[0]);
    expect(said).toMatch(/store manager of BV-GANGA-01 to add the frame through stock-in/);
    expect(said).not.toMatch(/put back in stock/);
  });

  it('says the same after a confirm that booked a historical frame', async () => {
    vi.mocked(refundReviewsApi.confirm).mockResolvedValueOnce({
      review_id: 'open',
      status: 'POSTED',
      result: { status: 'credited', restock_applied: true, restock_stock_ids: [], stock_in_task: 'TSK-1' },
    } as never);
    const user = userEvent.setup();
    render(<RefundReviewsPage />);
    await user.click(await screen.findByRole('button', { name: /Confirm/ }));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    const said = String(vi.mocked(toast.success).mock.calls.at(-1)?.[0]);
    expect(said).toMatch(/add the frame through stock-in/);
    expect(said).not.toMatch(/No stock was put back/);
  });
});
