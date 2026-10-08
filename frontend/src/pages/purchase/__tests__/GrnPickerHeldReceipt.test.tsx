// ============================================================================
// Bill from a receipt: a held receipt says what it is waiting for (audit C1)
// ============================================================================
// The picker reads the one held-lines reading (heldLinesSummary): a line
// beyond the order waits for the store manager, a line waiting to be
// catalogued for the catalogue manager -- never "waiting to be catalogued" for
// both. A held receipt is not billable and offers no second ask: each
// catalogue manager already has a task by name.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const getGRNs = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api', () => ({ vendorsApi: { getGRNs } }));
vi.mock('../../../services/api/vendorAp', () => ({ purchaseInvoicesApi: {} }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-DHN-02', roles: ['ACCOUNTANT'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { GrnPickerModal } from '../invoices/pickers';

describe('the receipt picker', () => {
  it('a receipt held beyond its order says it waits for the store manager, with no Invoice or ask', async () => {
    getGRNs.mockImplementation(async (p: { status?: string }) =>
      p?.status === 'PARTIALLY_ACCEPTED'
        ? {
            grns: [
              {
                grn_id: 'g-held',
                grn_number: 'RCPT/BV-DHN-02/26-27/0031',
                status: 'PARTIALLY_ACCEPTED',
                total_accepted: 3,
                unresolved_lines: [{ product_id: 'p-boss', accepted_qty: 2, reason: 'over_order' }],
              },
            ],
          }
        : { grns: [] },
    );
    render(<GrnPickerModal onClose={() => {}} onPick={async () => {}} />);
    expect(
      await screen.findByText(/Held: 1 line\(s\) beyond the order, for the store manager/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/waiting to be catalogued/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Invoice/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Ask for cataloguing/ })).toBeNull();
  });
});
