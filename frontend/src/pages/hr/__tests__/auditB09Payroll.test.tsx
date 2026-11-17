// ============================================================================
// Codex whole-app audit, batch b09 (payroll) - the screen halves
// ============================================================================
// L3-06  Payroll Run sent an advance recovery of -500 to Preview (net pay UP).
// L3-07  My Work "View / Download" payslip opened an empty about:blank tab.
// L3-14  Salary CSV import said "Imported 1" and dropped the bad row silently.
// L3-02  Tally JV / PF ECR are offered only once a row is approved (decided
//        2026-11-11: a draft is not a cost and is not exported).
// L3-23  My Work shows a payslip once it is Approved or Paid, says which, and
//        says plainly when the newest month is not ready yet (decided 2026-11-11).
// (L3-24, the month selector, is in payrollSections.test.tsx: it needs the
//  real route table. The server halves are backend/tests/test_audit_b09_payroll.py.)
//
// Every test drives the REAL page with only the API, auth and toast mocked.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

let currentUser = { id: 'admin-1', name: 'ZZ Admin', roles: ['ADMIN'], activeStoreId: 'ZZ-STORE' };
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: currentUser }),
}));

const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const payrollApi = {
  listConfigs: vi.fn(),
  listPtSlabs: vi.fn(),
  listRunRows: vi.fn(),
  getSummary: vi.fn(),
  runPayroll: vi.fn(),
  bulkConfigs: vi.fn(),
};
vi.mock('../../../services/api/payroll', () => ({
  payrollApi: new Proxy({}, { get: (_t, k: string) => (payrollApi as Record<string, unknown>)[k] }),
  grossOf: (c: { basic?: number; hra?: number }) => (c.basic || 0) + (c.hra || 0),
}));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { list: vi.fn(async () => ({ entities: [], total: 0 })) },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn(async () => ({ stores: [] })) },
}));

const getMyPayslip = vi.fn();
vi.mock('../../../services/api', () => ({
  hrApi: {
    getMyAttendance: vi.fn(async () => null),
    getMyCommission: vi.fn(async () => null),
    getMyLeaves: vi.fn(async () => null),
    getMyPayslip: (...a: unknown[]) => getMyPayslip(...a),
  },
}));

import { PayrollRunPage } from '../PayrollRunPage';
import { SalarySetupPage, parseSalaryCsv } from '../SalarySetupPage';
import { EmployeeSelfService } from '../EmployeeSelfService';

const CONFIG = { employee_id: 'EMP-1', basic: 15000, hra: 5000 };

beforeEach(() => {
  vi.clearAllMocks();
  currentUser = { id: 'admin-1', name: 'ZZ Admin', roles: ['ADMIN'], activeStoreId: 'ZZ-STORE' };
  payrollApi.listConfigs.mockResolvedValue({ configs: [CONFIG], total: 1 });
  payrollApi.listPtSlabs.mockResolvedValue({ pt_slabs: [], total: 0, source: '' });
  payrollApi.listRunRows.mockResolvedValue({ rows: [], total: 0, totals: {} });
  payrollApi.getSummary.mockResolvedValue({ summary: null });
  payrollApi.runPayroll.mockResolvedValue({ rows: [], totals: { net: 17750 }, count: 1 });
  payrollApi.bulkConfigs.mockResolvedValue({ created: 1, updated: 0, total: 1 });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// L3-06
// ---------------------------------------------------------------------------
describe('L3-06 Payroll Run: an advance recovery is never negative', () => {
  async function recoveryInput() {
    render(<PayrollRunPage />);
    await screen.findByText('EMP-1');
    // The row's two number inputs are LWP days, then Advance recovery.
    return screen.getAllByRole('spinbutton')[1];
  }

  it.each(['Preview', 'Run (save draft)'])('%s with -500 is refused on the screen and never sent', async (button) => {
    fireEvent.change(await recoveryInput(), { target: { value: '-500' } });
    fireEvent.click(screen.getByRole('button', { name: button }));
    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/cannot be negative \(EMP-1\)/));
    expect(payrollApi.runPayroll).not.toHaveBeenCalled();
  });

  it('a positive recovery is still sent as typed', async () => {
    fireEvent.change(await recoveryInput(), { target: { value: '500' } });
    fireEvent.click(screen.getByRole('button', { name: /^Preview/ }));
    await waitFor(() => expect(payrollApi.runPayroll).toHaveBeenCalledTimes(1));
    expect(payrollApi.runPayroll).toHaveBeenCalledWith(
      expect.objectContaining({ advances: { 'EMP-1': 500 }, dry_run: true }),
    );
    expect(toast.error).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// L3-02 (screen half): a draft is not exported
// ---------------------------------------------------------------------------
describe('L3-02 Payroll Run: Tally JV and PF ECR wait for an approved row', () => {
  const row = (employee_id: string, status: string) => ({
    employee_id, employee_name: employee_id, status, net_salary: 17750, deductions: 2250,
  });
  async function exportButtons(rows: unknown[]) {
    payrollApi.listRunRows.mockResolvedValue({ rows, total: rows.length, totals: {} });
    render(<PayrollRunPage />);
    return [
      await screen.findByRole('button', { name: 'Tally JV' }),
      screen.getByRole('button', { name: 'PF ECR' }),
    ] as HTMLButtonElement[];
  }

  it('every row a draft: both exports are off and say why', async () => {
    for (const b of await exportButtons([row('EMP-1', 'DRAFT'), row('EMP-2', 'DRAFT')])) {
      expect(b).toBeDisabled();
      expect(b.title).toMatch(/Approve the payroll run first/);
    }
  });

  it.each(['APPROVED', 'PAID'])('one %s row: both exports are on', async (status) => {
    for (const b of await exportButtons([row('EMP-1', 'DRAFT'), row('EMP-2', status)])) {
      expect(b).toBeEnabled();
      expect(b.title).toBe('');
    }
  });
});

// ---------------------------------------------------------------------------
// L3-14
// ---------------------------------------------------------------------------
const MIXED_CSV = 'employee_id,basic,hra\nEMP-OK,20000,8000\nEMP-BAD,-5000,1000';

describe('L3-14 salary CSV import: a row that is left out is named', () => {
  it('parseSalaryCsv returns the bad rows with their line and reason', () => {
    expect(parseSalaryCsv(MIXED_CSV)).toEqual({
      rows: [{ employee_id: 'EMP-OK', basic: 20000, hra: 8000 }],
      skipped: [{ line: 3, employee_id: 'EMP-BAD', reason: 'basic cannot be negative' }],
    });
    const more = parseSalaryCsv(
      'employee_id,basic,hra\n\n,20000,0\nEMP-Z,0,0\nEMP-N,abc,0\nEMP-H,20000,-1\nEMP-OK,20000,',
    );
    expect(more.rows).toEqual([{ employee_id: 'EMP-OK', basic: 20000, hra: 0 }]);
    expect(more.skipped).toEqual([
      { line: 3, employee_id: '', reason: 'employee_id is missing' },
      { line: 4, employee_id: 'EMP-Z', reason: 'basic must be greater than 0' },
      { line: 5, employee_id: 'EMP-N', reason: 'basic "abc" is not a number' },
      { line: 6, employee_id: 'EMP-H', reason: 'hra cannot be negative' },
    ]);
    expect(parseSalaryCsv('')).toEqual({ rows: [], skipped: [] });
  });

  async function importCsv(csv: string) {
    render(<SalarySetupPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Bulk import (CSV)' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: csv } });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
  }

  it('one good and one bad row: the good one is imported, the bad one is listed and the window stays open', async () => {
    await importCsv(MIXED_CSV);
    await waitFor(() => expect(payrollApi.bulkConfigs).toHaveBeenCalledTimes(1));
    expect(payrollApi.bulkConfigs).toHaveBeenCalledWith([{ employee_id: 'EMP-OK', basic: 20000, hra: 8000 }]);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('1 row was not imported:');
    expect(alert).toHaveTextContent('Line 3 (EMP-BAD): basic cannot be negative');
    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(expect.stringMatching(/1 row was NOT imported/)));
    // Not reported as a clean success, and the pasted text is still there to fix.
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toHaveValue(MIXED_CSV);
  });

  it('every row bad: nothing is sent and each row is listed', async () => {
    await importCsv('employee_id,basic\nEMP-A,0\nEMP-B,-1');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('2 rows were not imported:');
    expect(alert).toHaveTextContent('Line 2 (EMP-A): basic must be greater than 0');
    expect(alert).toHaveTextContent('Line 3 (EMP-B): basic cannot be negative');
    expect(payrollApi.bulkConfigs).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/Nothing was imported/));
  });

  it('a clean file still imports, reports success and closes the window', async () => {
    await importCsv('employee_id,basic\nEMP-OK,20000');
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Imported 1 (1 new, 0 updated)'));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Import' })).not.toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// L3-07
// ---------------------------------------------------------------------------
describe('L3-07 My Work: View / Download opens the payslip, not an empty tab', () => {
  const SLIP = {
    payslip: {
      payslip_id: 'PS-1', employee_id: 'emp-A', employee_name: 'ZZ <b>Sunil</b>', month: 10, year: 2026,
      status: 'PAID',
      breakdown: { gross_salary: 20000, total_deductions: 2250, net_pay: 17750 },
    },
  };

  /** window.open as a browser implements it: asking for `noopener` returns
   *  null (the HTML standard), which is exactly how the old code lost its tab. */
  function stubWindowOpen(blocked = false) {
    const written: string[] = [];
    // The new tab, as far as the page uses it: the written HTML is parsed into
    // a real document so the page can find its button, and print() is spied.
    const tab = { doc: document.implementation.createHTMLDocument('') };
    const popup = {
      print: vi.fn(),
      document: {
        write: (h: string) => { written.push(h); tab.doc = new DOMParser().parseFromString(h, 'text/html'); },
        close: vi.fn(),
        querySelector: (sel: string) => tab.doc.querySelector(sel),
      },
    };
    const open = vi.spyOn(window, 'open').mockImplementation((_url, _target, features) =>
      (blocked || String(features || '').includes('noopener') ? null : popup) as unknown as Window,
    );
    return { open, written, popup, tab };
  }

  async function clickDownload(slip: unknown = SLIP) {
    currentUser = { id: 'emp-A', name: 'Sunil Kumar', roles: ['SALES_STAFF'], activeStoreId: 'ZZ-STORE' };
    getMyPayslip.mockResolvedValue(slip);
    render(<EmployeeSelfService />);
    fireEvent.click(await screen.findByRole('button', { name: /View \/ Download/ }));
  }

  it('writes the same net pay the card shows into the new tab', async () => {
    const { open, written } = stubWindowOpen();
    await clickDownload();
    expect(open).toHaveBeenCalledTimes(1);
    const html = written.join('');
    expect(html).toContain('October 2026');
    expect(html).toContain('Net Pay');
    expect(html).toContain('₹17,750');
    // A name is text, never markup, in a tab that shares the app's origin.
    expect(html).not.toContain('<b>Sunil</b>');
    expect(html).toContain('ZZ &#60;b&#62;Sunil&#60;/b&#62;');
    expect(toast.error).not.toHaveBeenCalled();
  });

  // The live site's Content-Security-Policy (frontend/vercel.json: script-src
  // 'self', no 'unsafe-inline') refuses inline handlers, and the new tab
  // inherits that policy: an onclick="window.print()" button did nothing.
  it('the Print button works without any inline script in the new tab', async () => {
    const { written, popup, tab } = stubWindowOpen();
    await clickDownload();
    expect(written.join('')).not.toMatch(/\son[a-z]+\s*=|<script/i);
    expect(popup.print).not.toHaveBeenCalled();
    const button = tab.doc.querySelector('button')!;
    expect(button.textContent).toBe('Print / Save as PDF');
    button.click();
    expect(popup.print).toHaveBeenCalledTimes(1);
  });

  it('says so when the browser blocks the pop-up, instead of doing nothing', async () => {
    stubWindowOpen(true);
    await clickDownload();
    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/allow pop-ups/));
  });

  // L3-23: every payslip says whether it is Approved or Paid, on the card and
  // on the printed slip.
  it.each([
    ['PAID', 'Paid'],
    ['APPROVED', 'Approved'],
  ])('a %s payslip says so on the card and on the printed slip', async (status, label) => {
    const { written } = stubWindowOpen();
    await clickDownload({ payslip: { ...SLIP.payslip, status } });
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(written.join('')).toContain(`&middot; ${label}`);
    expect(screen.queryByText(/not ready yet/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// L3-23 (screen half): staff are not shown a draft
// ---------------------------------------------------------------------------
describe('L3-23 My Work: a payslip still in draft is "not ready yet", with no figures', () => {
  // Months well in the past, so the page's own month header never matches.
  const NOT_READY = { month: 4, year: 2025 };
  const MARCH = {
    payslip_id: 'PS-1', employee_id: 'emp-A', employee_name: 'Sunil Kumar', month: 3, year: 2025,
    status: 'APPROVED',
    breakdown: { gross_salary: 20000, total_deductions: 2250, net_pay: 17750 },
  };
  async function openMyWork(answer: unknown) {
    currentUser = { id: 'emp-A', name: 'Sunil Kumar', roles: ['SALES_STAFF'], activeStoreId: 'ZZ-STORE' };
    getMyPayslip.mockResolvedValue(answer);
    render(<EmployeeSelfService />);
    await screen.findByText('Latest Payslip');
  }

  it('first payslip is a draft: the card says the month is not ready and offers nothing to open', async () => {
    await openMyWork({ payslip: null, not_ready: NOT_READY });
    expect(await screen.findByText(/Your April 2025 payslip is not ready yet/)).toBeInTheDocument();
    expect(screen.queryByText('No payslip generated yet.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /View \/ Download/ })).not.toBeInTheDocument();
    expect(screen.queryByText('Net pay')).not.toBeInTheDocument();
  });

  it('newest month is a draft: last approved payslip stays, with the not-ready line above it', async () => {
    await openMyWork({ payslip: MARCH, not_ready: NOT_READY });
    expect(await screen.findByText(/Your April 2025 payslip is not ready yet/)).toBeInTheDocument();
    expect(screen.getByText('March 2025')).toBeInTheDocument();
    expect(screen.getByText('Approved')).toBeInTheDocument();
    expect(screen.getByText('₹17,750')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /View \/ Download/ })).toBeInTheDocument();
  });

  it('no payroll at all: the old empty line, and no not-ready claim', async () => {
    await openMyWork({ payslip: null });
    expect(await screen.findByText('No payslip generated yet.')).toBeInTheDocument();
    expect(screen.queryByText(/not ready yet/)).not.toBeInTheDocument();
  });
});
