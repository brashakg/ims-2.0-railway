// ============================================================================
// IMS 2.0 - a journal whose approval can no longer be used is approved again
// ============================================================================
// Review PR-1411 round 1 / decision D54: an APPROVED journal whose approval
// lapsed before Post (or was spent elsewhere) could only show "Post to
// ledger", and Post answered 410 expired for good. The server now marks it
// `needs_reapproval`; a checker who is not the maker gets the PIN box and
// "Approve again" instead of a Post button that cannot work. The maker is told
// a different checker must approve it. A valid approval still shows Post.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const auth = { user: { id: 'u-admin', roles: ['ADMIN'] } };
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../services/api/finance', () => ({
  financeApi: {
    listJournalEntries: vi.fn(),
    getChartOfAccounts: vi.fn().mockResolvedValue({ accounts: [] }),
    approveJournalEntry: vi.fn().mockResolvedValue({ ok: true }),
    postJournalEntry: vi.fn(),
    getJournalEntry: vi.fn(),
  },
}));

import JournalEntriesPanel from '../JournalEntriesPanel';
import { financeApi } from '../../../services/api/finance';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const entry = (over: Record<string, unknown> = {}) => ({
  je_id: 'JE-1', je_number: 'JE/GEN/2026-27/000001', description: 'Depreciation for May',
  lines: [], total_debit: 10000, total_credit: 10000, status: 'APPROVED',
  maker_id: 'u-maker', ...over,
});

async function open(over: Record<string, unknown>) {
  api.listJournalEntries.mockResolvedValue({ journal_entries: [entry(over)], total: 1 });
  render(<JournalEntriesPanel />);
  fireEvent.click(await screen.findByText('Depreciation for May'));
}

describe('journal re-approval (D54)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { id: 'u-admin', roles: ['ADMIN'] };
  });

  it('a lapsed approval shows Approve again with the PIN, not Post', async () => {
    await open({ needs_reapproval: true });
    expect(screen.queryByRole('button', { name: 'Post to ledger' })).toBeNull();
    const pin = screen.getByLabelText('Approval PIN');
    fireEvent.change(pin, { target: { value: '4821' } });
    fireEvent.click(screen.getByRole('button', { name: 'Approve again' }));
    await waitFor(() => expect(api.approveJournalEntry).toHaveBeenCalledWith('JE-1', '4821'));
  });

  it('the maker is told a different checker must approve it', async () => {
    auth.user = { id: 'u-maker', roles: ['ADMIN'] };
    await open({ needs_reapproval: true });
    expect(screen.getByText(/a different checker must approve it/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve again' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Post to ledger' })).toBeNull();
  });

  it('a usable approval still shows Post', async () => {
    await open({ needs_reapproval: false });
    expect(screen.getByRole('button', { name: 'Post to ledger' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve again' })).toBeNull();
  });

  it('a Post refused as expired switches to Approve again', async () => {
    api.postJournalEntry.mockRejectedValue({ response: { status: 410, data: { detail: { error: 'expired' } } } });
    api.getJournalEntry.mockResolvedValue(entry({ needs_reapproval: true }));
    await open({});
    fireEvent.click(screen.getByRole('button', { name: 'Post to ledger' }));
    expect(await screen.findByRole('button', { name: 'Approve again' })).toBeTruthy();
  });

  // Lost journal write after the approval was spent by this Post (D54): the
  // server answers 409 not_approved but its own row still says the approval is
  // usable (bound to this Post). The screen must follow the server and keep
  // "Post to ledger", because pressing Post again finishes the posting once;
  // "Approve again" would only ever be refused.
  it('a Post refused as not_approved follows the server and keeps Post', async () => {
    api.postJournalEntry.mockRejectedValueOnce({ response: { status: 409, data: { detail: { error: 'not_approved', status: 'APPROVED' } } } });
    api.getJournalEntry.mockResolvedValue(entry({ needs_reapproval: false }));
    await open({});
    fireEvent.click(screen.getByRole('button', { name: 'Post to ledger' }));
    await waitFor(() => expect(api.getJournalEntry).toHaveBeenCalledWith('JE-1'));
    expect(screen.queryByRole('button', { name: 'Approve again' })).toBeNull();
    api.postJournalEntry.mockResolvedValueOnce({ ok: true });
    fireEvent.click(await screen.findByRole('button', { name: 'Post to ledger' }));
    await waitFor(() => expect(api.postJournalEntry).toHaveBeenCalledTimes(2));
  });
});
