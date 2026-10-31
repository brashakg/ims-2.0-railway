// PR #1264 money review 11: an unclear superadmin credit note answers 503
// "could not be confirmed ... do NOT issue it again" -- the modal must show the
// SERVER's sentence (read through the shared helper, never err.response).
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { buildApiError } from '../../../services/api/client';

const toast = { success: vi.fn(), error: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
const change = vi.fn();
vi.mock('../../../services/api/sales', () => ({
  orderApi: {
    quoteBill: vi.fn(async () => ({ grand_total: 4500 })),
    superadminInvoiceChange: (...a: unknown[]) => change(...a),
    superadminEditOrder: vi.fn(),
  },
}));

import { SuperadminOrderEditModal } from '../SuperadminOrderEditModal';

const SENTENCE = 'The credit note could not be confirmed just now - the credit may already be on the customer\'s balance. Do NOT issue it again.';

describe('superadmin credit note that cannot be confirmed', () => {
  it("shows the server's do-not-issue-again sentence", async () => {
    change.mockRejectedValueOnce(buildApiError({
      message: 'Request failed with status code 503',
      response: { status: 503, data: { detail: SENTENCE } },
    } as Parameters<typeof buildApiError>[0]));
    const order: any = { id: 'ORD-1', invoiceNumber: 'BOK01/2627/00009', grandTotal: 5000,
      items: [{ id: 'li1', itemType: 'FRAME', productName: 'Frame', quantity: 1, unitPrice: 5000 }] };
    render(<SuperadminOrderEditModal order={order} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/Why is this order being changed/), { target: { value: 'price fix' } });
    const issue = await screen.findByRole('button', { name: /Issue credit \/ debit note/ });
    await waitFor(() => expect((issue as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(issue);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(SENTENCE));
  });
});
