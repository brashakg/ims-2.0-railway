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
    reverseJournalEntry: vi.fn(),
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

  // Codex round 3 (R3-A): a Post the server could not read its way through
  // now answers 503 "try again" (it used to answer 404 not found). Nothing
  // was posted or spent, so the drawer keeps the entry and "Post to ledger"
  // -- also when the re-read fails the same way -- and the next press posts.
  it('a Post answered 503 keeps the entry open with Post, and the next press posts', async () => {
    const unreadable = { response: { status: 503, data: { detail: 'Journal entries could not be read just now - please try again' } } };
    api.postJournalEntry.mockRejectedValueOnce(unreadable);
    api.getJournalEntry.mockRejectedValueOnce(unreadable);
    await open({ needs_reapproval: false });
    fireEvent.click(screen.getByRole('button', { name: 'Post to ledger' }));
    await waitFor(() => expect(api.getJournalEntry).toHaveBeenCalledWith('JE-1'));
    expect(screen.queryByRole('button', { name: 'Approve again' })).toBeNull();
    api.postJournalEntry.mockResolvedValueOnce({ ok: true });
    fireEvent.click(await screen.findByRole('button', { name: 'Post to ledger' }));
    await waitFor(() => expect(api.postJournalEntry).toHaveBeenCalledTimes(2));
  });
});

// PR-1411 round 4: a journal left REVERSED whose reversal voucher was never
// written (the voucher's write failed, or the server stopped between the two
// writes) showed no button at all, and the books carried it in full for good.
// The server marks it `reversal_unfinished`; the checker gets "Finish the
// reversal", which is the same Reverse call (the server writes the one voucher
// the entry already names).
describe('a reversal left without its voucher (round 4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { id: 'u-admin', roles: ['ADMIN'] };
  });

  it('offers Finish the reversal and calls Reverse', async () => {
    api.reverseJournalEntry.mockResolvedValue({ ok: true });
    await open({ status: 'REVERSED', reversed_by: 'JE-9', reversal_unfinished: true });
    expect(screen.getByText(/its reversal voucher was not saved/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Finish the reversal' }));
    await waitFor(() => expect(api.reverseJournalEntry).toHaveBeenCalledWith('JE-1'));
  });

  it('a finished reversal shows no reverse button', async () => {
    await open({ status: 'REVERSED', reversed_by: 'JE-9', reversal_unfinished: false });
    expect(screen.queryByRole('button', { name: 'Finish the reversal' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reverse this entry' })).toBeNull();
  });

  it('a Reverse that failed follows the server: the entry now offers Finish the reversal', async () => {
    api.reverseJournalEntry.mockRejectedValueOnce({ response: { status: 503, data: { detail: { error: 'write_failed' } } } });
    api.getJournalEntry.mockResolvedValue(entry({ status: 'REVERSED', reversed_by: 'JE-9', reversal_unfinished: true }));
    await open({ status: 'POSTED' });
    fireEvent.click(screen.getByRole('button', { name: 'Reverse this entry' }));
    api.reverseJournalEntry.mockResolvedValueOnce({ ok: true });
    fireEvent.click(await screen.findByRole('button', { name: 'Finish the reversal' }));
    await waitFor(() => expect(api.reverseJournalEntry).toHaveBeenCalledTimes(2));
  });

  // D113 (1): the mark was only inside the entry's drawer; nobody opening the
  // list could tell which entry the books still carried.
  it('the list row itself says Reversal not finished', async () => {
    api.listJournalEntries.mockResolvedValue({
      journal_entries: [
        entry({ status: 'REVERSED', reversed_by: 'JE-9', reversal_unfinished: true }),
        entry({ je_id: 'JE-2', je_number: 'JE/GEN/2026-27/000002', description: 'Rent for May',
          status: 'REVERSED', reversed_by: 'JE-8', reversal_unfinished: false }),
      ],
      total: 2,
    });
    render(<JournalEntriesPanel />);
    const unfinished = (await screen.findByText('Depreciation for May')).closest('tr') as HTMLElement;
    expect(unfinished.textContent).toContain('Reversal not finished');
    const finished = screen.getByText('Rent for May').closest('tr') as HTMLElement;
    expect(finished.textContent).not.toContain('Reversal not finished');
  });

  it('an accountant is told an admin must finish it', async () => {
    auth.user = { id: 'u-acc', roles: ['ACCOUNTANT'] };
    await open({ status: 'REVERSED', reversed_by: 'JE-9', reversal_unfinished: true });
    expect(screen.getByText(/An admin must finish the reversal/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Finish the reversal' })).toBeNull();
  });
});

describe('a journal list that could not be read (D84)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('says so instead of "No journal entries."', async () => {
    const detail = 'Journal entries could not be read just now - please try again';
    api.listJournalEntries.mockRejectedValue({ response: { status: 503, data: { detail } } });
    render(<JournalEntriesPanel />);
    expect(await screen.findByText(detail)).toBeTruthy();
    expect(screen.queryByText('No journal entries.')).toBeNull();
  });
});
