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

describe('StoreCreditLedgerCard money review 12', () => {
  it('locks the amount once a key was sent, and redeems with its own form key', async () => {
    issue.mockReset();
    issue.mockRejectedValueOnce(new Error('could not be confirmed')).mockResolvedValue({});
    const { unmount } = render(<StoreCreditLedgerCard customerId="CUST-1" />);
    fireEvent.click(await screen.findByRole('button', { name: /Issue/ }));
    const amount = screen.getByPlaceholderText('Amount') as HTMLInputElement;
    fireEvent.change(amount, { target: { value: '250' } });
    expect(amount.readOnly).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(issue).toHaveBeenCalledTimes(1));
    // The lost answer leaves the form open, its amount locked to what was sent.
    expect((screen.getByPlaceholderText('Amount') as HTMLInputElement).readOnly).toBe(true);
    expect((screen.getByPlaceholderText(/Reason/) as HTMLInputElement).readOnly).toBe(true);
    unmount();
  });

  it('sends the per-form key on a redeem', async () => {
    const { customerApi } = await import('../../../services/api/customers');
    const redeem = vi.spyOn(customerApi, 'redeemStoreCredit').mockResolvedValue({} as any);
    vi.spyOn(customerApi, 'getStoreCreditLedger').mockResolvedValue({ balance: 500, entries: [] } as any);
    render(<StoreCreditLedgerCard customerId="CUST-1" />);
    const btn = await screen.findByRole('button', { name: /Redeem/ });
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(btn);
    fireEvent.change(screen.getByPlaceholderText('Amount'), { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(redeem).toHaveBeenCalledTimes(1));
    expect(redeem.mock.calls[0][4]).toBeTruthy();
  });
});
