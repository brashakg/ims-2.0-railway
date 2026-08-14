// ============================================================================
// IMS 2.0 - the budgeting screen declares a short table, and stays discreet
// ============================================================================
// GET /budgets/variance sets `heads_partially_restricted` when it has withheld
// pay-shaped heads from a reader below ADMIN. This screen has said so since
// round 1 -- but it said it by NAMING the head ("Salary and payroll heads are
// not shown on this screen").
//
// That wording defeated the discretion of the banner one click away. The
// Finance dashboard deliberately says only that something is missing, because
// on a 1-5 person store the head plus a figure IS an individual's pay packet
// (owner ruling 2026-08-09). A manager who reads the explicit sentence here
// learns exactly how to decode the discreet one there, and the careful wording
// on the other screen buys nothing. Discretion applied on one screen and not
// its twin is not discretion, so both now render the same component.
//
// BOTH DIRECTIONS, and a positive control on the silent case: "no banner" is
// also what a crashed page looks like.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/budgets', () => ({
  budgetsApi: {
    list: vi.fn(),
    variance: vi.fn(),
    upsert: vi.fn(),
    remove: vi.fn(),
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
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

import BudgetingPage from '../BudgetingPage';
import { budgetsApi } from '../../../services/api/budgets';

const api = budgetsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const NOTICE = 'restricted-totals-notice';

const TOTALS = {
  revenue_planned: 300000,
  revenue_actual: 300000,
  revenue_variance: 0,
  revenue_variance_pct: 0,
  expense_planned: 20000,
  expense_actual: 21000,
  expense_variance: -1000,
  expense_variance_pct: -5,
  net_planned: 280000,
  net_actual: 279000,
};

// A pay-free variance table: the rows AND the totals are short by the same
// amount, so the table still adds up. It is simply not the whole cost.
const VARIANCE_SHORT = {
  store_id: 'ZZ-SOLO',
  period: '2026-08',
  lines: [{ head: 'Rent', planned_amount: 20000, actual_amount: 21000, variance: -1000, variance_pct: -5 }],
  totals: TOTALS,
  heads_partially_restricted: true,
};

const VARIANCE_WHOLE = {
  store_id: 'ZZ-SOLO',
  period: '2026-08',
  lines: [{ head: 'Rent', planned_amount: 20000, actual_amount: 21000, variance: -1000, variance_pct: -5 }],
  totals: TOTALS,
};

function primeBudgets(variance: unknown) {
  api.list.mockResolvedValue({ budgets: [], total: 0 });
  api.variance.mockResolvedValue(variance);
}

describe('BudgetingPage - a short budget table declares itself, discreetly', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the banner when heads were withheld', async () => {
    primeBudgets(VARIANCE_SHORT);
    render(<BudgetingPage />);
    const notice = await screen.findByTestId(NOTICE);
    expect(notice.textContent).toMatch(/not the full operating cost/i);
    expect(notice.textContent).toMatch(/ask an administrator/i);
  });

  it('shows NOTHING when nothing was withheld', async () => {
    primeBudgets(VARIANCE_WHOLE);
    render(<BudgetingPage />);
    await waitFor(() => expect(api.variance).toHaveBeenCalled());
    // POSITIVE CONTROL: prove the table actually rendered before concluding
    // that the absent banner means anything.
    expect(await screen.findAllByText(/rent/i)).not.toHaveLength(0);
    expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument();
  });

  it('never names the withheld head or its size', async () => {
    // THIS IS THE REGRESSION. The previous copy said "Salary and payroll heads
    // are not shown on this screen" -- restore that sentence and this fails.
    primeBudgets(VARIANCE_SHORT);
    render(<BudgetingPage />);
    const notice = await screen.findByTestId(NOTICE);
    const text = notice.textContent?.toLowerCase() || '';
    for (const word of ['salary', 'salaries', 'wage', 'payroll', 'pf', 'esi']) {
      expect(text).not.toContain(word);
    }
    expect(text).not.toMatch(/\d/);
  });
});
