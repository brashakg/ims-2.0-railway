// ============================================================================
// IMS 2.0 - blind EOD: the CASHIER reaches the Submit Count panel
// ============================================================================
// Owner rulings: a BLIND count IS the day-end (2026-08-25), and cashiers count
// and submit while the manager reviews the variance AFTER submission
// (2026-09-03 -- "only managers submit" explicitly rejected).
//
// The defect: BlindEodTallyPage handed every non-manager an EMPTY session list
// (a hard-coded `setSessions([])` branch), so `activeSession` never resolved
// and the Submit Count panel -- the whole point of the page -- was unreachable
// by the very people who count the drawer. The fix is ONE list call for every
// role; the SERVER blind-redacts cashier rows at the data layer (backend
// coverage: test_till_cashier_blind_list.py asserts the wire carries no
// expected figure -- a DOM assertion alone would pass while the number was
// still in the response body).
//
// The fixtures here are REDACTED rows, exactly what the server now sends a
// cashier: no expected/variance/advisory keys at all, `expected_hidden: true`.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/till', () => ({
  tillApi: {
    open: vi.fn(),
    blindSubmit: vi.fn(),
    lock: vi.fn(),
    reopen: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    zread: vi.fn(),
  },
  paisaToInr: (p?: number | null) => `Rs ${((Math.round(Number(p) || 0)) / 100).toFixed(2)}`,
}));

vi.mock('../../../context/AuthContext', () => ({
  // SALES_STAFF is the surviving cashier-only role (SALES_CASHIER is a retired
  // alias). MANAGER_PLUS(roles) is false for it.
  useAuth: () => ({
    user: { user_id: 'u-cash', roles: ['SALES_STAFF'], activeStoreId: 'BV-1' },
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

import BlindEodTallyPage from '../BlindEodTallyPage';
import { tillApi } from '../../../services/api/till';

const api = tillApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

/** An OPEN shared-drawer row AS THE SERVER SENDS IT TO A CASHIER: blind at the
 *  data layer -- the expected/variance/advisory keys are ABSENT, not null. */
const OPEN_REDACTED = {
  session_id: 'TILL-BV-1-X',
  store_id: 'BV-1',
  session_date: '2026-09-03',
  status: 'OPEN' as const,
  shift: 'PM',
  opening_float_paisa: 10000,
  opening_denominations: [],
  opened_at: '2026-09-03T09:00:00',
  blind_count_paisa: null,
  blind_denominations: [],
  expected_hidden: true,
};

const SUBMITTED_REDACTED = {
  ...OPEN_REDACTED,
  status: 'BLIND_SUBMITTED' as const,
  blind_count_paisa: 100000,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('BlindEodTallyPage as a cashier', () => {
  it('lists the shared drawer, renders Blind close and submits the count', async () => {
    api.list.mockResolvedValueOnce([OPEN_REDACTED]);
    // After the submit, load() refreshes: now the row is BLIND_SUBMITTED.
    api.list.mockResolvedValue([SUBMITTED_REDACTED]);
    api.blindSubmit.mockResolvedValue(SUBMITTED_REDACTED);

    render(<BlindEodTallyPage />);

    // The defect made this unreachable: an empty list meant no activeSession,
    // so the Blind close panel never rendered for a cashier.
    await waitFor(() => expect(screen.getByText('Blind close')).toBeTruthy());
    expect(api.list).toHaveBeenCalledWith(
      expect.objectContaining({ store_id: 'BV-1' }),
    );

    // Count 2 x Rs 500 notes = Rs 1000 = 100000 paisa.
    fireEvent.change(screen.getByLabelText('note of 500 rupees, pieces'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /submit count/i }));
    // Two-step confirm ("Once submitted you cannot edit").
    fireEvent.click(screen.getByRole('button', { name: /confirm submit/i }));

    await waitFor(() =>
      expect(api.blindSubmit).toHaveBeenCalledWith(
        'TILL-BV-1-X',
        expect.objectContaining({ blind_count_paisa: 100000 }),
      ),
    );
  });

  it('shows the waiting state after submission with no expected figure on screen', async () => {
    api.list.mockResolvedValue([SUBMITTED_REDACTED]);

    render(<BlindEodTallyPage />);

    await waitFor(() => expect(screen.getByText(/count submitted/i)).toBeTruthy());
    expect(screen.getByText(/awaiting manager review/i)).toBeTruthy();
    // Their own counted figure is shown...
    expect(screen.getByText(/1000\.00/)).toBeTruthy();
    // ...but no expected/variance blocks render (the manager-only panels), and
    // the wire itself carried none (backend-enforced; see the router tests).
    expect(screen.queryByText('Expected')).toBeNull();
    expect(screen.queryByText('Variance')).toBeNull();
    expect(screen.queryByText('Awaiting lock')).toBeNull();
  });
});
