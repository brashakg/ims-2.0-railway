// ============================================================================
// IMS 2.0 - Finance Dashboard > Budgets shows the plan Budgeting saved
// ============================================================================
// Audit L3-15. Budgeting saved a November revenue target of 10,000 and rent
// 1,000; the dashboard's Budgets tab said total budget 0. The server reader
// now builds from the same rows Budgeting writes (backend:
// test_audit_b12_budget_till_pettycash.py). What is proved HERE is the screen
// half: the dashboard asks for the SAME shop it reads everything else for, and
// the panel shows the figures and the month they belong to.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-sm', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: 'BV-DHN-01' },
  }),
}));
const fin = vi.hoisted(() => ({
  getRevenue: vi.fn(), getPnl: vi.fn(), getGstSummary: vi.fn(), getOutstanding: vi.fn(), getCashFlow: vi.fn(),
  getBudget: vi.fn(), getVendorPayments: vi.fn(), getPeriodStatus: vi.fn(), getPnlByStore: vi.fn(),
  getPnlByCategory: vi.fn(), getGstReconciliation: vi.fn(),
}));
vi.mock('../../../services/api/finance', () => ({ financeApi: fin }));
vi.mock('../../../services/api/vendorAp', () => ({
  cashFlowApi: { ownerDashboard: vi.fn(), forecast: vi.fn() }, vendorApApi: { apAging: vi.fn() },
}));
vi.mock('../../../services/api/cashReconciliation', () => ({ cashReconciliationApi: { summary: vi.fn(), signoff: vi.fn() } }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: vi.fn(async () => ({ stores: [] })) } }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));

import { ToastProvider } from '../../../context/ToastContext';
import FinanceDashboard from '../FinanceDashboard';
import BudgetPanel from '../BudgetPanel';

// What GET /finance/budget answers once Budgeting has saved the audit's plan.
const SAVED_PLAN = {
  month: 11, year: 2026, mode: 'full', store_id: 'BV-DHN-01', period: '2026-11',
  categories: { rent: { budget: 1000, actual: 400 } },
  revenue: { budget: 10000, actual: 2500 },
};

beforeEach(() => {
  for (const f of Object.values(fin)) f.mockResolvedValue({});
  fin.getBudget.mockResolvedValue(SAVED_PLAN);
});

describe('Finance Dashboard - Budgets tab', () => {
  it('asks for the budget of the SAME shop as the rest of the dashboard', async () => {
    render(<ToastProvider><FinanceDashboard /></ToastProvider>);
    await waitFor(() => expect(fin.getBudget).toHaveBeenCalled());
    await waitFor(() => expect(fin.getCashFlow).toHaveBeenCalled());

    const shop = fin.getCashFlow.mock.calls[0][0].store_id;
    expect(shop).toBe('BV-DHN-01');
    // It used to be called with no argument at all: no shop, any shop.
    expect(fin.getBudget.mock.calls[0][0]).toEqual({ store_id: shop });
  });

  it('shows the saved rent plan, the revenue target and the month', async () => {
    render(<ToastProvider><FinanceDashboard /></ToastProvider>);
    await waitFor(() => expect(fin.getBudget).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole('button', { name: /budgets/i }));

    // Total Budget = the expense heads (rent 1,000) -- it read 0.
    const total = await screen.findByText('Total Budget');
    expect(total.parentElement!.textContent).toMatch(/1,000/);
    expect(screen.getByTestId('budget-revenue-target').textContent).toMatch(/10,000/);
    expect(screen.getByTestId('budget-revenue-target').textContent).toMatch(/2,500/);
    expect(screen.getByText(/November 2026/)).toBeInTheDocument();
  });

  it('a FAILED budget read says so, never "Total Budget Rs 0", and recovers on retry', async () => {
    // Every load fails (the dashboard may load more than once on mount).
    fin.getBudget.mockRejectedValue(new Error('503'));
    render(<ToastProvider><FinanceDashboard /></ToastProvider>);
    await waitFor(() => expect(fin.getBudget).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole('button', { name: /budgets/i }));

    expect((await screen.findByTestId('budget-unreadable')).textContent).toMatch(/Could not load the budget/);
    expect(screen.queryByText('Total Budget')).not.toBeInTheDocument();

    fin.getBudget.mockResolvedValue(SAVED_PLAN);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    const total = await screen.findByText('Total Budget');
    expect(total.parentElement!.textContent).toMatch(/1,000/);
    expect(screen.queryByTestId('budget-unreadable')).not.toBeInTheDocument();
  });
});

describe('BudgetPanel', () => {
  const ROW = {
    category: 'rent', allocated: 1000, spent: 400, remaining: 600, variance: 600, variance_percent: 60,
  };

  it('keeps the income target out of the spend totals', () => {
    render(
      <BudgetPanel
        budgets={[ROW]}
        selectedYear="2026-27"
        period="2026-11"
        revenue={{ target: 10000, actual: 0 }}
      />,
    );
    // 1,000, not 11,000: a revenue target is not money allocated to spend.
    expect(screen.getByText('Total Budget').parentElement!.textContent).toMatch(/1,000/);
    expect(screen.getByText('Total Budget').parentElement!.textContent).not.toMatch(/11,000/);
  });

  it('says nothing about revenue when no target is set and none was earned', () => {
    render(<BudgetPanel budgets={[ROW]} selectedYear="2026-27" period="2026-11" revenue={{ target: 0, actual: 0 }} />);
    expect(screen.queryByTestId('budget-revenue-target')).not.toBeInTheDocument();
  });

  it('falls back to the financial year when the server names no month', () => {
    render(<BudgetPanel budgets={[ROW]} selectedYear="2026-27" />);
    expect(screen.getByText(/FY 2026-27/)).toBeInTheDocument();
  });
});
