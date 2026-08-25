// ============================================================================
// IMS 2.0 - one drawer, one count: the Finance screen says whose count it is
// ============================================================================
// POS Day-End and Finance > Cash Register are two DOORS onto ONE till session.
// Whichever door counts the drawer first owns that count, so a Finance close
// after a Day-End close now REPORTS the Day-End figure rather than its own --
// that is what stops the same drawer reading Rs 2,000 here and Rs 3,000 on the
// Z-Read.
//
// A silently swapped number is its own defect: the manager typed a grid and
// must be told that the figure on the row is not it. finance.py stamps
// `counted_from_shared_record` on the close; this is the screen consuming it.
// The flags used to be stored and rendered NOWHERE, which is the same as not
// having them.
//
// BOTH DIRECTIONS: a marker that is always up is noise, and noise on a cash
// screen gets tuned out inside a week.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/cashRegister', () => ({
  cashRegisterApi: {
    sessions: vi.fn(),
    open: vi.fn(),
    close: vi.fn(),
  },
}));

vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn().mockResolvedValue([]) },
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-sm', roles: ['STORE_MANAGER'], activeStoreId: 'ZZ-SOLO' },
  }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  }),
}));

vi.mock('../../../hooks/useIsOnlineStore', () => ({
  useIsOnlineStore: () => false,
}));

import CashRegisterPage from '../CashRegisterPage';
import { cashRegisterApi } from '../../../services/api/cashRegister';

const api = cashRegisterApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const BADGE = 'counted-at-day-end';

/** A closed session: Rs 3,000 counted at Day-End, Rs 2,000 typed here. The
 *  figure on the row is the shared one -- the drawer only held one lot of
 *  notes. */
const CLOSED_SHARED = {
  session_id: 'ZZ-CR-1',
  store_id: 'ZZ-SOLO',
  status: 'CLOSED' as const,
  opening_float: 1000,
  opening_denominations: [],
  opened_at: '2026-08-24T09:00:00+05:30',
  closed_at: '2026-08-24T21:00:00+05:30',
  closed_by_name: 'ZZ Test Manager',
  counted: 3000,
  expected: 3000,
  variance: 0,
  variance_status: 'BALANCED' as const,
  till_session_id: 'TILL-ZZ-1',
  till_link_ok: true,
  till_already_counted: true,
  till_count_differs: true,
  counted_from_shared_record: true,
};

/** The ordinary case: this screen counted the drawer itself. */
const CLOSED_OWN = {
  ...CLOSED_SHARED,
  session_id: 'ZZ-CR-2',
  counted: 2000,
  expected: 2000,
  till_already_counted: false,
  till_count_differs: false,
  counted_from_shared_record: false,
};

function primeHistory(session: unknown) {
  api.sessions.mockResolvedValue({
    sessions: [session],
    open_session: null,
    expected_preview: null,
  });
}

describe('CashRegisterPage - a count that came from the other door says so', () => {
  beforeEach(() => vi.clearAllMocks());

  it('marks the row whose count was made at Day-End', async () => {
    primeHistory(CLOSED_SHARED);
    render(<CashRegisterPage />);

    expect(await screen.findByTestId(BADGE)).toBeInTheDocument();
    // The figure shown IS the shared one, not the grid typed on this screen.
    expect(screen.getAllByText('₹3,000')).not.toHaveLength(0);
    expect(screen.queryByText('₹2,000')).not.toBeInTheDocument();
  });

  it('shows NOTHING when this screen counted the drawer itself', async () => {
    primeHistory(CLOSED_OWN);
    render(<CashRegisterPage />);

    await waitFor(() => expect(api.sessions).toHaveBeenCalled());
    // POSITIVE CONTROL: prove the history table actually rendered, or "no
    // badge" would also be satisfied by a page that crashed or never loaded.
    expect(await screen.findByText('ZZ-CR-2')).toBeInTheDocument();
    expect(screen.getAllByText('₹2,000')).not.toHaveLength(0);
    expect(screen.queryByTestId(BADGE)).not.toBeInTheDocument();
  });
});
