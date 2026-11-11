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

import { Suspense, useSyncExternalStore } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// jsdom has no requestIdleCallback, so the layout's chunk-warming would fall
// back to a 1.5s setTimeout that can fire after teardown. Not under test.
vi.stubGlobal('requestIdleCallback', () => 0);

let currentRoles: string[] = [];

// The active shop changes IN PLACE in the real app (AuthContext.setActiveStore,
// no page reload), so the mock lets a test switch it under a mounted screen.
let currentStoreId = 'ZZ-STORE';
const storeListeners = new Set<() => void>();
const subscribeStore = (l: () => void) => { storeListeners.add(l); return () => { storeListeners.delete(l); }; };
function switchShop(storeId: string) {
  currentStoreId = storeId;
  storeListeners.forEach((l) => l());
}

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'u-1', roles: currentRoles,
      activeStoreId: useSyncExternalStore(subscribeStore, () => currentStoreId),
    },
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
  }, {
    advance_id: 'ADV-2', employee_id: 'EMP-1', employee_name: 'ZZ Priya Sheet',
    amount: 500, date_requested: '2026-08-05', status: 'deducted',
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
  currentStoreId = 'ZZ-STORE';
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
    // The old page header is kept on every payroll section, as an h2: HRLayout's
    // module header is the page's one h1.
    expect(screen.getByRole('heading', { level: 2, name: 'Month-end, by the rupee.' })).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });

  // Audit L3-16 (owner ruling 2026-11-11): the sheet shows the payroll run and
  // nothing else. It used to say "No salary data", or list an old-calculation
  // row as zero Basic/HRA/Gross beside a net pay.
  it('sheet, a month with no payroll run: says so plainly and points to Payroll Run', async () => {
    get.mockImplementation(async (url: string) => {
      if (url === '/payroll/salary-sheet') return { data: { salaries: [], total: 0 } };
      throw new Error(`unexpected GET ${url}`);
    });
    renderRoute('/hr/payroll', ['ADMIN']);
    expect(
      await screen.findByText(/^No payroll run is saved for \w+ \d{4}\. Salaries are calculated in \.$/, undefined, FIND),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Payroll Run' })).toHaveAttribute('href', '/hr/payroll-run');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('advances: the employee picker is fed by the layout load; picking one loads their advances', async () => {
    renderRoute('/hr/payroll/advances', ['ADMIN']);
    const picker = await screen.findByTitle('Select employee for advances', undefined, FIND);
    expect(await screen.findByRole('option', { name: 'ZZ Priya Sheet' })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: 'EMP-1' } });
    expect(await screen.findByText('Pending')).toBeInTheDocument();
    // The Deducted badge reads exactly 'Deducted' (the old tab printed a stray '$' before it).
    expect(screen.getByText('Deducted')).toBeInTheDocument();
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

// Audit L3-24: the payslip on screen must be the one for the month in the
// selector. It used to load only when the EMPLOYEE was picked, so changing the
// month left the previous month's payslip (and its net pay) on screen.
describe('payslips: the payslip follows the month and year selectors', () => {
  const slipFor = (month: number, year: number, netPay: number) => ({
    payslip: {
      ...PAYSLIP.payslip, month, year,
      breakdown: { ...PAYSLIP.payslip.breakdown, net_pay: netPay },
    },
  });
  const year = new Date().getFullYear();

  async function openSlip(answer: (month: number, year: number) => unknown) {
    get.mockImplementation(async (url: string) => {
      if (url === '/payroll/salary-sheet') return { data: SHEET };
      const m = /^\/payroll\/payslip\/EMP-1\/(\d+)\/(\d+)$/.exec(url);
      if (m) return { data: await answer(Number(m[1]), Number(m[2])) };
      throw new Error(`unexpected GET ${url}`);
    });
    renderRoute('/hr/payroll/payslips', ['ADMIN']);
    fireEvent.change(await screen.findByTitle('Select payslip month', undefined, FIND), { target: { value: '2' } });
    const picker = screen.getByTitle('Select employee for payslip');
    expect(await screen.findByRole('option', { name: 'ZZ Priya Sheet' })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: 'EMP-1' } });
  }

  it('changing the month loads that month and takes the old payslip off the screen', async () => {
    await openSlip((month, y) => (month === 2 ? slipFor(2, y, 17650) : { payslip: null }));
    expect(await screen.findByText(`February ${year}`)).toBeInTheDocument();
    expect(screen.getByText('₹17,650')).toBeInTheDocument();

    fireEvent.change(screen.getByTitle('Select payslip month'), { target: { value: '3' } });
    expect(await screen.findByText('No payslip found for selected month')).toBeInTheDocument();
    expect(screen.queryByText(`February ${year}`)).not.toBeInTheDocument();
    expect(screen.queryByText('₹17,650')).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(`/payroll/payslip/EMP-1/3/${year}`);
  });

  it('changing the year reloads too', async () => {
    await openSlip((month, y) => slipFor(month, y, y === year ? 17650 : 16000));
    expect(await screen.findByText('₹17,650')).toBeInTheDocument();
    fireEvent.change(screen.getByTitle('Select payslip year'), { target: { value: String(year - 1) } });
    expect(await screen.findByText(`February ${year - 1}`)).toBeInTheDocument();
    expect(screen.getByText('₹16,000')).toBeInTheDocument();
  });

  it('a slow answer for the month you left never lands under the month you picked', async () => {
    let releaseFebruary: (v: unknown) => void = () => {};
    await openSlip((month, y) => (
      month === 2
        ? new Promise((resolve) => { releaseFebruary = () => resolve(slipFor(2, y, 17650)); })
        : slipFor(month, y, 15000)
    ));
    // February is still on its way when March is picked and answers first.
    fireEvent.change(screen.getByTitle('Select payslip month'), { target: { value: '3' } });
    expect(await screen.findByText(`March ${year}`)).toBeInTheDocument();
    releaseFebruary(null);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText(`March ${year}`)).toBeInTheDocument();
    expect(screen.getByText('₹15,000')).toBeInTheDocument();
    expect(screen.queryByText('₹17,650')).not.toBeInTheDocument();
  });

  // Audit L3-24, the fourth selector: the shop. The picked employee belongs to
  // the shop they were picked in; their payslip stayed on screen under a picker
  // that already listed the new shop's staff.
  it('changing the shop clears the picked employee and takes their payslip off the screen', async () => {
    const OTHER_SHOP = { salaries: [{ ...SHEET.salaries[0], employee_id: 'EMP-9', employee_name: 'ZZ Other Shop' }] };
    get.mockImplementation(async (url: string, config?: { params?: { store_id?: string } }) => {
      if (url === '/payroll/salary-sheet') {
        return { data: config?.params?.store_id === 'ZZ-STORE-B' ? OTHER_SHOP : SHEET };
      }
      if (url.startsWith('/payroll/payslip/EMP-1/')) return { data: slipFor(2, year, 17650) };
      if (url.startsWith('/payroll/advances/')) return { data: ADVANCES };
      throw new Error(`unexpected GET ${url}`);
    });
    renderRoute('/hr/payroll/payslips', ['ADMIN']);
    const picker = await screen.findByTitle('Select employee for payslip', undefined, FIND);
    expect(await screen.findByRole('option', { name: 'ZZ Priya Sheet' })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: 'EMP-1' } });
    expect(await screen.findByText('₹17,650')).toBeInTheDocument();

    act(() => switchShop('ZZ-STORE-B'));
    expect(await screen.findByRole('option', { name: 'ZZ Other Shop' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'ZZ Priya Sheet' })).not.toBeInTheDocument();
    expect(picker).toHaveValue('');
    expect(screen.queryByText('₹17,650')).not.toBeInTheDocument();
    expect(screen.queryByText('EARNINGS')).not.toBeInTheDocument();
  });

  it('an employee picked on Advances has their payslip loaded when Payslips opens', async () => {
    renderRoute('/hr/payroll/advances', ['ADMIN']);
    const advPicker = await screen.findByTitle('Select employee for advances', undefined, FIND);
    expect(await screen.findByRole('option', { name: 'ZZ Priya Sheet' })).toBeInTheDocument();
    fireEvent.change(advPicker, { target: { value: 'EMP-1' } });
    expect(await screen.findByText('Pending')).toBeInTheDocument();
    fireEvent.click(navButton('Payslips')!);
    expect(await screen.findByText('EARNINGS', undefined, FIND)).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(expect.stringMatching(/^\/payroll\/payslip\/EMP-1\/\d+\/\d+$/));
  });
});

// Audit L3-24, the Advances half. Advances and Payslips share ONE picked
// employee. The advances list was loaded only by the Advances picker's own
// onChange and never cleared, so after the employee (or the shop) changed
// anywhere else, one person's advances sat under another person's name.
describe('advances: the list on screen belongs to the employee in the picker', () => {
  const SECOND = { ...SHEET.salaries[0], salary_record_id: 'SR-2', employee_id: 'EMP-2', employee_name: 'ZZ Second Staff' };
  const OTHER_SHOP = { salaries: [{ ...SHEET.salaries[0], employee_id: 'EMP-9', employee_name: 'ZZ Other Shop' }] };
  const SECOND_ADVANCES = {
    advances: [{
      advance_id: 'ADV-7', employee_id: 'EMP-2', employee_name: 'ZZ Second Staff',
      amount: 7300, date_requested: '2026-09-07', status: 'approved',
    }],
  };

  /** EMP-1 has the two ADVANCES rows (Pending 1,000 and Deducted 500), EMP-2 one of 7,300, EMP-9 none. */
  function route(firstAnswer: () => unknown = () => ADVANCES) {
    get.mockImplementation(async (url: string, config?: { params?: { store_id?: string } }) => {
      if (url === '/payroll/salary-sheet') {
        return { data: config?.params?.store_id === 'ZZ-STORE-B' ? OTHER_SHOP : { salaries: [SHEET.salaries[0], SECOND] } };
      }
      if (url === '/payroll/advances/EMP-1') return { data: await firstAnswer() };
      if (url === '/payroll/advances/EMP-2') return { data: SECOND_ADVANCES };
      if (url === '/payroll/advances/EMP-9') return { data: { advances: [] } };
      if (url.startsWith('/payroll/payslip/')) return { data: PAYSLIP };
      throw new Error(`unexpected GET ${url}`);
    });
  }

  async function pickFirstOnAdvances() {
    renderRoute('/hr/payroll/advances', ['ADMIN']);
    const picker = await screen.findByTitle('Select employee for advances', undefined, FIND);
    expect(await screen.findByRole('option', { name: 'ZZ Priya Sheet' })).toBeInTheDocument();
    fireEvent.change(picker, { target: { value: 'EMP-1' } });
    return picker;
  }

  async function pickOnPayslipsAndComeBack(employeeId: string, optionName: string) {
    fireEvent.click(navButton('Payslips')!);
    const slipPicker = await screen.findByTitle('Select employee for payslip', undefined, FIND);
    expect(await screen.findByRole('option', { name: optionName })).toBeInTheDocument();
    fireEvent.change(slipPicker, { target: { value: employeeId } });
    expect(await screen.findByText('EARNINGS')).toBeInTheDocument();
    fireEvent.click(navButton('Advances')!);
    return screen.findByTitle('Select employee for advances', undefined, FIND);
  }

  it('an employee picked on Payslips: Advances shows THEIR advances, not the previous employee\'s', async () => {
    route();
    await pickFirstOnAdvances();
    expect(await screen.findByText('Pending')).toBeInTheDocument();
    expect(screen.getByText('₹1,000')).toBeInTheDocument();

    expect(await pickOnPayslipsAndComeBack('EMP-2', 'ZZ Second Staff')).toHaveValue('EMP-2');
    expect(await screen.findByText('₹7,300')).toBeInTheDocument();
    expect(screen.queryByText('₹1,000')).not.toBeInTheDocument();
    expect(screen.queryByText('Pending')).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/payroll/advances/EMP-2');
  });

  it('after a shop change, an employee of the new shop never shows the old shop\'s advances', async () => {
    route();
    const picker = await pickFirstOnAdvances();
    expect(await screen.findByText('Pending')).toBeInTheDocument();

    act(() => switchShop('ZZ-STORE-B'));
    expect(await screen.findByRole('option', { name: 'ZZ Other Shop' })).toBeInTheDocument();
    expect(picker).toHaveValue('');
    expect(screen.queryByText('Pending')).not.toBeInTheDocument();

    expect(await pickOnPayslipsAndComeBack('EMP-9', 'ZZ Other Shop')).toHaveValue('EMP-9');
    expect(await screen.findByText('No advances recorded')).toBeInTheDocument();
    expect(screen.queryByText('Pending')).not.toBeInTheDocument();
    expect(screen.queryByText('₹1,000')).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledWith('/payroll/advances/EMP-9');
  });

  it('a slow answer for the employee you left never lands under the employee you picked', async () => {
    let releaseFirst: () => void = () => {};
    route(() => new Promise((resolve) => { releaseFirst = () => resolve(ADVANCES); }));
    const picker = await pickFirstOnAdvances();
    // EMP-1's advances are still on their way when EMP-2 is picked and answers first.
    fireEvent.change(picker, { target: { value: 'EMP-2' } });
    expect(await screen.findByText('₹7,300')).toBeInTheDocument();
    releaseFirst();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText('₹7,300')).toBeInTheDocument();
    expect(screen.queryByText('Pending')).not.toBeInTheDocument();
    expect(screen.queryByText('₹1,000')).not.toBeInTheDocument();
  });
});

describe('the picks live in PayrollLayout, so they survive moving between sections', () => {
  it('month, year and employee picked on one section are still picked on the next', async () => {
    const year = new Date().getFullYear();
    renderRoute('/hr/payroll', ['ADMIN']);
    // Sheet: pick a month and year.
    fireEvent.change(await screen.findByTitle('Select payroll month', undefined, FIND), { target: { value: '3' } });
    fireEvent.change(screen.getByTitle('Select payroll year'), { target: { value: String(year - 1) } });
    // Advances: pick the employee.
    fireEvent.click(navButton('Advances')!);
    const advPicker = await screen.findByTitle('Select employee for advances', undefined, FIND);
    expect(await screen.findByRole('option', { name: 'ZZ Priya Sheet' })).toBeInTheDocument();
    fireEvent.change(advPicker, { target: { value: 'EMP-1' } });
    expect(await screen.findByText('Pending')).toBeInTheDocument();
    // Payslips: all three picks are still there.
    fireEvent.click(navButton('Payslips')!);
    const slipPicker = await screen.findByTitle('Select employee for payslip', undefined, FIND);
    expect(slipPicker).toHaveValue('EMP-1');
    expect(screen.getByTitle('Select payslip month')).toHaveValue('3');
    expect(screen.getByTitle('Select payslip year')).toHaveValue(String(year - 1));
    // And back on the sheet the month/year are unchanged too.
    fireEvent.click(navButton('Salary Sheet')!);
    expect(await screen.findByTitle('Select payroll month', undefined, FIND)).toHaveValue('3');
    expect(screen.getByTitle('Select payroll year')).toHaveValue(String(year - 1));
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
