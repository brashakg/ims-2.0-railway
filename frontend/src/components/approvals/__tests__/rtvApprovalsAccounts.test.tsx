// ============================================================================
// IMS 2.0 - Return-to-Vendor approvals are the accounts roles' (R1)
// ============================================================================
// Owner ruling 2026-10-12: an 'rtv' approval (the supplier credit raised on a
// vendor RMA) goes to ADMIN / SUPERADMIN / ACCOUNTANT, who see its amount;
// store and area managers no longer approve one. The server routes it
// (services/approvals.may_approve) and stamps each inbox row with can_approve;
// the screens follow:
//   * Approve / Reject show only on a row the reader can act on -- an
//     accountant acts on the rtv row, not on a refund they can only read;
//   * the Approvals page carries the reader's own approval PIN, because an
//     accountant has no other screen that sets one;
//   * a PIN-less approver is told where to set it.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

let roles: string[] = ['ACCOUNTANT'];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-acct', roles },
    hasRole: (want: string | string[]) => {
      if (roles.includes('ADMIN') || roles.includes('SUPERADMIN')) return true;
      return (Array.isArray(want) ? want : [want]).some((r) => roles.includes(r));
    },
  }),
}));
const toastError = vi.hoisted(() => vi.fn());
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: toastError, warning: vi.fn(), info: vi.fn() }),
}));
const api = vi.hoisted(() => ({
  getInbox: vi.fn(),
  getPinStatus: vi.fn(),
  setPin: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
}));
vi.mock('../../../services/api/approvals', () => ({ approvalsApi: api }));

import type { InboxRequest } from '../ApprovalRequestCard';
import { ApprovalRequestCard } from '../ApprovalRequestCard';
import { PINApproveModal } from '../PINApproveModal';
import { ApprovalInboxPage } from '../../../pages/approvals/ApprovalInboxPage';

function row(action_type: string, amount: number | null, can_approve?: boolean): InboxRequest {
  return {
    request_id: `r-${action_type}`,
    action_type,
    status: 'REQUESTED',
    requested_by: 'u-maker',
    requested_by_name: 'Ravi',
    store_id: 'BV-DHN-01',
    amount,
    reason: `${action_type} reason`,
    created_at: '2026-10-12T09:00:00Z',
    expires_at: '2099-01-01T00:00:00Z',
    ...(can_approve === undefined ? {} : { can_approve }),
  };
}

const RTV = row('rtv', 48250, true);
const REFUND_READ_ONLY = row('refund', 1999, false);

beforeEach(() => {
  roles = ['ACCOUNTANT'];
  toastError.mockReset();
  Object.values(api).forEach((fn) => fn.mockReset());
  api.getPinStatus.mockResolvedValue({ has_pin: false, pin_set_at: null });
  // Pending and ALL answer alike (History keeps only non-REQUESTED rows).
  api.getInbox.mockResolvedValue({ requests: [RTV, REFUND_READ_ONLY], total: 2 });
});

describe('the inbox offers Approve / Reject only where the reader can act', () => {
  it('ACCOUNTANT: the rtv row has the buttons and the amount; a refund they only read has none', async () => {
    render(<ApprovalInboxPage />);
    const rtvCard = (await screen.findByText('Return to Vendor')).closest('div.border') as HTMLElement;
    const refundCard = screen.getByText('Refund').closest('div.border') as HTMLElement;

    expect(within(rtvCard).getByRole('button', { name: 'Approve' })).toBeInTheDocument();
    expect(within(rtvCard).getByRole('button', { name: 'Reject' })).toBeInTheDocument();
    expect(rtvCard.textContent).toContain('₹48,250');

    expect(within(refundCard).queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(within(refundCard).queryByRole('button', { name: 'Reject' })).toBeNull();
    expect(refundCard.textContent).toContain('₹1,999');
  });

  it('a row the server did not stamp keeps its buttons (older answer)', () => {
    roles = ['STORE_MANAGER'];
    render(<ApprovalRequestCard request={row('refund', 999)} showActions />);
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument();
  });

  it('can_approve=false hides the buttons for a manager too (a tier above theirs)', () => {
    roles = ['STORE_MANAGER'];
    render(<ApprovalRequestCard request={row('refund', 4500, false)} showActions />);
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull();
  });
});

describe('the approver sets their own PIN on the Approvals page', () => {
  it('ACCOUNTANT: the page carries "Your Approval PIN" for this login', async () => {
    render(<ApprovalInboxPage />);
    expect(await screen.findByText('Your Approval PIN')).toBeInTheDocument();
    await waitFor(() => expect(api.getPinStatus).toHaveBeenCalledWith('u-acct'));
  });

  it('a PIN-less approver is pointed at the Approvals page, not a Settings tab they cannot open', async () => {
    api.approve.mockResolvedValue({ ok: false, status: 423, error: 'pin_not_set' });
    render(<PINApproveModal request={RTV} mode="approve" onClose={vi.fn()} onDone={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/PIN/i, { selector: 'input' }), {
      target: { value: '2468' },
    });
    const dialog = screen.getByRole('dialog');
    const buttons = within(dialog).getAllByRole('button', { name: 'Approve' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toContain('Approvals page');
    expect(msg).not.toContain('Settings');
  });
});
