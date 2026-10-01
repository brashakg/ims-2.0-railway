// ============================================================================
// IMS 2.0 - a Return-to-Vendor approval's amount is supplier money
// ============================================================================
// Owner ruling 2026-10-01: "supplier balances should not be shown to anyone
// apart from admin superadmin and accountant". An 'rtv' approval request
// carries, as its amount, the credit a supplier gave us on a vendor RMA
// (routers/vendor_rma record_credit_note). The approver inbox is open to store
// and area managers, so the card, the PIN modal and the maker's own list show
// them an em-dash for an 'rtv' amount -- even if an answer still carries it.
// Every other action type (a refund, a discount override) keeps its amount for
// them: the rule is supplier money, not approvals in general.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

let roles: string[] = ['STORE_MANAGER'];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', roles },
    // Same contract as AuthContext.hasRole: ADMIN / SUPERADMIN pass any check.
    hasRole: (want: string | string[]) => {
      if (roles.includes('ADMIN') || roles.includes('SUPERADMIN')) return true;
      return (Array.isArray(want) ? want : [want]).some((r) => roles.includes(r));
    },
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
const getMyRequests = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/approvals', () => ({
  approvalsApi: { getMyRequests, approve: vi.fn(), reject: vi.fn() },
}));

import type { ApprovalRequest } from '../../../services/api/approvals';
import { ApprovalRequestCard } from '../ApprovalRequestCard';
import { PINApproveModal } from '../PINApproveModal';
import { MyRequestsPage } from '../../../pages/approvals/MyRequestsPage';

function request(action_type: string, amount: number): ApprovalRequest {
  return {
    request_id: `r-${action_type}`,
    action_type,
    status: 'REQUESTED',
    requested_by: 'u2',
    requested_by_name: 'Ravi',
    store_id: 'BV-DHN-01',
    amount,
    reason: 'Supplier credit for returned frames',
    created_at: '2026-10-01T09:00:00Z',
    expires_at: '2099-01-01T00:00:00Z',
  };
}

const RTV = request('rtv', 48250);
const REFUND = request('refund', 1999);

beforeEach(() => {
  roles = ['STORE_MANAGER'];
  getMyRequests.mockReset();
});

describe('rtv approval amount - managers see a dash', () => {
  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])('%s: the inbox card shows no supplier credit', (role) => {
    roles = [role];
    render(<ApprovalRequestCard request={RTV} showActions />);
    expect(screen.getByText('Return to Vendor')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('48,250');
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('STORE_MANAGER: the PIN modal shows no supplier credit', () => {
    render(<PINApproveModal request={RTV} mode="approve" onClose={vi.fn()} onDone={vi.fn()} />);
    expect(document.body.textContent).not.toContain('48,250');
  });

  it('STORE_MANAGER: My requests shows no supplier credit', async () => {
    getMyRequests.mockResolvedValue({ requests: [RTV, REFUND], total: 2 });
    render(<MyRequestsPage />);
    expect(await screen.findByText('Return to Vendor')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('48,250');
    // Not supplier money: a refund keeps its amount.
    expect(document.body.textContent).toContain('₹1,999');
  });

  it('STORE_MANAGER: a refund request still shows its amount (the rule is supplier money only)', () => {
    render(<ApprovalRequestCard request={REFUND} showActions />);
    expect(document.body.textContent).toContain('₹1,999');
  });
});

describe('rtv approval amount - the accounts roles still read it', () => {
  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])('%s: card, modal and My requests show it', async (role) => {
    roles = [role];
    getMyRequests.mockResolvedValue({ requests: [RTV], total: 1 });
    const card = render(<ApprovalRequestCard request={RTV} showActions />);
    expect(document.body.textContent).toContain('₹48,250');
    card.unmount();
    const modal = render(<PINApproveModal request={RTV} mode="approve" onClose={vi.fn()} onDone={vi.fn()} />);
    expect(document.body.textContent).toContain('₹48,250');
    modal.unmount();
    render(<MyRequestsPage />);
    await screen.findByText('Return to Vendor');
    expect(document.body.textContent).toContain('₹48,250');
  });
});
