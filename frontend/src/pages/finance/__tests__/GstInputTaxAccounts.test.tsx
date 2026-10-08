// ============================================================================
// IMS 2.0 - input tax from supplier bills is the accounts roles' (R1)
// ============================================================================
// Owner ruling 2026-10-07: the GST summary and the GSTR-3B report show input
// tax summed from supplier bills; managers do not need them, only SUPERADMIN /
// ADMIN / ACCOUNTANT see them. The server now refuses a manager both; the
// screens follow the same list (PAYABLES_ROLES): no GST tab on the Finance
// dashboard and no call behind it, no GSTR-3B menu entry, no GSTR-3B page.
// GSTR-1 (sales GST only) stays the managers'.

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

describe.each([['STORE_MANAGER'], ['AREA_MANAGER']])('%s: no input tax anywhere', (role) => {
  it('Finance dashboard: no GST summary call, no GST tab', async () => {
    roles = [role];
    render(<FinanceDashboard />);
    await waitFor(() => expect(api.getPnl).toHaveBeenCalled());
    await waitFor(() => expect(api.getPnlByCategory).toHaveBeenCalled());
    expect(api.getGstSummary).not.toHaveBeenCalled();
    expect(api.getGstReconciliation).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /gst management/i })).toBeNull();
  });

  it('GSTR-3B: no menu entry and the page bounces; GSTR-1 still opens', async () => {
    expect(navIds(role)).not.toContain('gstr3b');
    expect(navIds(role)).toContain('gstr1');
    openReport('/reports/gstr3b', role);
    expect(await screen.findByText('UNAUTHORIZED')).toBeInTheDocument();
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
