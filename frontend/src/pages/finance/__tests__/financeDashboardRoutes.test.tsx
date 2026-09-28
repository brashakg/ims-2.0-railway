// ============================================================================
// Wave 6 finance split: the dashboard sections are real routes
// ============================================================================
// Mounts the REAL financeRoutes (real FinanceLayout, real section pages, real
// ProtectedRoute) with only the API, auth and toast mocked, inside a wrapper
// that does what AppLayout does: key the page on its pathname. That key is
// the whole reason for the first test -- AppLayout REMOUNTS the page on every
// pathname change, so a date range held in the layout's useState snapped back
// to the defaults on every section click. The range lives in the URL instead.

import { Suspense } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route, Outlet, useLocation } from 'react-router-dom';

const CURRENT = { roles: ['ACCOUNTANT'] as string[] };

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    isLoading: false,
    user: { user_id: 'u1', roles: CURRENT.roles, activeRole: CURRENT.roles[0], activeStoreId: 'BV-BOK-01' },
    hasRole: (role: string | string[]) =>
      (Array.isArray(role) ? role : [role]).some((r) => CURRENT.roles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

vi.mock('../../../services/api/finance', () => ({
  financeApi: {
    getRevenue: vi.fn(async () => ({ total_revenue: 300000 })),
    getPnl: vi.fn(async () => ({ revenue: 300000, total_expenses: 24450 })),
    getGstSummary: vi.fn(async () => ({ cgst: 100, sgst: 100, gst_collected: 200 })),
    getOutstanding: vi.fn(async () => ({ items: [] })),
    getVendorPayments: vi.fn(async () => []),
    getPeriodStatus: vi.fn(async () => ({ locked: false })),
    getPnlByStore: vi.fn(async () => ({ stores: [] })),
    getPnlByCategory: vi.fn(async () => ({ categories: [] })),
    getGstReconciliation: vi.fn(async () => ({ entities: [] })),
    listJournalEntries: vi.fn(async () => ({ journal_entries: [] })),
    getChartOfAccounts: vi.fn(async () => ({ accounts: [] })),
  },
}));

// The two standalone pages the deleted tabs now point at -> sentinels. What is
// under test is that the dashboard sends people there, not those pages.
vi.mock('../CashFlowPage', () => ({ default: () => <div>CASH-FLOW-PAGE-SENTINEL</div> }));
vi.mock('../BudgetingPage', () => ({ default: () => <div>BUDGETING-PAGE-SENTINEL</div> }));

import { financeRoutes } from '../../../routes/financeRoutes';
import { financeApi } from '../../../services/api/finance';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const SLOW = 20000;

// AppLayout.tsx: <div key={location.pathname} className="ims-anim-page"><Outlet /></div>
function KeyedLikeAppLayout() {
  const { pathname } = useLocation();
  return (
    <div key={pathname}>
      <Outlet />
    </div>
  );
}

function renderAt(path: string, roles: string[] = ['ACCOUNTANT']) {
  CURRENT.roles = roles;
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={<div>chunk-loading</div>}>
        <Routes>
          <Route path="/" element={<KeyedLikeAppLayout />}>
            {financeRoutes}
          </Route>
          <Route path="/unauthorized" element={<div>DENIED-SENTINEL</div>} />
        </Routes>
      </Suspense>
    </MemoryRouter>,
  );
}

const dateInputs = () =>
  Array.from(document.querySelectorAll<HTMLInputElement>('input[type="date"]'));

describe('the FY / date range survives section changes', () => {
  beforeEach(() => vi.clearAllMocks());

  it('keeps a changed From date when moving to another section', async () => {
    renderAt('/finance/dashboard');
    await screen.findByText('Total Revenue', {}, { timeout: SLOW });

    fireEvent.change(dateInputs()[0], { target: { value: '2026-05-01' } });
    await waitFor(() => expect(dateInputs()[0]?.value).toBe('2026-05-01'), { timeout: SLOW });

    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /gst management/i }));
    await screen.findByText('GST Breakdown', {}, { timeout: SLOW });

    // The bar still shows the chosen date, and the reload after the remount
    // asked the API for that range, not the FY-start default.
    expect(dateInputs()[0].value).toBe('2026-05-01');
    expect(api.getPnl).toHaveBeenLastCalledWith(
      expect.objectContaining({ from_date: '2026-05-01' }),
    );
  }, SLOW * 2);
});

// ===========================================================================
// OWNER RULING 2026-09-27, one door: the dashboard's own Cash Flow and Budgets
// tabs are deleted. The standalone pages are the only doors, and every old way
// in (the header, a bookmarked ?tab=) lands there.
// ===========================================================================
describe('cash flow and budgets have one door each', () => {
  beforeEach(() => vi.clearAllMocks());

  it('has no Cash Flow or Budgets tab in the section nav', async () => {
    renderAt('/finance/dashboard');
    await screen.findByText('Total Revenue', {}, { timeout: SLOW });
    // Positive control: the nav really rendered.
    expect(screen.getByRole('button', { name: /gst management/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^cash flow$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^budgets$/i })).not.toBeInTheDocument();
  }, SLOW);

  it('the header Cash flow link opens the standalone page for a finance admin', async () => {
    renderAt('/finance/dashboard', ['ACCOUNTANT']);
    const link = await screen.findByRole('link', { name: /cash flow/i }, { timeout: SLOW });
    expect(link).toHaveAttribute('href', '/finance/cash-flow');
    await userEvent.setup().click(link);
    expect(await screen.findByText('CASH-FLOW-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW * 2);

  it('hides the Cash flow link from a store manager (the page would refuse them)', async () => {
    renderAt('/finance/dashboard', ['STORE_MANAGER']);
    await screen.findByText('Total Revenue', {}, { timeout: SLOW });
    expect(screen.queryByRole('link', { name: /cash flow/i })).not.toBeInTheDocument();
    // ...while Budgets, whose page admits every dashboard role, stays.
    expect(screen.getByRole('link', { name: /^budgets$/i })).toHaveAttribute('href', '/finance/budgeting');
  }, SLOW);

  it('forwards a bookmarked ?tab=cash-flow to the standalone cash-flow page', async () => {
    renderAt('/finance/dashboard?tab=cash-flow', ['ADMIN']);
    expect(await screen.findByText('CASH-FLOW-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('forwards a bookmarked ?tab=budgets to the standalone budgeting page', async () => {
    renderAt('/finance/dashboard?tab=budgets', ['STORE_MANAGER']);
    expect(await screen.findByText('BUDGETING-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);
});

// ===========================================================================
// One smoke test per section: its own URL renders its own panel, inside the
// layout, with its own tab highlighted -- and not the index's panel.
// ===========================================================================
describe('every dashboard section has its own URL', () => {
  beforeEach(() => vi.clearAllMocks());

  const SECTIONS: Array<[path: string, tab: RegExp, text: string]> = [
    ['/finance/dashboard', /revenue & p&l/i, 'Total Revenue'],
    ['/finance/dashboard/gst', /gst management/i, 'GST Breakdown'],
    ['/finance/dashboard/outstanding', /outstanding & collections/i, 'Outstanding Receivables'],
    ['/finance/dashboard/period', /period management/i, 'Financial Period Management'],
    ['/finance/dashboard/vendor-payments', /vendor payments/i, 'Total Payable'],
    ['/finance/dashboard/journal-entries', /journal entries/i, 'No journal entries.'],
  ];

  it.each(SECTIONS)('%s renders its section inside the layout', async (path, tab, text) => {
    renderAt(path);
    expect(await screen.findByText(text, {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /the books, in real time/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: tab }).className).toContain('border-blue-400');
    if (path !== '/finance/dashboard') {
      expect(screen.queryByText('Total Revenue')).not.toBeInTheDocument();
    }
  }, SLOW);

  it('forwards a legacy ?tab=gst to /finance/dashboard/gst, keeping the other params', async () => {
    renderAt('/finance/dashboard?tab=gst&from=2026-05-01');
    expect(await screen.findByText('GST Breakdown', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(dateInputs()[0].value).toBe('2026-05-01');
  }, SLOW);
});
