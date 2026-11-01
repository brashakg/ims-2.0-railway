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
];
const needsCompleting = vi.fn(async () => ({ items: ITEMS }));
const completeRecord = vi.fn(async () => ({ message: 'Done' }));
vi.mock('../../../services/api/returns', () => ({
  returnsApi: {
    needsCompleting: () => needsCompleting(),
    completeRecord: (...a: unknown[]) => (completeRecord as any)(...a),
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
    expect(needsCompleting).toHaveBeenCalledTimes(3); // reloaded after each answer
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
