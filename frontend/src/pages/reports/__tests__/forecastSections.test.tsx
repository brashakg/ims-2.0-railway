// ============================================================================
// Wave 6 B13 - the three Demand Forecast panels, one URL each
// ============================================================================
// DemandForecast kept category / seasonal / reorder behind one URL in
// useState. Each is a child route of /reports/forecast now. Every test drives
// the REAL reportRoutes table under the REAL ReportsLayout, so the URL-to-panel
// wiring under test is the one the app ships: a panel wired to the wrong URL,
// or one that lost its <Outlet context> data, renders the wrong text and fails
// here. Then the same table's gates: every panel admits exactly the roles the
// one-URL page admitted.

import { Suspense, Children, isValidElement, type ReactElement, type ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// jsdom has no requestIdleCallback, so the layout's chunk-warming would fall
// back to a 1.5s setTimeout that can fire after teardown. Not under test.
vi.stubGlobal('requestIdleCallback', () => 0);

let currentRoles: string[] = [];

vi.mock('../../../context/AuthContext', () => ({
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

// The layout's KPI strip and the forecast page's Expense-vs-Revenue card are
// React Query hooks over the reports API. Not under test; they idle.
vi.mock('../reportsQueries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../reportsQueries')>()),
  useSalesSummary: () => ({ data: undefined, isPending: false, isError: false }),
  useSalesGrowth: () => ({ data: undefined }),
  useExpenseVsRevenue: () => ({ data: undefined, isPending: false }),
}));

vi.mock('../../../services/api/analytics', () => ({
  analyticsV2Api: { getDemandForecast: vi.fn() },
}));

import { analyticsV2Api } from '../../../services/api/analytics';
import { ToastProvider } from '../../../context/ToastContext';
import { reportRoutes } from '../../../routes/reportRoutes';

const getDemandForecast = analyticsV2Api.getDemandForecast as unknown as ReturnType<typeof vi.fn>;

// reportRoutes lazy-loads the layout, the page and the panels, and those
// chunks compile on demand under vitest - slow on a loaded machine. Same
// allowance as the other real-route tests (expensesSections, clinicalRoutesSplit).
const FIND = { timeout: 20000 };
vi.setConfig({ testTimeout: 30000 });

// One product selling 3 a day with 10 on the shelf: ~3 days of stock and an
// 80-unit reorder, so the reorder panel shows an Urgent Reorder card and the
// category panel one 'ZZ Frames' row.
beforeEach(() => {
  getDemandForecast.mockResolvedValue({
    forecasts: [{
      product_id: 'ZZ-P1', product_name: 'ZZ Aviator', brand: 'ZZ', category: 'ZZ Frames',
      avg_daily_sales: 3, trend: 'increasing', predicted_30_day: 90, current_stock: 10,
      reorder_recommended: 80,
    }],
  });
});

// The REAL route table - the URL-to-panel mapping and gates the app ships
// (reportRoutes.tsx), never a hand-copied one.
function renderRoute(path: string, roles: string[]) {
  currentRoles = roles;
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Suspense fallback={null}>
            <Routes>
              {reportRoutes}
              <Route path="/unauthorized" element={<div>ZZ-DENIED</div>} />
            </Routes>
          </Suspense>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const renderPanel = (path: string) => renderRoute(path, ['SUPERADMIN']);

/** The panel's own nav link is the active one - one URL per panel. */
async function expectActivePanelLink(name: RegExp) {
  expect(await screen.findByRole('link', { name }, FIND)).toHaveAttribute('aria-current', 'page');
}

describe('each forecast panel renders at its own URL', () => {
  it('category (index): the per-category table and the 30/60/90 range picker', async () => {
    renderPanel('/reports/forecast');
    expect(await screen.findByText('ZZ Frames', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '60 Days' })).toBeInTheDocument();
    await expectActivePanelLink(/^Category Forecast$/);
  });

  it('seasonal: the four season cards, and no range picker', async () => {
    renderPanel('/reports/forecast/seasonal');
    expect(await screen.findByText('Monsoon', undefined, FIND)).toBeInTheDocument();
    expect(screen.getAllByText('Products to Stock')).toHaveLength(4);
    expect(screen.queryByRole('button', { name: '60 Days' })).not.toBeInTheDocument();
    await expectActivePanelLink(/^Seasonal Trends$/);
  });

  it('reorder: the per-product suggestions', async () => {
    renderPanel('/reports/forecast/reorder');
    expect(await screen.findByText('ZZ Aviator', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByText('Urgent Reorder')).toBeInTheDocument();
    await expectActivePanelLink(/^Reorder Suggestions/);
  });

  it('the Reports section nav keeps Forecast lit on a panel URL', async () => {
    renderPanel('/reports/forecast/reorder');
    await screen.findByText('ZZ Aviator', undefined, FIND);
    expect(screen.getByRole('button', { name: 'Forecast' })).toHaveClass('on');
  });

  it('a legacy /reports?tab=forecast link lands on the category panel', async () => {
    renderPanel('/reports?tab=forecast');
    expect(await screen.findByText('ZZ Frames', undefined, FIND)).toBeInTheDocument();
    await expectActivePanelLink(/^Category Forecast$/);
  });
});

// Same door as /reports/forecast on every panel: the route admits the Reports
// roles, and inside it the shell still shows everyone but SUPERADMIN the
// Superadmin-only card (the panel never mounts, the forecast is never fetched).
// Nothing widened, nothing narrowed.
describe('each panel keeps the roles /reports/forecast had', () => {
  it.each(['', '/seasonal', '/reorder'])(
    'ACCOUNTANT opens /reports/forecast%s and sees the Superadmin-only card',
    async (sub) => {
      renderRoute(`/reports/forecast${sub}`, ['ACCOUNTANT']);
      expect(await screen.findByText(/available to Superadmin only/, undefined, FIND)).toBeInTheDocument();
      expect(screen.queryAllByText('Products to Stock')).toHaveLength(0);
      expect(getDemandForecast).not.toHaveBeenCalled();
    },
  );

  it.each(['', '/seasonal', '/reorder'])(
    'SALES_STAFF is refused /reports/forecast%s',
    async (sub) => {
      renderRoute(`/reports/forecast${sub}`, ['SALES_STAFF']);
      expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
    },
  );
});

describe('an unknown forecast sub-address', () => {
  it('goes back to the forecast page (category panel), not an empty shell', async () => {
    renderPanel('/reports/forecast/anything');
    expect(await screen.findByText('ZZ Frames', undefined, FIND)).toBeInTheDocument();
    await expectActivePanelLink(/^Category Forecast$/);
  });
});

// The render tests above cannot see the forecast gate: the `reports` layout
// gate carries the same role list and refuses first. So the forecast gate is
// pinned here, straight off the shipped route table.
describe('the forecast route gate, read off the real route table', () => {
  const childrenOf = (el: ReactElement): ReactElement[] =>
    Children.toArray((el.props as { children?: ReactNode }).children).filter(isValidElement) as ReactElement[];
  const routePath = (el: ReactElement) => (el.props as { path?: string }).path;

  it('forecast is gated to exactly the roles the one-URL page admitted', () => {
    const top = childrenOf(reportRoutes as ReactElement);
    const reports = top.find((r) => routePath(r) === 'reports');
    expect(reports).toBeDefined();
    const forecast = childrenOf(reports!).find((r) => routePath(r) === 'forecast');
    expect(forecast).toBeDefined();
    const gate = (forecast!.props as { element: ReactElement }).element;
    expect((gate.props as { allowedRoles?: string[] }).allowedRoles).toEqual(
      ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'],
    );
  });
});
