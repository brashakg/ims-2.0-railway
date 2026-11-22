// ============================================================================
// IMS 2.0 - Rejecting a journal in the Approvals inbox needs the journal note
// ============================================================================
// Audit L3-25: a journal's request decided in the Approvals inbox now moves the
// journal, so a rejection there is held to the journal screen's rule -- a
// checker note of at least 10 characters. The server refuses without one
// (400 note_required); the modal must say why and stay open so the approver
// can type the note, not close on a generic "could not complete" toast.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-admin', roles: ['ADMIN'] }, hasRole: () => true }),
}));
const toastError = vi.hoisted(() => vi.fn());
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: toastError, warning: vi.fn(), info: vi.fn() }),
}));
const api = vi.hoisted(() => ({ approve: vi.fn(), reject: vi.fn() }));
vi.mock('../../../services/api/approvals', () => ({ approvalsApi: api }));

import type { ApprovalRequest } from '../../../services/api/approvals';
import { PINApproveModal } from '../PINApproveModal';

function request(action_type: string): ApprovalRequest {
  return {
    request_id: `r-${action_type}`,
    action_type,
    status: 'REQUESTED',
    requested_by: 'u-maker',
    store_id: 'BV-DHN-01',
    amount: 100,
    created_at: '2026-11-12T09:00:00Z',
    expires_at: '2099-01-01T00:00:00Z',
  };
}

beforeEach(() => {
  toastError.mockReset();
  api.reject.mockReset();
});

describe('rejecting a journal entry from the Approvals inbox', () => {
  it('says a note is needed and keeps the modal open when the server refuses note_required', async () => {
    api.reject.mockResolvedValue({ ok: false, status: 400, error: 'note_required' });
    const onClose = vi.fn();
    render(
      <PINApproveModal request={request('journal_entry')} mode="reject" onClose={onClose} onDone={vi.fn()} />,
    );
    // The reason box no longer claims to be optional for a journal.
    expect(screen.queryByText('Reason (optional)')).toBeNull();
    fireEvent.change(screen.getByLabelText(/PIN/i, { selector: 'input' }), {
      target: { value: '2468' },
    });
    const buttons = within(screen.getByRole('dialog')).getAllByRole('button', { name: 'Reject' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(String(toastError.mock.calls[0][0])).toContain('at least 10 characters');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('leaves the reason optional for every other kind of request', () => {
    render(
      <PINApproveModal request={request('petty_cash')} mode="reject" onClose={vi.fn()} onDone={vi.fn()} />,
    );
    expect(screen.getByText('Reason (optional)')).toBeTruthy();
  });
});
