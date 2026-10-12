// ============================================================================
// IMS 2.0 - managers keep OUTPUT GST; input tax is the accounts roles' (R5)
// ============================================================================
// Owner ruling 2026-10-12 (R5): GST INPUT tax (from supplier bills) on the GST
// summary and GSTR-3B (Table 4 ITC, 3.1(d) RCM) and its export is the accounts
// roles'; managers KEEP output GST. The server leaves the input keys out of a
// manager's summary and return (finance/gst._output_side_only,
// reports/gstr3b._output_side_only) -- it does not lock managers out. So the
// screens must not either: a manager still gets the Finance GST tab and its
// summary call, the GSTR-3B menu entry and the GSTR-3B page (which draws no
// Table 4 for him: GSTR3BReport.inputTax.test). A routed test, because the
// component tests render GSTPanel / GSTR3BReport directly and stay green
// even when a route, a tab or a menu entry shuts a manager out.

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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

describe.each([['STORE_MANAGER'], ['AREA_MANAGER']])('%s: keeps output GST', (role) => {
  it('Finance dashboard: the GST tab and its summary call; no org-wide reconciliation', async () => {
    roles = [role];
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    await waitFor(() => expect(api.getGstSummary).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: /gst management/i })).toBeInTheDocument();
    // /finance/gst/reconciliation is accounts-only on the server.
    await waitFor(() => expect(api.getPnlByCategory).toHaveBeenCalled());
    expect(api.getGstReconciliation).not.toHaveBeenCalled();
  });

  it('GSTR-3B: menu entry and page (its Table 4 is left out by the server)', async () => {
    expect(navIds(role)).toContain('gstr3b');
    expect(navIds(role)).toContain('gstr1');
    openReport('/reports/gstr3b', role);
    expect(await screen.findByText('GSTR-3B PAGE')).toBeInTheDocument();
  });

  it('GSTR-1 (sales GST only) still opens', async () => {
    openReport('/reports/gstr1', role);
    expect(await screen.findByText('GSTR-1 PAGE')).toBeInTheDocument();
  });
});

describe.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])('%s: keeps the input tax', (role) => {
  it('Finance dashboard: the GST tab and its summary', async () => {
    roles = [role];
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getGstSummary).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: /gst management/i })).toBeInTheDocument();
  });

  it('GSTR-3B: menu entry and page', async () => {
    expect(navIds(role)).toContain('gstr3b');
    openReport('/reports/gstr3b', role);
    expect(await screen.findByText('GSTR-3B PAGE')).toBeInTheDocument();
  });
});
