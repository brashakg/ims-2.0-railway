// PR #1264 money review 13: Returns > Needs completing -- Accounts / Admin
// finish an unfinished counter return by answering ONE question; Accounts
// see the panel only (no return desk).
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const toast = { success: vi.fn(), error: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
let roles = ['ACCOUNTANT'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { user_id: 'u1', name: 'Acc', roles, activeStoreId: 'BV-PUN-01' } }),
}));
const ITEMS = [
  { return_id: 'RET-A', order_id: 'ORD-1', order_number: 'BV/0001', store_id: 'BV-PUN-01', amount: 1800,
    credit_line: { type: 'ISSUED', amount: 1200, note_number: 'TST01/C2627/0007' }, cash_leg: 600,
    record_not_saved: false, money_known: true, at: '2026-10-30T10:00:00+00:00' },
  { return_id: 'RET-B', order_id: 'ORD-2', order_number: 'BV/0002', store_id: 'BV-PUN-01', amount: 900,
    credit_line: null, cash_leg: 0, record_not_saved: false, money_known: false },
  { return_id: 'RET-C', order_id: 'ORD-3', order_number: 'BV/0003', store_id: 'BV-PUN-01', amount: 500,
    credit_line: { type: 'ISSUED', amount: 500, note_number: 'TST01/C2627/0008' }, cash_leg: 0,
    record_not_saved: false, money_known: true },
];
const needsCompleting = vi.fn(async (): Promise<any> => ({ items: ITEMS }));
const owedPaid = vi.fn(async (..._a: unknown[]) => ({ message: 'Booked' }));
const completeRecord = vi.fn(async () => ({ message: 'Done' }));
vi.mock('../../../services/api/returns', () => ({
  returnsApi: {
    needsCompleting: () => needsCompleting(),
    completeRecord: (...a: unknown[]) => (completeRecord as any)(...a),
    owedPaid: (...a: unknown[]) => owedPaid(...a),
    quote: vi.fn(), create: vi.fn(), list: vi.fn(async () => ({ returns: [] })),
  },
}));
vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn(async () => ({ orders: [] })) },
  productApi: { searchProducts: vi.fn(async () => []) },
  inventoryApi: { getStockByBarcode: vi.fn() },
}));

import { NeedsCompletingPanel } from '../NeedsCompletingPanel';
import ReturnsPage from '../../../pages/orders/ReturnsPage';

describe('Needs completing panel', () => {
  beforeEach(() => { vi.clearAllMocks(); roles = ['ACCOUNTANT']; });

  it('lists each unfinished return and sends the one answer the person gives', async () => {
    render(<NeedsCompletingPanel standalone />);
    expect(await screen.findByText('BV/0001')).toBeTruthy();
    expect(screen.getByText(/Store credit given Rs 1,200.00 - CN TST01\/C2627\/0007/)).toBeTruthy();
    expect(screen.getByText('No store credit written')).toBeTruthy();
    // Money moved on RET-A: Yes / No only. Nothing moved on RET-B: Nothing was paid only.
    const rowA = screen.getByText('BV/0001').closest('tr')!;
    const rowB = screen.getByText('BV/0002').closest('tr')!;
    expect(rowA.textContent).not.toMatch(/Nothing was paid/);
    expect(rowB.textContent).not.toMatch(/the cash part was paid/);
    fireEvent.click(screen.getByRole('button', { name: 'No - the cash part was NOT paid' }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm: No - the cash part was NOT paid/ }));
    await waitFor(() => expect(completeRecord).toHaveBeenCalledWith('RET-A', { cash_leg_paid: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Nothing was paid' }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm: Nothing was paid/ }));
    await waitFor(() => expect(completeRecord).toHaveBeenCalledWith('RET-B', { nothing_paid: true }));
    // No cash part: one answer, "Complete the record" (sent as cash paid).
    const rowC = screen.getByText('BV/0003').closest('tr')!;
    expect(rowC.textContent).not.toMatch(/the cash part was/);
    expect(rowC.textContent).not.toMatch(/Nothing was paid/);
    fireEvent.click(screen.getByRole('button', { name: 'Complete the record' }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm: Complete the record/ }));
    await waitFor(() => expect(completeRecord).toHaveBeenCalledWith('RET-C', { cash_leg_paid: true }));
    expect(needsCompleting).toHaveBeenCalledTimes(4); // reloaded after each answer
  });

  it('gives Accounts the panel only, never the return desk', async () => {
    render(<ReturnsPage />);
    expect(await screen.findByText('BV/0001')).toBeTruthy();
    expect(screen.queryByText('New return')).toBeNull();
    expect(screen.queryByText('Store Credit')).toBeNull();
  });

  it('shows a cashier the desk and no panel', async () => {
    roles = ['CASHIER'];
    render(<ReturnsPage />);
    expect(await screen.findByText('New return')).toBeTruthy();
    expect(needsCompleting).not.toHaveBeenCalled();
  });
});

describe('NeedsCompletingPanel - Owed to customer (money review 15)', () => {
  it('books a cash owed part as paid only once the cash source is said, after a confirm step', async () => {
    needsCompleting.mockResolvedValue({ items: [], owed: [
      { return_id: 'RET-O', order_id: 'ORD-9', order_number: 'BV/0009', store_id: 'BV-PUN-01', owed_amount: 600,
        owed_tenders: [{ method: 'CASH', amount: 600 }] },
    ] });
    owedPaid.mockClear();
    render(<NeedsCompletingPanel standalone />);
    const paidBy = await screen.findByLabelText('Paid by');
    fireEvent.change(paidBy, { target: { value: 'CASH' } });
    expect(screen.queryByRole('button', { name: /Cash owed - now paid/ })).toBeNull(); // no source yet
    fireEvent.change(screen.getByLabelText('Cash from'), { target: { value: 'HQ' } });
    fireEvent.click(screen.getByRole('button', { name: /Cash owed - now paid/ }));
    expect(owedPaid).not.toHaveBeenCalled(); // a confirm step first
    fireEvent.click(screen.getByRole('button', { name: /Confirm: Rs 600.00 paid today/ }));
    await waitFor(() => expect(owedPaid).toHaveBeenCalledTimes(1));
    expect(owedPaid.mock.calls[0]).toEqual(['RET-O', { method: 'CASH', cash_paid_from: 'HQ' }]);
  });

  it('a UPI payout needs no cash source', async () => {
    needsCompleting.mockResolvedValue({ items: [], owed: [
      { return_id: 'RET-U', order_id: 'ORD-8', store_id: 'BV-PUN-01', owed_amount: 300, owed_tenders: [] },
    ] });
    owedPaid.mockClear();
    render(<NeedsCompletingPanel />); // shown at the desk too, though nothing else needs completing
    fireEvent.change(await screen.findByLabelText('Paid by'), { target: { value: 'UPI' } });
    expect(screen.queryByLabelText('Cash from')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Cash owed - now paid/ }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm: Rs 300.00 paid today/ }));
    await waitFor(() => expect(owedPaid).toHaveBeenCalledTimes(1));
    expect(owedPaid.mock.calls[0]).toEqual(['RET-U', { method: 'UPI' }]);
  });
});
