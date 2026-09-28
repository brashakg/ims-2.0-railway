// ============================================================================
// Wave 6 Expenses split - one smoke test per section, the route gates, and the
// legacy ?tab= mapper
// ============================================================================
// The old ExpenseTracker held nine role-gated tabs behind one URL and had no
// test at all. Each tab is now its own URL under ExpensesLayout, fed by the
// layout's one expenses load through <Outlet context>. Every test drives the
// REAL financeRoutes table, so the URL-to-section mapping under test is the one
// the app ships (a section that lost its data wiring, or a URL wired to the
// wrong section, renders the wrong text and fails here), then the same table's
// gates (each section's allowedRoles must be exactly its old JSX gate).

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
      settlePettyCashDay: vi.fn(),
    },
  };
});

import { expensesApi } from '../../../../services/api/expenses';
import { ToastProvider } from '../../../../context/ToastContext';
import { legacyTabTarget } from '../legacyTabRedirect';
import { financeRoutes } from '../../../../routes/financeRoutes';

const api = expensesApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

// financeRoutes lazy-loads the layout and every section, and those chunks
// compile on demand under vitest - slow on a loaded machine. Same allowance as
// the other real-route tests (clinicalRoutesSplit, customersRecallsRoute).
const FIND = { timeout: 20000 };
vi.setConfig({ testTimeout: 30000 });

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

// The REAL route table - the same URL-to-section mapping and gates the app
// ships (financeRoutes.tsx), never a hand-copied one - under the REAL
// ToastProvider, whose value changes identity on every toast (the layout's
// load lists `toast`, so every toast re-runs it, as in the app).
function renderRoute(path: string, roles: string[]) {
  currentRoles = roles;
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[path]}>
        <Suspense fallback={null}>
          <Routes>
            {financeRoutes}
            <Route path="/unauthorized" element={<div>ZZ-DENIED</div>} />
          </Routes>
        </Suspense>
      </MemoryRouter>
    </ToastProvider>,
  );
}

const renderSection = (path: string) => renderRoute(path, ['ADMIN']);

/** The section's own nav link is the active one - one URL per section. */
async function expectActiveLink(name: RegExp) {
  expect(await screen.findByRole('link', { name }, FIND)).toHaveAttribute('aria-current', 'page');
}

describe('each expenses section renders at its own URL', () => {
  it('my (index): the user\'s own expenses', async () => {
    renderSection('/finance/expenses');
    expect(await screen.findByText('ZZ bus fare to Ranchi', undefined, FIND)).toBeInTheDocument();
    await expectActiveLink(/^My Expenses$/);
  });

  it('approvals: the approval queue with Approve / Reject', async () => {
    renderSection('/finance/expenses/approvals');
    expect(await screen.findByText('ZZ lens cloths', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Approve/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reject/ })).toBeInTheDocument();
    await expectActiveLink(/^Pending Approval/);
  });

  it('entry: the ledger-entry queue', async () => {
    renderSection('/finance/expenses/entry');
    expect(await screen.findByText('ZZ courier charges', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Mark entered/ })).toBeInTheDocument();
    await expectActiveLink(/^For Entry/);
  });

  it('aging: the reimbursement aging table', async () => {
    renderSection('/finance/expenses/aging');
    expect(await screen.findByText('ZZ Aging Person', undefined, FIND)).toBeInTheDocument();
    await expectActiveLink(/^Aging/);
  });

  it('duplicates: the duplicate-bill watch-list', async () => {
    renderSection('/finance/expenses/duplicates');
    expect(await screen.findByText('ZZ duplicated receipt', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByText('Possible duplicate bills')).toBeInTheDocument();
    await expectActiveLink(/^Duplicates/);
  });

  it('advances: loads its own list on arrival', async () => {
    renderSection('/finance/expenses/advances');
    expect(await screen.findByText('ZZ camp bus fare', undefined, FIND)).toBeInTheDocument();
    expect(api.getAdvances).toHaveBeenCalledWith({ store_id: 'ZZ-STORE' });
    await expectActiveLink(/^Advances$/);
  });

  it('float: loads the store float on arrival', async () => {
    renderSection('/finance/expenses/float');
    expect(await screen.findByText('ZZ opening float', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByText('Float balance')).toBeInTheDocument();
    expect(api.getPettyCashBalance).toHaveBeenCalledWith('ZZ-STORE');
    await expectActiveLink(/^Petty Cash Float$/);
  });

  it('settle: loads the day position on arrival', async () => {
    renderSection('/finance/expenses/settle');
    expect(await screen.findByText('not yet settled', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Count & settle/ })).toBeInTheDocument();
    expect(api.getPettyCashSettlementPosition).toHaveBeenCalledWith('ZZ-STORE', expect.any(String));
    await expectActiveLink(/^Day Settlement$/);
  });

  it('summary: spending by category from the user\'s own expenses', async () => {
    renderSection('/finance/expenses/summary');
    expect(await screen.findByText('Spending by category', undefined, FIND)).toBeInTheDocument();
    await expectActiveLink(/^Category Summary$/);
  });
});

// The JSX gates are now route gates.
describe('each section keeps its old role gate, now on the route', () => {
  it.each(['approvals', 'entry', 'aging', 'duplicates', 'float', 'settle'])(
    'SALES_STAFF is refused /finance/expenses/%s',
    async (section) => {
      renderRoute(`/finance/expenses/${section}`, ['SALES_STAFF']);
      expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
    },
  );

  it.each([
    ['advances', 'ZZ camp bus fare'],
    ['summary', 'Spending by category'],
  ])('SALES_STAFF can open the ungated /finance/expenses/%s', async (section, text) => {
    renderRoute(`/finance/expenses/${section}`, ['SALES_STAFF']);
    expect(await screen.findByText(text, undefined, FIND)).toBeInTheDocument();
  });

  it('STORE_MANAGER approves and holds the float but is not the accountant', async () => {
    const { unmount } = renderRoute('/finance/expenses/entry', ['STORE_MANAGER']);
    expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
    unmount();
    renderRoute('/finance/expenses/float', ['STORE_MANAGER']);
    expect(await screen.findByText('ZZ opening float', undefined, FIND)).toBeInTheDocument();
  });

  it('a legacy ?tab= link lands on that section', async () => {
    renderRoute('/finance/expenses?tab=approvals', ['ADMIN']);
    expect(await screen.findByText('ZZ lens cloths', undefined, FIND)).toBeInTheDocument();
  });
});

// A toast re-runs the layout's load. For a user with no expenses of their own
// that reload used to swap the page for the spinner, which unmounted <Outlet/>
// and wiped the open section: Day Settlement snapped back to today, an open
// float modal vanished. The section must survive the reload.
describe('the open section survives a layout reload', () => {
  // A real network round-trip: the reload is in flight long enough to render.
  beforeEach(() => {
    api.getExpenses.mockImplementation(() => new Promise((r) => { setTimeout(() => r({ expenses: [] }), 150); }));
  });

  it('Day Settlement keeps the picked day after a settle', async () => {
    api.settlePettyCashDay.mockResolvedValue({ variance: 0, variance_status: 'BALANCED' });
    renderRoute('/finance/expenses/settle', ['STORE_MANAGER']);
    fireEvent.change(await screen.findByLabelText(/Settlement day/, undefined, FIND), { target: { value: '2025-01-15' } });
    fireEvent.click(await screen.findByRole('button', { name: /Count & settle/ }, FIND));
    fireEvent.change(screen.getByPlaceholderText('Count the box'), { target: { value: '3200' } });
    fireEvent.click(screen.getByRole('button', { name: 'Settle day' }));
    await waitFor(() => expect(api.getExpenses).toHaveBeenCalledTimes(2)); // the toast's reload
    await waitFor(() => expect(screen.getByLabelText(/Settlement day/)).toHaveValue('2025-01-15'));
    expect(api.getPettyCashSettlementPosition).toHaveBeenLastCalledWith('ZZ-STORE', '2025-01-15');
  });

  it('an open Top-up modal keeps its amount when another toast fires', async () => {
    renderRoute('/finance/expenses/float', ['STORE_MANAGER']);
    fireEvent.click(await screen.findByRole('button', { name: /Top up/ }, FIND));
    fireEvent.change(screen.getByPlaceholderText('e.g. 5000'), { target: { value: '900' } });
    // A toast from elsewhere on the page: submit the empty Add-expense form.
    fireEvent.click(screen.getByRole('button', { name: /Add expense/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    await waitFor(() => expect(api.getExpenses).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByPlaceholderText('e.g. 5000')).toHaveValue(900));
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

  it('an inherited Object key is unknown, not a URL built from Object.prototype', () => {
    for (const k of ['constructor', '__proto__', 'toString', 'hasOwnProperty']) {
      expect(legacyTabTarget(`?tab=${k}`)).toBe('/finance/expenses');
    }
  });

  it('carries every other query param through', () => {
    expect(legacyTabTarget('?tab=settle&date=2026-09-01')).toBe('/finance/expenses/settle?date=2026-09-01');
  });
});
