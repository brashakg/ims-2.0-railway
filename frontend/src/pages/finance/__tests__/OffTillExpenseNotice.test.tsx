// ============================================================================
// IMS 2.0 - the till screen says its expected figure leaves something out
// ============================================================================
// THIS IS NOT THE REDACTION BANNER, and conflating the two would be a bug in
// the opposite direction. Since 92ab066 the drawer maths excludes payroll-
// shaped heads for EVERY role including ADMIN, because salaries, advances and
// PF/ESI are never paid from a shop till (owner, 2026-08-14). So "Expected in
// drawer" is now CORRECT, not shortened, and nothing here may imply that a
// store manager is being shown less than an administrator would be.
//
// What it must do is tell the person holding the cash. If a manager books a
// pay head as an expense and then watches the expected figure not move, the
// screen has to say why -- or they will "correct" a count that was right, and
// a corrected count is a real cash difference in a real drawer.
//
// finance.py:4497 sets `off_till_expense_advisory` on the live preview and
// finance.py:4840 carries it on each reconciliation history row. These tests
// drive the REAL pages, because a component-only test would pass while nobody
// consumed the flag -- which is exactly the defect this round was opened for.
//
// BOTH DIRECTIONS on every screen: an advisory that is always up is noise, and
// noise on a cash screen gets tuned out inside a week.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/cashRegister', () => ({
  cashRegisterApi: {
    sessions: vi.fn(),
    open: vi.fn(),
    close: vi.fn(),
  },
}));

vi.mock('../../../services/api/cashReconciliation', () => ({
  cashReconciliationApi: { summary: vi.fn() },
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
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

// A physical store. The ONLINE branch replaces the whole till panel with a
// "there is no drawer here" card, which would make every assertion below pass
// vacuously by rendering nothing at all.
vi.mock('../../../hooks/useIsOnlineStore', () => ({
  useIsOnlineStore: () => false,
}));

import CashRegisterPage from '../CashRegisterPage';
import { cashRegisterApi } from '../../../services/api/cashRegister';
import { OFF_TILL_EXPENSE_NOTICE } from '../offTillExpenseCopy';

const api = cashRegisterApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const ADVISORY = 'off-till-expense-advisory';

const OPEN_SESSION = {
  session_id: 'ZZ-SESS-1',
  store_id: 'ZZ-SOLO',
  status: 'OPEN' as const,
  shift: 'PM',
  opening_float: 5000,
  opening_denominations: [],
  opened_at: '2026-08-14T09:00:00+05:30',
  opened_by_name: 'ZZ Test Manager',
};

// The drawer as finance.py now returns it: an ordinary cash expense IS
// deducted, the pay head is NOT, and the flag says so. No amount anywhere.
const PREVIEW_WITH_OFF_TILL = {
  opening_float: 5000,
  cash_sales: 40000,
  cash_refunds: 0,
  cash_expenses: 2333.33,
  bank_deposit: 0,
  expected: 42666.67,
  off_till_expense_advisory: true,
  off_till_expense_message:
    'One or more expenses booked here in this period are not paid out of the ' +
    'shop till, so they are left out of the expected-cash figure. If your ' +
    'count does not tally, check with an administrator before adjusting anything.',
};

// The same day with no pay head booked at all -- the ordinary case, and the
// one that must stay silent.
const PREVIEW_CLEAN = {
  opening_float: 5000,
  cash_sales: 40000,
  cash_refunds: 0,
  cash_expenses: 2333.33,
  bank_deposit: 0,
  expected: 42666.67,
  off_till_expense_advisory: false,
  off_till_expense_message: null,
};

function primeTill(preview: unknown) {
  api.sessions.mockResolvedValue({
    sessions: [],
    open_session: OPEN_SESSION,
    expected_preview: preview,
  });
}

describe('CashRegisterPage - the counter is told the expected figure is not everything', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the advisory when the backend flags an off-till expense', async () => {
    primeTill(PREVIEW_WITH_OFF_TILL);
    render(<CashRegisterPage />);
    const note = await screen.findByTestId(ADVISORY);
    expect(note.textContent).toMatch(/not paid out of the shop till/i);
    expect(note.textContent).toMatch(/check with an administrator/i);
  });

  it('shows NOTHING when nothing was left out', async () => {
    // Without this half, an advisory hardcoded to always render would pass the
    // test above and be worthless -- permanently-on warnings are ignored.
    primeTill(PREVIEW_CLEAN);
    render(<CashRegisterPage />);
    await waitFor(() => expect(api.sessions).toHaveBeenCalled());
    // POSITIVE CONTROL. Without an anchor proving the reconciliation panel
    // actually rendered, "no advisory" would also be satisfied by a page that
    // crashed, stayed on its spinner, or took the ONLINE branch -- i.e. the
    // assertion would be true by construction. Since the 2026-08-25 ruling the
    // panel is BLIND while counting: the anchor is the blinded close panel,
    // and the expected figure must NOT be on the page.
    expect(await screen.findByText(/Close the day/i)).toBeInTheDocument();
    expect(screen.getByText(/stays hidden while you count/i)).toBeInTheDocument();
    expect(screen.queryByText(/expected in drawer/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId(ADVISORY)).not.toBeInTheDocument();
  });

  it('never names the head or its size', async () => {
    // On a 1-5 person store the head plus a number IS somebody's pay packet.
    primeTill(PREVIEW_WITH_OFF_TILL);
    render(<CashRegisterPage />);
    const note = await screen.findByTestId(ADVISORY);
    const text = note.textContent?.toLowerCase() || '';
    for (const word of ['salary', 'salaries', 'wage', 'payroll', 'advance', 'pf', 'esi']) {
      expect(text).not.toContain(word);
    }
    expect(text).not.toMatch(/\d/);
  });

  it('does not claim the figure is short for THIS ROLE', async () => {
    // The drawer exclusion is identical for ADMIN and SUPERADMIN (92ab066).
    // Wording that hints at a role-based restriction here would be a lie in
    // the opposite direction from the P&L banner -- it would tell a manager
    // an administrator sees a bigger expected-cash figure. Nobody does.
    primeTill(PREVIEW_WITH_OFF_TILL);
    render(<CashRegisterPage />);
    const note = await screen.findByTestId(ADVISORY);
    const text = note.textContent?.toLowerCase() || '';
    for (const phrase of ['your role', 'not shown to you', 'permission', 'restricted']) {
      expect(text).not.toContain(phrase);
    }
  });

  it('renders a sentence, never an empty warning box, if the text is missing', async () => {
    // A response flagged but textless (an older cached body, a proxy dropping
    // the field) previously rendered an amber box with a warning icon and no
    // words in it. A warning that says nothing is worse than none.
    primeTill({ ...PREVIEW_WITH_OFF_TILL, off_till_expense_message: null });
    render(<CashRegisterPage />);
    const note = await screen.findByTestId(ADVISORY);
    expect(note.textContent?.trim()).toBe(OFF_TILL_EXPENSE_NOTICE);
  });
});
