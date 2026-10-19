// ============================================================================
// IMS 2.0 - a manager sees the WHOLE bill before approving a discount
// ============================================================================
// Owner decision 2026-10-18 (money review BLOCK-2): a discount_override
// request is raised by the SERVER from its own pricing of the bill, and the
// card shows it in full -- shop, cashier, product names, qty, MRP, price, line
// %, bill %, rupees off, the bill before and after. The cashier's words are
// only the note, and the card labels them as the cashier's.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'm1', roles: ['STORE_MANAGER'] }, hasRole: () => false }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../services/api/approvals', () => ({
  approvalsApi: { approve: vi.fn(), reject: vi.fn() },
}));

import type { ApprovalRequest } from '../../../services/api/approvals';
import { ApprovalRequestCard } from '../ApprovalRequestCard';
import { PINApproveModal } from '../PINApproveModal';

const DISCOUNT: ApprovalRequest = {
  request_id: 'REQ-9',
  action_type: 'discount_override',
  status: 'REQUESTED',
  requested_by: 'u-staff',
  requested_by_name: 'Priya Staff',
  store_id: 'BV-BOK-01',
  store_name: 'Better Vision Bokaro',
  amount: null,
  reason: 'regular customer',
  created_at: '2026-10-19T09:00:00Z',
  expires_at: '2099-01-01T00:00:00Z',
  context: {
    bill_hash: 'x'.repeat(64),
    discount_percent: 18,
    zero_total: false,
    lines: [
      { product_id: 'p-1', product_name: 'Ray-Ban RB3025', quantity: 2, mrp: 10000,
        unit_price: 10000, discount_percent: 18 },
    ],
    cart_discount_percent: 0,
    discount_rupees: 3600,
    bill_before: 20000,
    bill_after: 16400,
    customer_id: 'cust-x',
    customer_name: 'Rohan Mehta',
  },
};

describe('a discount_override request shows the bill the manager approves', () => {
  it('names the shop, the cashier, the customer and every line, with the money', () => {
    render(<ApprovalRequestCard request={DISCOUNT} showActions />);
    for (const text of ['Better Vision Bokaro', 'Rohan Mehta', 'Ray-Ban RB3025', '₹16,400', '₹20,000']) {
      expect(screen.getAllByText(new RegExp(text)).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText('₹10,000').length).toBe(2); // MRP + price
    expect(screen.getAllByText('18%').length).toBeGreaterThan(0);
    // Rupees off is the headline amount, not an em-dash.
    expect(screen.getAllByText('₹3,600').length).toBeGreaterThan(0);
    // The cashier's words are labelled as theirs.
    expect(screen.getByText(/Cashier's note:/)).toBeTruthy();
    expect(screen.getByText(/regular customer/)).toBeTruthy();
    // No raw context dump.
    expect(screen.queryByText(/bill_hash/)).toBeNull();
  });

  it('carries the rupees off and the labelled note into the PIN step', () => {
    render(<PINApproveModal request={DISCOUNT} mode="approve" onClose={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByText('₹3,600')).toBeTruthy();
    expect(screen.getByText(/Cashier's note: regular customer/)).toBeTruthy();
  });
});
