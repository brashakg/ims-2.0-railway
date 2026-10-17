// ============================================================================
// IMS 2.0 - the GST summary and GSTR-3B are the accounts roles' (R1)
// ============================================================================
// Owner ruling 2026-10-07 (R1): the GST summary and the GSTR-3B report show
// input-tax totals summed from supplier bills -- managers do NOT need them;
// ONLY SUPERADMIN, ADMIN and ACCOUNTANT see them (COST_ROLES; the server
// answers a manager 403 on both). So a manager gets no Finance GST tab and no
// summary call, no GSTR-3B menu entry, and /reports/gstr3b is the app's
// not-allowed page. GSTR-1 now follows the same approved Accounts-only rule.
// A routed test, because
// the component tests render GSTPanel / GSTR3BReport directly and stay green
// whatever a route, a tab or a menu entry admits.

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

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

let roles: string[] = ['STORE_MANAGER'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-1', roles, activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    isAuthenticated: true,
    isLoading: false,
    hasRole: (want: string | string[]) =>
      roles.includes('ADMIN') || roles.includes('SUPERADMIN') ||
      (Array.isArray(want) ? want : [want]).some((r) => roles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));
// The dashboard's Shop picker / shop names read the store list (react-query).
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));
vi.mock('../../reports/GSTR3BPage', () => ({ GSTR3BPage: () => <div>GSTR-3B PAGE</div> }));
vi.mock('../../reports/GSTR1Page', () => ({ GSTR1Page: () => <div>GSTR-1 PAGE</div> }));

import FinanceDashboard from '../FinanceDashboard';
import { financeApi } from '../../../services/api/finance';
import { reportRoutes } from '../../../routes/reportRoutes';
import { filterVisibleGroups } from '../../../components/shell/navConfig';
import type { UserRole } from '../../../types';

const api = financeApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

beforeEach(() => {
  vi.clearAllMocks();
  api.getRevenue.mockResolvedValue({ total_revenue: 0 });
  api.getPnl.mockResolvedValue({ revenue: 0, expenses: {} });
  api.getGstSummary.mockResolvedValue({ cgst: 0, sgst: 0, gst_input_credit: 4321, net_gst_payable: 0 });
  api.getOutstanding.mockResolvedValue([]);
  api.getCashFlow.mockResolvedValue({});
  api.getBudget.mockResolvedValue({ categories: {} });
  api.getVendorPayments.mockResolvedValue([]);
  api.getPeriodStatus.mockResolvedValue({ locked: false });
  api.getPnlByStore.mockResolvedValue({ stores: [] });
  api.getPnlByCategory.mockResolvedValue({ categories: [] });
  api.getGstReconciliation.mockResolvedValue({ entities: [] });
});

function openReport(path: string, role: string) {
  roles = [role];
  render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={null}>
        <Routes>
          {reportRoutes}
          <Route path="/unauthorized" element={<div>UNAUTHORIZED</div>} />
        </Routes>
      </Suspense>
    </MemoryRouter>,
  );
}

const navIds = (role: string) =>
  filterVisibleGroups([role as UserRole], role as UserRole, () => true).flatMap((g) => g.items.map((i) => i.id));

describe.each([['STORE_MANAGER'], ['AREA_MANAGER']])('%s: no GST summary, no GSTR-3B', (role) => {
  it('Finance dashboard: no GST tab, no summary call, no reconciliation call', async () => {
    roles = [role];
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    await waitFor(() => expect(api.getPnlByCategory).toHaveBeenCalled());
    // The dashboard drew its tabs, without the GST one.
    expect(screen.getByRole('button', { name: /revenue & p&l/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /gst management/i })).toBeNull();
    expect(api.getGstSummary).not.toHaveBeenCalled();
    expect(api.getGstReconciliation).not.toHaveBeenCalled();
  });

  it('GSTR-3B: no menu entry, and the page is the not-allowed page', async () => {
    expect(navIds(role)).not.toContain('gstr3b');
    expect(navIds(role)).not.toContain('gstr1');
    openReport('/reports/gstr3b', role);
    expect(await screen.findByText('UNAUTHORIZED')).toBeInTheDocument();
    expect(screen.queryByText('GSTR-3B PAGE')).toBeNull();
  });

  it('GSTR-1 is also restricted to Accounts and administrators', async () => {
    openReport('/reports/gstr1', role);
    expect(await screen.findByText('UNAUTHORIZED')).toBeInTheDocument();
    expect(screen.queryByText('GSTR-1 PAGE')).toBeNull();
  });
});

describe.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])('%s: keeps the input tax', (role) => {
  it('Finance dashboard: the GST tab and its summary', async () => {
    roles = [role];
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getGstSummary).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: /gst management/i })).toBeInTheDocument();
  });

  // Round 4: the cards say whose figures they are -- the server's one shop
  // rule (resolve_store_scope) gives an admin every shop and an accountant
  // his own shop, on the cards and the reconciliation table alike.
  it('the GST cards say which shops they cover', async () => {
    roles = [role];
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getGstSummary).toHaveBeenCalled());
    await userEvent.setup().click(screen.getByRole('button', { name: /gst management/i }));
    const admin = role !== 'ACCOUNTANT';
    expect(await screen.findByText(admin ? /All shops · current month/ : /Your shop: .*current month/)).toBeInTheDocument();
    expect(screen.queryByText(admin ? /Your shop:/ : /All shops/)).toBeNull();
  });

  it('GSTR-3B: menu entry and page', async () => {
    expect(navIds(role)).toContain('gstr3b');
    openReport('/reports/gstr3b', role);
    expect(await screen.findByText('GSTR-3B PAGE')).toBeInTheDocument();
  });

  it('GSTR-1: menu entry and page', async () => {
    expect(navIds(role)).toContain('gstr1');
    openReport('/reports/gstr1', role);
    expect(await screen.findByText('GSTR-1 PAGE')).toBeInTheDocument();
  });
});
