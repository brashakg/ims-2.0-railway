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
//
// OWNER RULING 2026-09-27 (one door): the dashboard's Budgets and Cash Flow
// tabs were deleted, and with them the two tab describes that used to live at
// the bottom of this file. Where that coverage went:
//   - Budgets: /finance/budgeting (BudgetingPage) carries its own notice,
//     pinned both directions in BudgetingPageRestrictedNotice.test.tsx.
//   - Cash flow: /finance/cash-flow (CashFlowPage) is SUPERADMIN / ADMIN /
//     ACCOUNTANT only, and its /finance/owner-dashboard figures are
//     deliberately NOT stripped for that set (cash_flow.py, owner ruling
//     2026-08-14) -- there is no short total there to declare. The roles the
//     strip protected (store / area managers) cannot open that page at all.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../../../services/api/finance', () => ({
  financeApi: {
    getRevenue: vi.fn(),
    getPnl: vi.fn(),
    getGstSummary: vi.fn(),
    getOutstanding: vi.fn(),
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
import { FinanceRevenuePlPage } from '../FinanceRevenuePlPage';
import { financeApi } from '../../../services/api/finance';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

// The dashboard is a layout with one page per section (Wave 6 split), so it is
// mounted the way the app mounts it: the layout route with the Revenue & P&L
// section as its index.
function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={['/finance/dashboard']}>
      <Routes>
        <Route path="/finance/dashboard" element={<FinanceDashboard />}>
          <Route index element={<FinanceRevenuePlPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

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

function primeApi(pnl: unknown) {
  api.getRevenue.mockResolvedValue({ total_revenue: 300000 });
  api.getPnl.mockResolvedValue(pnl);
  api.getGstSummary.mockResolvedValue({});
  api.getOutstanding.mockResolvedValue([]);
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
    primeApi(PNL_SHORT);
    renderDashboard();
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    const notice = await screen.findByTestId(NOTICE);
    expect(notice).toBeInTheDocument();
    expect(notice.textContent).toMatch(/not the full operating cost/i);
    expect(notice.textContent).toMatch(/ask an administrator/i);
  });

  it('shows NOTHING when the backend does not set the flag', async () => {
    // THE OTHER DIRECTION. Without this, a banner hardcoded to always render
    // would pass the test above and be worthless on the shop floor.
    primeApi(PNL_WHOLE);
    renderDashboard();
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument(),
    );
  });

  it('never names the withheld head or its size', async () => {
    // On a 1-5 person store the head plus a number IS somebody's pay packet.
    // The reader is told THAT something is missing, never WHAT or HOW MUCH.
    primeApi(PNL_SHORT);
    renderDashboard();
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
    primeApi(PNL_SHORT);
    api.getPnl.mockRejectedValue(new Error('boom'));
    renderDashboard();
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId(NOTICE)).not.toBeInTheDocument(),
    );
  });
});
