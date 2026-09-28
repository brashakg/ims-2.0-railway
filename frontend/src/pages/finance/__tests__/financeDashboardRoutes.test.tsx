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
    getCashFlow: vi.fn(async () => ({ inflows: 1000, outflows: 500 })),
    getBudget: vi.fn(async () => ({ categories: {} })),
    getVendorPayments: vi.fn(async () => []),
    getPeriodStatus: vi.fn(async () => ({ locked: false })),
    getPnlByStore: vi.fn(async () => ({ stores: [] })),
    getPnlByCategory: vi.fn(async () => ({ categories: [] })),
    getGstReconciliation: vi.fn(async () => ({ entities: [] })),
    listJournalEntries: vi.fn(async () => ({ journal_entries: [] })),
    getChartOfAccounts: vi.fn(async () => ({ accounts: [] })),
  },
}));

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
