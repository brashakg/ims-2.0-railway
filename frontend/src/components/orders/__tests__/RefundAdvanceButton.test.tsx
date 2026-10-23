// 'Refund advance and cancel' (owner 2026-10-23): accounts / admin only; the
// confirm dialog shows the server's amount + method; disabled with the
// server's reason when the door would refuse.
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../services/api/returns', () => ({
  returnsApi: { advanceRefundPreview: vi.fn(), refundAdvanceAndCancel: vi.fn() },
}));
const toast = { success: vi.fn(), error: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

import { returnsApi } from '../../../services/api/returns';
import { RefundAdvanceButton, canRefundAdvance } from '../RefundAdvanceButton';

const preview = vi.mocked(returnsApi.advanceRefundPreview);
const post = vi.mocked(returnsApi.refundAdvanceAndCancel);

describe('RefundAdvanceButton', () => {
  beforeEach(() => vi.clearAllMocks());

  it('is offered to accounts, admin and superadmin only', () => {
    expect(canRefundAdvance({ roles: ['ACCOUNTANT'] })).toBe(true);
    expect(canRefundAdvance({ roles: ['ADMIN'] })).toBe(true);
    expect(canRefundAdvance({ roles: ['SUPERADMIN'] })).toBe(true);
    for (const r of ['STORE_MANAGER', 'CASHIER', 'SALES_STAFF', 'AREA_MANAGER', 'OPTOMETRIST']) {
      expect(canRefundAdvance({ roles: [r] })).toBe(false);
    }
  });

  it('is disabled with the server reason when the door would refuse', async () => {
    preview.mockResolvedValue({ ok: false, reason: 'No money was taken on this order - cancel it normally.' });
    render(<RefundAdvanceButton orderId="ORD-2" onDone={() => {}} />);
    await screen.findByText(/No money was taken/);
    const btn = screen.getByRole('button', { name: /Refund advance and cancel/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('confirms the amount and method, then refunds and cancels', async () => {
    preview.mockResolvedValue({
      ok: true,
      total: 1000,
      refund_tenders: [{ method: 'CASH', amount: 600 }, { method: 'STORE_CREDIT', amount: 400 }],
      invoice_number: 'BOK01/2627/00009',
    });
    post.mockResolvedValue({ message: 'Refund of Rs 1,000.00 recorded and the order cancelled.' });
    const onDone = vi.fn();
    render(<RefundAdvanceButton orderId="ORD-1" onDone={onDone} />);
    const btn = await screen.findByRole('button', { name: /Refund advance and cancel/ });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(btn);

    expect(screen.getByText('Cash')).toBeTruthy();
    expect(screen.getByText('Store credit')).toBeTruthy();
    expect(screen.getByText(/whole invoice BOK01\/2627\/00009/)).toBeTruthy();
    const confirm = screen.getByRole('button', { name: /Refund ₹1,000.00 and cancel/ }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true); // a reason is required
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'customer cancelled the order' } });
    fireEvent.click(confirm);
    await waitFor(() => expect(post).toHaveBeenCalledWith('ORD-1', 'customer cancelled the order'));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalled();
  });
});
