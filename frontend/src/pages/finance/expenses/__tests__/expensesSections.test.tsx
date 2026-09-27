// ============================================================================
// Wave 6 Expenses split - one smoke test per section, the route gates, and the
// legacy ?tab= mapper
// ============================================================================
// The old ExpenseTracker held nine role-gated tabs behind one URL and had no
// test at all. Each tab is now its own URL under ExpensesLayout, fed by the
// layout's one expenses load through <Outlet context>. These tests drive the
// REAL layout + section at each URL (a section that lost its data wiring in
// the move renders empty and fails here), then the REAL financeRoutes gates
// (each section's allowedRoles must be exactly its old JSX gate).

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

// jsdom has no requestIdleCallback, so the layout's chunk-warming would fall
// back to a 1.5s setTimeout that can fire after teardown. Not under test.
vi.stubGlobal('requestIdleCallback', () => 0);

let currentRoles: string[] = [];

vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', roles: currentRoles, activeStoreId: 'ZZ-STORE' },
    isAuthenticated: true,
    isLoading: false,
    // Real ProtectedRoute semantics: SUPERADMIN/ADMIN pass every gate.
    hasRole: (roles: string[]) =>
      currentRoles.includes('SUPERADMIN') || currentRoles.includes('ADMIN')
      || roles.some((r) => currentRoles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

// One stable object: the layout's loaders list `toast` as a dependency, so a
// fresh object per render would re-run them forever.
const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}));
vi.mock('../../../../context/ToastContext', () => ({ useToast: () => toastMock }));

vi.mock('../../../../services/api/expenses', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../../services/api/expenses')>();
  return {
    ...actual,
    expensesApi: {
      getExpenses: vi.fn(),
      getPendingApproval: vi.fn(),
      getToEnter: vi.fn(),
      getAging: vi.fn(),
      getDuplicateBills: vi.fn(),
      getAdvances: vi.fn(),
      getPettyCashBalance: vi.fn(),
      getPettyCashSettlementPosition: vi.fn(),
      listPettyCashSettlements: vi.fn(),
    },
  };
});

import { expensesApi } from '../../../../services/api/expenses';
import { ExpensesLayout } from '../ExpensesLayout';
import { MyExpensesSection } from '../MyExpensesSection';
import { ExpenseApprovalsSection } from '../ExpenseApprovalsSection';
import { ExpenseEntrySection } from '../ExpenseEntrySection';
import { ExpenseAgingSection } from '../ExpenseAgingSection';
import { ExpenseDuplicatesSection } from '../ExpenseDuplicatesSection';
import { ExpenseAdvancesSection } from '../ExpenseAdvancesSection';
import { PettyCashFloatSection } from '../PettyCashFloatSection';
import { DaySettlementSection } from '../DaySettlementSection';
import { ExpenseSummarySection } from '../ExpenseSummarySection';
import { legacyTabTarget } from '../legacyTabRedirect';
import { financeRoutes } from '../../../../routes/financeRoutes';

const api = expensesApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const row = (id: string, description: string, status: string, extra = {}) => ({
  expense_id: id, category: 'supplies', amount: 300, description, status,
  expense_date: '2026-09-20', employee_name: 'ZZ Staff', ...extra,
});

beforeEach(() => {
  api.getExpenses.mockResolvedValue({ expenses: [row('EXP-MINE-1', 'ZZ bus fare to Ranchi', 'PENDING')] });
  api.getPendingApproval.mockResolvedValue({ expenses: [row('EXP-APR-1', 'ZZ lens cloths', 'PENDING')] });
  api.getToEnter.mockResolvedValue({ expenses: [row('EXP-ENT-1', 'ZZ courier charges', 'SENT_TO_ACCOUNTANT')] });
  api.getAging.mockResolvedValue({
    buckets: { '0-7': { count: 1, amount: 100 }, '8-15': { count: 0, amount: 0 }, '15+': { count: 0, amount: 0 } },
    rows: [{
      expense_id: 'EXP-AGE-1', employee_name: 'ZZ Aging Person', category: 'food', amount: 100,
      status: 'APPROVED', since: '2026-09-25', days_pending: 3, bucket: '0-7',
    }],
    total_count: 1,
    total_amount: 100,
  });
  api.getDuplicateBills.mockResolvedValue({
    expenses: [row('EXP-DUP-1', 'ZZ duplicated receipt', 'PENDING', { duplicate_bill: true })],
  });
  api.getAdvances.mockResolvedValue({
    advances: [{
      advance_id: 'ADV-1', employee_id: 'u-2', employee_name: 'ZZ Sunil', advance_type: 'TRAVEL',
      amount: 2000, purpose: 'ZZ camp bus fare', status: 'PENDING', created_at: '2026-09-20',
    }],
  });
  api.getPettyCashBalance.mockResolvedValue({
    ok: true, store_id: 'ZZ-STORE', exists: true, balance: 3200, float_limit: 5000,
    low_balance_threshold: 1000, status: 'OPEN', is_low: false,
    recent_ledger: [{
      txn_id: 'T1', type: 'CREDIT', delta: 5000, balance_after: 5000,
      reason: 'ZZ opening float', created_at: '2026-09-01',
    }],
  });
  api.getPettyCashSettlementPosition.mockResolvedValue({
    ok: true, store_id: 'ZZ-STORE', settle_date: '2026-09-28', exists: true, opening_float: 5000,
    credits_today: 0, debits_today: 1800, payouts_count: 2, expected_closing: 3200, tolerance: 0,
    settled: false, settlement: null, day_ledger: [],
  });
  api.listPettyCashSettlements.mockResolvedValue({ settlements: [] });
});

function renderSection(path: string, roles: string[] = ['ADMIN']) {
  currentRoles = roles;
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/finance/expenses" element={<ExpensesLayout />}>
          <Route index element={<MyExpensesSection />} />
          <Route path="approvals" element={<ExpenseApprovalsSection />} />
          <Route path="entry" element={<ExpenseEntrySection />} />
          <Route path="aging" element={<ExpenseAgingSection />} />
          <Route path="duplicates" element={<ExpenseDuplicatesSection />} />
          <Route path="advances" element={<ExpenseAdvancesSection />} />
          <Route path="float" element={<PettyCashFloatSection />} />
          <Route path="settle" element={<DaySettlementSection />} />
          <Route path="summary" element={<ExpenseSummarySection />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

/** The section's own nav link is the active one - one URL per section. */
async function expectActiveLink(name: RegExp) {
  expect(await screen.findByRole('link', { name })).toHaveAttribute('aria-current', 'page');
}

describe('each expenses section renders at its own URL', () => {
  it('my (index): the user\'s own expenses', async () => {
    renderSection('/finance/expenses');
    expect(await screen.findByText('ZZ bus fare to Ranchi')).toBeInTheDocument();
    await expectActiveLink(/^My Expenses$/);
  });

  it('approvals: the approval queue with Approve / Reject', async () => {
    renderSection('/finance/expenses/approvals');
    expect(await screen.findByText('ZZ lens cloths')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Approve/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reject/ })).toBeInTheDocument();
    await expectActiveLink(/^Pending Approval/);
  });

  it('entry: the ledger-entry queue', async () => {
    renderSection('/finance/expenses/entry');
    expect(await screen.findByText('ZZ courier charges')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Mark entered/ })).toBeInTheDocument();
    await expectActiveLink(/^For Entry/);
  });

  it('aging: the reimbursement aging table', async () => {
    renderSection('/finance/expenses/aging');
    expect(await screen.findByText('ZZ Aging Person')).toBeInTheDocument();
    await expectActiveLink(/^Aging/);
  });

  it('duplicates: the duplicate-bill watch-list', async () => {
    renderSection('/finance/expenses/duplicates');
    expect(await screen.findByText('ZZ duplicated receipt')).toBeInTheDocument();
    expect(screen.getByText('Possible duplicate bills')).toBeInTheDocument();
    await expectActiveLink(/^Duplicates/);
  });

  it('advances: loads its own list on arrival', async () => {
    renderSection('/finance/expenses/advances');
    expect(await screen.findByText('ZZ camp bus fare')).toBeInTheDocument();
    expect(api.getAdvances).toHaveBeenCalledWith({ store_id: 'ZZ-STORE' });
    await expectActiveLink(/^Advances$/);
  });

  it('float: loads the store float on arrival', async () => {
    renderSection('/finance/expenses/float');
    expect(await screen.findByText('ZZ opening float')).toBeInTheDocument();
    expect(screen.getByText('Float balance')).toBeInTheDocument();
    expect(api.getPettyCashBalance).toHaveBeenCalledWith('ZZ-STORE');
    await expectActiveLink(/^Petty Cash Float$/);
  });

  it('settle: loads the day position on arrival', async () => {
    renderSection('/finance/expenses/settle');
    expect(await screen.findByText('not yet settled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Count & settle/ })).toBeInTheDocument();
    expect(api.getPettyCashSettlementPosition).toHaveBeenCalledWith('ZZ-STORE', expect.any(String));
    await expectActiveLink(/^Day Settlement$/);
  });

  it('summary: spending by category from the user\'s own expenses', async () => {
    renderSection('/finance/expenses/summary');
    expect(await screen.findByText('Spending by category')).toBeInTheDocument();
    await expectActiveLink(/^Category Summary$/);
  });
});

// The JSX gates are now route gates. Drive the REAL route table.
function renderRoute(path: string, roles: string[]) {
  currentRoles = roles;
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={null}>
        <Routes>
          {financeRoutes}
          <Route path="/unauthorized" element={<div>ZZ-DENIED</div>} />
        </Routes>
      </Suspense>
    </MemoryRouter>,
  );
}

describe('each section keeps its old role gate, now on the route', () => {
  it.each(['approvals', 'entry', 'aging', 'duplicates', 'float', 'settle'])(
    'SALES_STAFF is refused /finance/expenses/%s',
    async (section) => {
      renderRoute(`/finance/expenses/${section}`, ['SALES_STAFF']);
      expect(await screen.findByText('ZZ-DENIED')).toBeInTheDocument();
    },
  );

  it.each([
    ['advances', 'ZZ camp bus fare'],
    ['summary', 'Spending by category'],
  ])('SALES_STAFF can open the ungated /finance/expenses/%s', async (section, text) => {
    renderRoute(`/finance/expenses/${section}`, ['SALES_STAFF']);
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it('STORE_MANAGER approves and holds the float but is not the accountant', async () => {
    const { unmount } = renderRoute('/finance/expenses/entry', ['STORE_MANAGER']);
    expect(await screen.findByText('ZZ-DENIED')).toBeInTheDocument();
    unmount();
    renderRoute('/finance/expenses/float', ['STORE_MANAGER']);
    expect(await screen.findByText('ZZ opening float')).toBeInTheDocument();
  });

  it('a legacy ?tab= link lands on that section', async () => {
    renderRoute('/finance/expenses?tab=approvals', ['ADMIN']);
    expect(await screen.findByText('ZZ lens cloths')).toBeInTheDocument();
  });
});

describe('legacyTabTarget', () => {
  it('maps every former tab to its section URL; my and unknown land on the index', () => {
    for (const s of ['approvals', 'entry', 'aging', 'duplicates', 'advances', 'float', 'settle', 'summary']) {
      expect(legacyTabTarget(`?tab=${s}`)).toBe(`/finance/expenses/${s}`);
    }
    expect(legacyTabTarget('?tab=my')).toBe('/finance/expenses');
    expect(legacyTabTarget('?tab=nonsense')).toBe('/finance/expenses');
  });

  it('carries every other query param through', () => {
    expect(legacyTabTarget('?tab=settle&date=2026-09-01')).toBe('/finance/expenses/settle?date=2026-09-01');
  });
});
