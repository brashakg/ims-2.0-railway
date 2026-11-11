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
const giveBack = vi.fn(async (..._a: unknown[]) => ({ message: 'Given back' }));
const completeRecord = vi.fn(async (): Promise<any> => ({ message: 'Done' }));
const retryStockStep = vi.fn(async (..._a: unknown[]) => ({ message: 'Restock applied' }));
vi.mock('../../../services/api/returns', () => ({
  returnsApi: {
    needsCompleting: () => needsCompleting(),
    completeRecord: (...a: unknown[]) => (completeRecord as any)(...a),
    owedPaid: (...a: unknown[]) => owedPaid(...a),
    giveBackUnbilled: (...a: unknown[]) => giveBack(...a),
    retryStockStep: (...a: unknown[]) => retryStockStep(...a),
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


describe('NeedsCompletingPanel - Unbilled payments (money review 20)', () => {
  it('gives back an unbilled till payment through the one door, after a confirm step', async () => {
    needsCompleting.mockResolvedValue({ items: [], owed: [], unbilled: [
      { key: 'POSPAY:O1:k:leg:0', kind: 'STORE_CREDIT', amount: 100, order_id: 'O1', order_number: 'BV/0101',
        store_id: 'BV-PUN-01', customer_id: 'C1' },
    ] });
    giveBack.mockClear();
    render(<NeedsCompletingPanel />);
    expect(await screen.findByText('BV/0101')).toBeTruthy(); // the human order number
    fireEvent.click(screen.getByRole('button', { name: 'Give back this unbilled payment' }));
    expect(giveBack).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Confirm: give back Rs 100.00/ }));
    await waitFor(() => expect(giveBack).toHaveBeenCalledTimes(1));
    expect(giveBack.mock.calls[0]).toEqual(['POSPAY:O1:k:leg:0']);
  });
});

describe('money review 22: a dead voucher on a walk-in bill is paid back in cash', () => {
  it('asks where the cash comes from before it can be confirmed, and sends it', async () => {
    needsCompleting.mockResolvedValue({ items: [], owed: [], unbilled: [
      { key: 'POSPAY:O7:k7:leg:0', kind: 'GIFT_VOUCHER', amount: 80, order_id: 'O7', order_number: 'BV/0707',
        store_id: 'BV-PUN-01', voucher_code: 'GV7', cash_payout: true },
    ] });
    giveBack.mockClear();
    render(<NeedsCompletingPanel />);
    expect(await screen.findByText('BV/0707')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Give back this unbilled payment' })).toBeNull();
    fireEvent.change(screen.getByLabelText('Cash from'), { target: { value: 'HQ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Give back this unbilled payment' }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm: Rs 80.00 paid back in cash/ }));
    await waitFor(() => expect(giveBack).toHaveBeenCalledTimes(1));
    expect(giveBack.mock.calls[0]).toEqual(['POSPAY:O7:k7:leg:0', 'HQ']);
  });
});


describe('NeedsCompletingPanel - Stock still to put back (L2-RETURN-F03)', () => {
  const OPEN = { return_id: 'RET-S', order_id: 'ORD-7', order_number: 'BV/0007', store_id: 'BV-PUN-01',
    pieces: '1 x RB-123, 2 x OAK-55' };

  it('keeps the completion message on screen until it is dismissed', async () => {
    needsCompleting.mockResolvedValue({ items: [ITEMS[2]] });
    const message = 'Return record completed. NOT back in stock: 1 x RB-123, 2 x OAK-55. Do not add these by hand';
    completeRecord.mockResolvedValueOnce({ message, stock_step_open: true });
    render(<NeedsCompletingPanel standalone />);
    fireEvent.click(await screen.findByRole('button', { name: 'Complete the record' }));
    fireEvent.click(screen.getByRole('button', { name: /Confirm: Complete the record/ }));
    // Duration 0: the toast stays (ToastContext auto-dismisses only when > 0).
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(message, 0));
  });

  it('names the pieces still out and puts them back through Retry stock step, never by hand', async () => {
    needsCompleting.mockReset();
    needsCompleting.mockResolvedValueOnce({ items: [], stock_open: [OPEN] }).mockResolvedValue({ items: [] });
    retryStockStep.mockClear();
    render(<NeedsCompletingPanel />); // shown at the desk too, though nothing else needs completing
    expect(await screen.findByText('Not back in stock: 1 x RB-123, 2 x OAK-55')).toBeTruthy();
    expect(screen.getByLabelText('Stock still to put back').textContent).toMatch(/Do not add them by hand/);
    fireEvent.click(screen.getByRole('button', { name: 'Retry stock step' }));
    await waitFor(() => expect(retryStockStep).toHaveBeenCalledTimes(1));
    expect(retryStockStep.mock.calls[0]).toEqual(['RET-S']);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Restock applied', 0));
    // Reloaded: the piece is back, the row is gone.
    await waitFor(() => expect(screen.queryByText(/Not back in stock/)).toBeNull());
  });
});
