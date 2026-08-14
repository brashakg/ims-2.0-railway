// ============================================================================
// IMS 2.0 - the Finance dashboard must not present a shortened total as truth
// ============================================================================
// finance.py has known since round 1 of PR #985 that it withholds an expense
// head from readers below ADMIN: it sets `expenses_partially_restricted` on
// /finance/pnl and `categories_partially_restricted` on /finance/budget, with
// the comment "Tell the reader their panel is incomplete rather than letting a
// short total read as the truth."
//
// Nothing on the screen told them. FinanceDashboard rendered the shortened
// figure as "Operating Expenses" and the shortened budget table as the budget.
// That is the PR #960 class of defect: a screen stating something the system
// knows is not true.
//
// THESE TESTS DRIVE THE REAL FinanceDashboard, not the banner component on its
// own. A test of the banner alone would pass even if nobody ever wired the
// flag into the page -- which is precisely the bug. So the api module is
// mocked, the page is mounted, and the assertion is on what a store manager
// actually sees.
//
// BOTH DIRECTIONS, always: a banner that always shows is exactly as useless as
// one that never does, and only the "absent" half can catch that.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../../services/api/finance', () => ({
  financeApi: {
    getRevenue: vi.fn(),
    getPnl: vi.fn(),
    getGstSummary: vi.fn(),
    getOutstanding: vi.fn(),
    getCashFlow: vi.fn(),
    getBudget: vi.fn(),
    getVendorPayments: vi.fn(),
    getPeriodStatus: vi.fn(),
    getPnlByStore: vi.fn(),
    getPnlByCategory: vi.fn(),
    getGstReconciliation: vi.fn(),
    exportTally: vi.fn(),
  },
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      user_id: 'u-sm',
      roles: ['STORE_MANAGER'],
      activeStoreId: 'ZZ-SOLO',
    },
  }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

import FinanceDashboard from '../FinanceDashboard';
import { financeApi } from '../../../services/api/finance';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

// A payroll-EXCLUSIVE P&L, exactly the shape finance.py returns to a store
// manager: no payroll_cost, no net_profit, and total_expenses already short.
const PNL_SHORT = {
  revenue: 300000,
  total_expenses: 24450,
  expenses: { Rent: 21000, Electricity: 3450 },
  expenses_partially_restricted: true,
};

// The same month with nothing withheld (no pay head was ever booked).
const PNL_WHOLE = {
  revenue: 300000,
  total_expenses: 24450,
  expenses: { Rent: 21000, Electricity: 3450 },
};

const BUDGET_SHORT = {
  categories: { Rent: { budget: 20000, actual: 21000 } },
  categories_partially_restricted: true,
};

const BUDGET_WHOLE = {
  categories: { Rent: { budget: 20000, actual: 21000 } },
};

function primeApi(pnl: unknown, budget: unknown) {
  api.getRevenue.mockResolvedValue({ total_revenue: 300000 });
  api.getPnl.mockResolvedValue(pnl);
  api.getGstSummary.mockResolvedValue({});
  api.getOutstanding.mockResolvedValue([]);
  api.getCashFlow.mockResolvedValue({ inflows: 0, outflows: 0 });
  api.getBudget.mockResolvedValue(budget);
  api.getVendorPayments.mockResolvedValue([]);
  api.getPeriodStatus.mockResolvedValue({ locked: false });
  api.getPnlByStore.mockResolvedValue({ stores: [] });
  api.getPnlByCategory.mockResolvedValue({ categories: [] });
  api.getGstReconciliation.mockResolvedValue({ entities: [] });
}

const NOTICE = 'restricted-totals-notice';

describe('FinanceDashboard - incomplete expense totals are declared', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows the banner when the backend says the panel is short', async () => {
    primeApi(PNL_SHORT, BUDGET_SHORT);
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    const notice = await screen.findByTestId(NOTICE);
    expect(notice).toBeInTheDocument();
    expect(notice.textContent).toMatch(/not the full operating cost/i);
    expect(notice.textContent).toMatch(/ask an administrator/i);
  });

  it('shows NOTHING when the backend does not set the flag', async () => {
    // THE OTHER DIRECTION. Without this, a banner hardcoded to always render
    // would pass the test above and be worthless on the shop floor.
    primeApi(PNL_WHOLE, BUDGET_WHOLE);
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument(),
    );
  });

  it('never names the withheld head or its size', async () => {
    // On a 1-5 person store the head plus a number IS somebody's pay packet.
    // The reader is told THAT something is missing, never WHAT or HOW MUCH.
    primeApi(PNL_SHORT, BUDGET_SHORT);
    render(<FinanceDashboard />);
    const notice = await screen.findByTestId(NOTICE);
    const text = notice.textContent?.toLowerCase() || '';
    for (const word of ['salary', 'salaries', 'wage', 'payroll', 'pf', 'esi']) {
      expect(text).not.toContain(word);
    }
    expect(text).not.toMatch(/\d/);
  });

  it('does not raise a banner when the P&L call fails outright', async () => {
    // A rejected call means "we do not know", not "something was withheld".
    // Inventing a restriction banner from a network error would train people
    // to ignore the real one.
    primeApi(PNL_SHORT, BUDGET_SHORT);
    api.getPnl.mockRejectedValue(new Error('boom'));
    api.getBudget.mockRejectedValue(new Error('boom'));
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument(),
    );
  });
});

// ===========================================================================
// THE BUDGETS TAB - a SECOND flag, on a tab the tests above never open.
// ===========================================================================
// The four tests above mount the dashboard on its default tab ('revenue-pl')
// and prime BUDGET_SHORT into the mock, which reads as coverage and is not:
// the budgets branch renders only under `activeTab === 'budgets'`, so deleting
// its banner outright left every one of them green. That is this repo's
// documented hollow-test class - a fixture that supplies the answer to a
// question nobody asks - so the tab is actually clicked here.

describe('FinanceDashboard budgets tab - the short budget declares itself', () => {
  beforeEach(() => vi.clearAllMocks());

  async function openBudgetsTab() {
    const user = userEvent.setup();
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getBudget).toHaveBeenCalled());
    await user.click(screen.getByRole('button', { name: /budgets/i }));
  }

  it('shows the banner on the budgets tab when categories were withheld', async () => {
    primeApi(PNL_WHOLE, BUDGET_SHORT);
    await openBudgetsTab();
    const notice = await screen.findByTestId(NOTICE);
    expect(notice.textContent).toMatch(/not the full operating cost/i);
    // Scoped to what the reader is looking at, not the P&L wording.
    expect(notice.textContent).toMatch(/budget rows and totals/i);
  });

  it('shows NOTHING on the budgets tab when no category was withheld', async () => {
    // The other direction. `expenses_partially_restricted` is deliberately
    // absent too -- if the budgets banner were ever wired to the P&L flag this
    // pair would not notice, so the flags are varied INDEPENDENTLY below.
    primeApi(PNL_WHOLE, BUDGET_WHOLE);
    await openBudgetsTab();
    await waitFor(() =>
      expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument(),
    );
  });

  it('the budgets banner follows the BUDGET flag, not the P&L flag', async () => {
    // Cross-wiring guard. With the P&L short and the budget whole, the budgets
    // tab must stay clean -- otherwise a reader is told the budget leaves
    // something out when it does not, and the notice stops meaning anything.
    primeApi(PNL_SHORT, BUDGET_WHOLE);
    await openBudgetsTab();
    await waitFor(() =>
      expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument(),
    );
  });
});
