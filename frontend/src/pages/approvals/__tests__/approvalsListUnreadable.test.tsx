// ============================================================================
// IMS 2.0 - an approvals list that could not be read says so (D112)
// ============================================================================
// PR-1411 round 5: the approvals inbox, "my requests" and the refund approvals
// queue now get a 503 when the server could not read the requests (it used to
// answer an empty list). The three screens swallowed the error and showed
// "No pending approvals." / "You have not submitted any approval requests." --
// the same false "nothing is waiting". They now say the list could not be
// read; a list that really is empty still says so.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-admin', roles: ['ADMIN'] }, hasRole: () => true }),
}));
vi.mock('../../../components/settings/ApprovalPinSection', () => ({ ApprovalPinSection: () => null }));
const api = vi.hoisted(() => ({ getInbox: vi.fn(), getMyRequests: vi.fn() }));
vi.mock('../../../services/api/approvals', () => ({ approvalsApi: api }));

import { ApprovalInboxPage } from '../ApprovalInboxPage';
import { MyRequestsPage } from '../MyRequestsPage';
import { PendingApprovalsPage } from '../../returns/PendingApprovalsPage';

const unreadable = { response: { status: 503, data: { detail: { error: 'approval_unreadable' } } } };
const empty = { requests: [], total: 0 };

const screens = [
  { name: 'approvals inbox', Page: ApprovalInboxPage, call: 'getInbox' as const,
    failed: /approvals could not be read/, none: 'No pending approvals.' },
  { name: 'my requests', Page: MyRequestsPage, call: 'getMyRequests' as const,
    failed: /requests could not be read/, none: 'You have not submitted any approval requests.' },
  { name: 'refund approvals', Page: PendingApprovalsPage, call: 'getInbox' as const,
    failed: /refund approvals could not be read/, none: 'No refunds awaiting approval.' },
];

describe.each(screens)('$name that could not be read (D112)', ({ Page, call, failed, none }) => {
  beforeEach(() => vi.clearAllMocks());

  it('says so instead of the empty-list text', async () => {
    api[call].mockRejectedValue(unreadable);
    render(<Page />);
    expect((await screen.findByRole('alert')).textContent).toMatch(failed);
    expect(screen.queryByText(none)).toBeNull();
  });

  it('a list that is really empty still says so', async () => {
    api[call].mockResolvedValue(empty);
    render(<Page />);
    expect(await screen.findByText(none)).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
