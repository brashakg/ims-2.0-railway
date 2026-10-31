// Money review 11 of #1264: one Idempotency-Key per open form -- a re-press
// after an unclear answer resumes the same credit line; a new form, a new key.
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: { roles: ['ADMIN'] } }) }));
const toast = { success: vi.fn(), error: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
const issue = vi.fn();
vi.mock('../../../services/api/customers', () => ({
  customerApi: {
    getStoreCreditLedger: vi.fn(async () => ({ balance: 0, entries: [] })),
    issueStoreCredit: (...a: unknown[]) => issue(...a),
    redeemStoreCredit: vi.fn(),
  },
}));

import { StoreCreditLedgerCard } from '../StoreCreditLedgerCard';

describe('StoreCreditLedgerCard idempotency key', () => {
  it('re-sends the same key on a re-press and a new key for a new form', async () => {
    issue.mockRejectedValueOnce(new Error('could not be confirmed')).mockResolvedValue({});
    render(<StoreCreditLedgerCard customerId="CUST-1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Issue/ }));
    fireEvent.change(screen.getByPlaceholderText('Amount'), { target: { value: '250' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(issue).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((screen.getByRole('button', { name: /Confirm|Saving/ }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(issue).toHaveBeenCalledTimes(2));
    const k1 = issue.mock.calls[0][4];
    expect(k1).toBeTruthy();
    expect(issue.mock.calls[1][4]).toBe(k1);
    // The form closed on success: a new form gets a new key.
    fireEvent.click(await screen.findByRole('button', { name: /Issue/ }));
    fireEvent.change(screen.getByPlaceholderText('Amount'), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(issue).toHaveBeenCalledTimes(3));
    expect(issue.mock.calls[2][4]).toBeTruthy();
    expect(issue.mock.calls[2][4]).not.toBe(k1);
  });
});
