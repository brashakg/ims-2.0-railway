// ============================================================================
// Wave 6 B14 payroll split - one smoke test per section, the salary gate at
// every payroll address, and the HR nav's offer of those sections
// ============================================================================
// The old PayrollDashboard held three tabs behind one URL outside HRLayout
// and had no test. Each tab is now its own URL inside HRLayout, fed by
// PayrollLayout's one salary-sheet load through <Outlet context>. Every test
// drives the REAL hrRoutes table (the URL-to-section mapping and the gates
// the app ships), under the real HRLayout and the real ToastProvider.
//
// OWNER RULE: salary data is SUPERADMIN + ADMIN only. The gate tests below are
// the ones that must stay red if anyone widens SALARY_ROLES or drops the
// allowedRoles from the /hr/payroll route.

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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
    // Real AuthContext.hasRole semantics: SUPERADMIN/ADMIN pass every gate,
    // anyone else needs one of the listed roles.
    hasRole: (roles: string[]) =>
      currentRoles.includes('SUPERADMIN') || currentRoles.includes('ADMIN')
      || roles.some((r) => currentRoles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

// HRLayout's own reads (today's roster + leaves for its stat cards) and the
// check-in buttons' store lookup. None of it is under test; empty is fine.
vi.mock('../../../services/api', () => ({
  hrApi: {
    getAttendance: vi.fn(async () => ({ records: [] })),
    getLeaves: vi.fn(async () => ({ leaves: [] })),
    checkIn: vi.fn(),
    checkOut: vi.fn(),
  },
  storeApi: { getStore: vi.fn() },
}));

// The payroll reads go through the shared axios client directly (payrollApi
// in PayrollLayout). Routed by URL in beforeEach.
const get = vi.fn();
const post = vi.fn();
vi.mock('../../../services/api/client', () => ({
  default: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
  },
}));

import { ToastProvider } from '../../../context/ToastContext';
import { hrRoutes } from '../../../routes/hrRoutes';
import { SALARY_ROLES } from '../payroll/payrollShared';

// hrRoutes lazy-loads the layout and every section, and those chunks compile
// on demand under vitest - slow on a loaded machine. Same allowance as the
// other real-route tests (expensesSections, clinicalRoutesSplit).
const FIND = { timeout: 20000 };
vi.setConfig({ testTimeout: 30000 });

const SHEET = {
  salaries: [{
    salary_record_id: 'SR-1', employee_id: 'EMP-1', employee_name: 'ZZ Priya Sheet',
    month: 9, year: 2026, status: 'DRAFT',
    basic: 20000, hra: 8000, allowances: 2000, gross_salary: 30000, pf_employee: 1800,
    esi: 0, professional_tax: 200, tds: 0, lwp_deduction: 0, advance_deduction: 1000, net_pay: 27000,
  }],
};
const ADVANCES = {
  advances: [{
    advance_id: 'ADV-1', employee_id: 'EMP-1', employee_name: 'ZZ Priya Sheet',
    amount: 1000, date_requested: '2026-09-05', status: 'pending',
  }],
};
const PAYSLIP = {
  payslip: {
    payslip_id: 'PS-1', employee_id: 'EMP-1', employee_name: 'ZZ Priya Sheet',
    employee_number: 'E001', designation: 'ZZ Optometrist', month: 9, year: 2026,
    generated_at: '2026-09-30',
    breakdown: {
      basic: 20000, hra: 8000, conveyance: 0, medical: 0, special_allowance: 2000,
      gross_salary: 30000, pf_employee: 1800, pf_employer: 1800, professional_tax: 200,
      esi: 0, tds: 0, lwp_deduction: 0, advance_deduction: 1000, net_pay: 27000,
    },
  },
};

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async (url: string) => {
    if (url === '/payroll/salary-sheet') return { data: SHEET };
    if (url.startsWith('/payroll/advances/')) return { data: ADVANCES };
    if (url.startsWith('/payroll/payslip/')) return { data: PAYSLIP };
    throw new Error(`unexpected GET ${url}`);
  });
});

// The REAL route table - the same URL-to-section mapping and gates the app
// ships (hrRoutes.tsx), never a hand-copied one.
function renderRoute(path: string, roles: string[]) {
  currentRoles = roles;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Suspense fallback={null}>
            <Routes>
              {hrRoutes}
              <Route path="/unauthorized" element={<div>ZZ-DENIED</div>} />
            </Routes>
          </Suspense>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const PAYROLL_LABELS = ['Salary Sheet', 'Advances', 'Payslips'];
const navButton = (label: string) => screen.queryByRole('button', { name: new RegExp(`^${label}$`) });

/** The section's own HRLayout nav row is the highlighted one - one URL per section. */
function expectActiveSection(label: string) {
  expect(navButton(label)).toHaveClass('text-bv-red-600');
}

describe('each payroll section renders at its own URL inside the HR module', () => {
  it('sheet (index): the salary table, under the HR header', async () => {
    renderRoute('/hr/payroll', ['ADMIN']);
    expect(await screen.findByText('ZZ Priya Sheet', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Export/ })).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/payroll/salary-sheet', {
      params: { month: expect.any(Number), year: expect.any(Number), store_id: 'ZZ-STORE' },
    });
    // Inside HRLayout now: the module header is on screen above the section.
    expect(screen.getByRole('heading', { name: /Who's on the floor/ })).toBeInTheDocument();
    expectActiveSection('Salary Sheet');
  });

  it('advances: the employee picker is fed by the layout load; picking one loads their advances', async () => {
    renderRoute('/hr/payroll/advances', ['ADMIN']);
    const picker = await screen.findByTitle('Select employee for advances', undefined, FIND);
    expect(await screen.findByRole('option', { name: 'ZZ Priya Sheet' })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: 'EMP-1' } });
    expect(await screen.findByText('Pending')).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/payroll/advances/EMP-1');
    expect(screen.getByRole('button', { name: /Record Advance/ })).toBeInTheDocument();
    expectActiveSection('Advances');
  });

  it('payslips: picking an employee loads the payslip for the picked month', async () => {
    renderRoute('/hr/payroll/payslips', ['ADMIN']);
    const picker = await screen.findByTitle('Select employee for payslip', undefined, FIND);
    expect(await screen.findByRole('option', { name: 'ZZ Priya Sheet' })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: 'EMP-1' } });
    expect(await screen.findByText('EARNINGS')).toBeInTheDocument();
    expect(screen.getByText('ZZ Optometrist')).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(expect.stringMatching(/^\/payroll\/payslip\/EMP-1\/\d+\/\d+$/));
    expectActiveSection('Payslips');
  });
});

describe('the salary gate is unchanged: SUPERADMIN + ADMIN only, at every payroll address', () => {
  it('SALARY_ROLES is exactly the two roles (owner ruling 2026-08-10 - never widen)', () => {
    expect(SALARY_ROLES).toEqual(['SUPERADMIN', 'ADMIN']);
  });

  const PAYROLL_URLS = ['/hr/payroll', '/hr/payroll/advances', '/hr/payroll/payslips'];
  // Every other HR-module role: they can open /hr/today, so the refusal below
  // is the payroll gate's own, not the module's.
  const REFUSED = ['ACCOUNTANT', 'STORE_MANAGER', 'AREA_MANAGER']
    .flatMap((role) => PAYROLL_URLS.map((url) => [role, url] as const));

  it.each(REFUSED)('%s is refused %s, and no salary data is even requested', async (role, url) => {
    renderRoute(url, [role]);
    expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  it('SUPERADMIN opens the sheet', async () => {
    renderRoute('/hr/payroll', ['SUPERADMIN']);
    expect(await screen.findByText('ZZ Priya Sheet', undefined, FIND)).toBeInTheDocument();
  });
});

describe('HRLayout offers the payroll sections only to the salary roles', () => {
  it('ACCOUNTANT sees the HR sections but no payroll row', async () => {
    renderRoute('/hr/today', ['ACCOUNTANT']);
    expect(await screen.findByRole('button', { name: /^Leave Requests/ }, FIND)).toBeInTheDocument();
    for (const label of PAYROLL_LABELS) expect(navButton(label)).not.toBeInTheDocument();
  });

  it('ADMIN sees all three payroll rows next to the HR sections', async () => {
    renderRoute('/hr/today', ['ADMIN']);
    expect(await screen.findByRole('button', { name: /^Leave Requests/ }, FIND)).toBeInTheDocument();
    for (const label of PAYROLL_LABELS) expect(navButton(label)).toBeInTheDocument();
  });
});
