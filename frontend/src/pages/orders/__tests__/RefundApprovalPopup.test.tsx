// Money review of #1264 (round 10): the manager-approval popup opens on the
// REAL ApiError the shared client rejects with (#1274), asks for the server's
// amount, re-submits with the approval, and shows the server's reason when the
// resubmit fails.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { buildApiError } from '../../../services/api/client';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u-cashier', name: 'Cash', roles: ['CASHIER'], activeStoreId: 'BV-DHN-02' } }),
}));
const ORDERS = [{ id: 'ORD-1', orderNumber: 'BV/26-27/0001', customerName: 'Asha', customerPhone: '9800000001',
  items: [{ id: 'L1', productName: 'Frame', quantity: 1, unitPrice: 2500, taxable_value: 2119, tax_amount: 381, gst_rate: 18 }] }];
vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn(async () => ({ orders: ORDERS })) },
  productApi: { searchProducts: vi.fn(async () => []) },
  inventoryApi: { getStockByBarcode: vi.fn(async () => { throw new Error('nf'); }) },
}));
const create = vi.fn();
vi.mock('../../../services/api/returns', () => ({
  returnsApi: {
    create: (...a: unknown[]) => create(...a),
    quote: vi.fn(async () => ({ gross_refund: 2500, net_refund: 2500, restocking_fee: 0 })),
    list: vi.fn(async () => ({ returns: [] })),
  },
}));
const createRequest = vi.fn(async () => ({ request_id: 'REQ-1', required_tier: 'auto', expires_at: new Date(Date.now() + 600000).toISOString() }));
const getMyRequests = vi.fn(async () => ({ requests: [{ request_id: 'REQ-1', status: 'APPROVED', approval_token: 'TOK' }] }));
vi.mock('../../../services/api/approvals', () => ({
  approvalsApi: { createRequest: (...a: unknown[]) => (createRequest as any)(...a), getMyRequests: () => getMyRequests() },
}));

import ReturnsPage from '../ReturnsPage';

const axiosErr = (status: number, detail: unknown) => buildApiError({
  isAxiosError: true, message: `Request failed with status code ${status}`, config: {},
  response: { status, data: { detail } },
} as any);

const GATE = { reason: 'REFUND_APPROVAL_REQUIRED', required_tier: 'auto', this_refund: 2500, prior_refunds: 0,
  running_total: 2500, message: 'needs a manager' };

async function driveToSubmit() {
  render(<ReturnsPage />);
  fireEvent.click(screen.getByText('Store Credit').closest('button')!);
  fireEvent.change(screen.getByPlaceholderText(/Order number/), { target: { value: 'Asha' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search' }));
  fireEvent.click(await screen.findByText('BV/26-27/0001'));
  const qty = (await screen.findAllByRole('combobox'))[0];
  fireEvent.change(qty, { target: { value: '1' } });
  const review = await screen.findByRole('button', { name: /Review Credit Note/ });
  await waitFor(() => expect((review as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(review);
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
}

describe('Refund approval popup', () => {
  it('opens on the real 403 ApiError and asks for the server amount', async () => {
    create.mockRejectedValueOnce(axiosErr(403, GATE));
    create.mockRejectedValueOnce(axiosErr(503, 'The store credit could not be confirmed just now - check the balance before trying again.'));
    await driveToSubmit();
    expect(await screen.findByRole('dialog', { name: 'Refund approval required' })).toBeTruthy();
    await waitFor(() => expect(createRequest).toHaveBeenCalled());
    expect((createRequest.mock.calls[0] as any)[0].amount).toBe(2500);
    // After approval the resubmit fails: is the SERVER's reason shown?
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2), { timeout: 3000 });
    expect((create.mock.calls[1] as any)[0].refund_approval_token ?? (create.mock.calls[1] as any)[0].refund_approval_request_id).toBeTruthy();
    // The approval resubmit is the SAME press: the same Idempotency-Key.
    expect((create.mock.calls[0] as any)[1]).toBeTruthy();
    expect((create.mock.calls[1] as any)[1]).toBe((create.mock.calls[0] as any)[1]);
    await screen.findByText(/could not be (confirmed|finalised)/);
    expect(screen.queryByText(/could not be confirmed just now/)).toBeTruthy();
  });

  it('reopens the popup with the server figures when the approval is refused as invalid', async () => {
    create.mockReset();
    createRequest.mockClear();
    create.mockRejectedValueOnce(axiosErr(403, GATE));
    create.mockRejectedValueOnce(axiosErr(403, { reason: 'REFUND_APPROVAL_INVALID', required_tier: 'auto',
      this_refund: 2400, prior_refunds: 100, running_total: 2500, message: 'approval no longer matches' }));
    await driveToSubmit();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2), { timeout: 3000 });
    // The popup is up again, asking for the SERVER's figure (not the page's).
    await waitFor(() => expect(createRequest).toHaveBeenCalledTimes(2), { timeout: 3000 });
    expect((createRequest.mock.calls[1] as any)[0].amount).toBe(2400);
  });

  it('re-sends the same Idempotency-Key when a press after a lost answer goes again', async () => {
    create.mockReset();
    create.mockRejectedValueOnce(axiosErr(503, 'The store credit could not be confirmed just now - Do NOT redo this return.'));
    create.mockResolvedValueOnce({ return_id: 'RET-1', credit_amount: 2500 });
    await driveToSubmit();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    await screen.findByText(/could not be confirmed/);
    const review = screen.getByRole('button', { name: /Review Credit Note/ });
    await waitFor(() => expect((review as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(review);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    const k1 = (create.mock.calls[0] as any)[1];
    expect(k1).toBeTruthy();
    expect((create.mock.calls[1] as any)[1]).toBe(k1);
  });

  it('makes a new press key after a success, and after any change of the return', async () => {
    create.mockReset();
    create.mockResolvedValueOnce({ return_id: 'RET-1', credit_amount: 2500 });
    create.mockRejectedValueOnce(axiosErr(503, 'The store credit could not be confirmed just now - Do NOT redo this return.'));
    create.mockResolvedValue({ return_id: 'RET-3', credit_amount: 2400 });
    await driveToSubmit();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    // A success: the next return is a new press.
    fireEvent.click(await screen.findByRole('button', { name: 'New Return' }));
    fireEvent.change(screen.getByPlaceholderText(/Order number/), { target: { value: 'Asha' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    fireEvent.click(await screen.findByText('BV/26-27/0001'));
    fireEvent.change((await screen.findAllByRole('combobox'))[0], { target: { value: '1' } });
    let review = await screen.findByRole('button', { name: /Review Credit Note/ });
    await waitFor(() => expect((review as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(review);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    const [k1, k2] = [(create.mock.calls[0] as any)[1], (create.mock.calls[1] as any)[1]];
    expect(k1).toBeTruthy();
    expect(k2).toBeTruthy();
    expect(k2).not.toBe(k1);
    // A lost answer, then the return changes (a restocking fee): a new press key.
    await screen.findByText(/could not be confirmed/);
    const fee = screen.getAllByRole('spinbutton').find((el) => (el as HTMLInputElement).max !== '') as HTMLInputElement;
    fireEvent.change(fee, { target: { value: '100' } });
    review = await screen.findByRole('button', { name: /Review Credit Note/ });
    await waitFor(() => expect((review as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(review);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(3));
    expect((create.mock.calls[2] as any)[1]).toBeTruthy();
    expect((create.mock.calls[2] as any)[1]).not.toBe(k2);
  });
});
