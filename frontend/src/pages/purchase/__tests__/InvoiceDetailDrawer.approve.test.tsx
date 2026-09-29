// The detail drawer approves a match exception through the one ApproveModal
// (reason >= 10 chars), posting to the row's id. Before, the drawer carried
// its own approve with a 1-character floor.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/vendorAp', () => ({
  purchaseInvoicesApi: {
    getMatch: vi.fn().mockResolvedValue(null),
    approveException: vi.fn().mockResolvedValue({
      match_status: 'MATCHED_OVERRIDE',
      exception_override: { approved_by: 'acc-1', reason: 'Vendor agreed short-ship' },
    }),
  },
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { roles: ['ACCOUNTANT'] }, hasRole: () => true }),
}));

import { InvoiceDetailDrawer } from '../invoices/InvoiceDetailDrawer';
import { purchaseInvoicesApi, type PurchaseInvoice } from '../../../services/api/vendorAp';

const ON_HOLD = {
  purchase_invoice_id: 'b-1',
  vendor_id: 'v-1',
  vendor_name: 'Luxottica India',
  vendor_invoice_no: 'INV-1',
  vendor_invoice_date: '2026-07-01',
  lines: [],
  total_amount: 1050,
  match_status: 'ON_HOLD_EXCEPTION',
} as unknown as PurchaseInvoice;

describe('InvoiceDetailDrawer approve', () => {
  it('opens ApproveModal, holds the 10-char rule, posts to the row id', async () => {
    const onChanged = vi.fn();
    render(<InvoiceDetailDrawer invoice={ON_HOLD} config={null} onClose={vi.fn()} onChanged={onChanged} />);

    fireEvent.click(screen.getByRole('button', { name: /Approve exception/ }));
    const modal = screen.getByText('Approve match exception').closest('div.bg-white') as HTMLElement;
    const confirm = within(modal).getByRole('button', { name: /Approve exception/ });
    const box = within(modal).getByRole('textbox');

    fireEvent.change(box, { target: { value: 'ok' } });
    expect(confirm).toBeDisabled();

    fireEvent.change(box, { target: { value: 'Vendor agreed short-ship' } });
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(purchaseInvoicesApi.approveException).toHaveBeenCalledWith('b-1', { reason: 'Vendor agreed short-ship' }),
    );
    await waitFor(() =>
      expect(onChanged).toHaveBeenCalledWith(
        expect.objectContaining({ purchase_invoice_id: 'b-1', match_status: 'MATCHED_OVERRIDE' }),
      ),
    );
    expect(screen.queryByText('Approve match exception')).toBeNull();
  });
});
