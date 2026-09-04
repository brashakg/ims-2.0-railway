// ============================================================================
// Apply-for-leave from My Work (/my-work) — the door floor staff actually use
// ============================================================================
// Nobody below manager could request leave (POST /hr/leaves sits behind the
// HR finance gate), so leave happened over WhatsApp. The self-service screen
// now files through POST /hr/me/leaves and cancels through
// POST /hr/me/leaves/{id}/cancel. These tests drive the REAL component with
// only the API mocked and lock the wiring that would rot silently:
//
//  * the submit sends the EXACT snake_case payload the backend model reads
//    (the axios aliaser only camelizes responses — a camelCase body would
//    422 in production while a loosely-asserted test stayed green);
//  * after a successful apply the leave list is REFETCHED (the "did it go
//    through?" glance is the anti-WhatsApp feature);
//  * the server's own 409 overlap message reaches the toast verbatim (the
//    server is the authority; swallowing its message re-opens double-filing);
//  * Cancel is offered ONLY on a PENDING row and posts the row's own id;
//  * buildLeaveApplication / canCancelLeave guard exactly what the server
//    would reject anyway (mirror, not a second rule).

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const getMyAttendance = vi.fn();
const getMyCommission = vi.fn();
const getMyPayslip = vi.fn();
const getMyLeaves = vi.fn();
const applyMyLeave = vi.fn();
const cancelMyLeave = vi.fn();

vi.mock('../../../services/api', () => ({
  hrApi: {
    getMyAttendance: (...a: unknown[]) => getMyAttendance(...a),
    getMyCommission: (...a: unknown[]) => getMyCommission(...a),
    getMyPayslip: (...a: unknown[]) => getMyPayslip(...a),
    getMyLeaves: (...a: unknown[]) => getMyLeaves(...a),
    applyMyLeave: (...a: unknown[]) => applyMyLeave(...a),
    cancelMyLeave: (...a: unknown[]) => cancelMyLeave(...a),
  },
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'emp-A', name: 'Sunil Kumar', roles: ['SALES_STAFF'], activeStoreId: 'BV-BOK-01' },
  }),
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    success: (...a: unknown[]) => toastSuccess(...a),
    error: (...a: unknown[]) => toastError(...a),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

import {
  EmployeeSelfService,
  buildLeaveApplication,
  canCancelLeave,
} from '../EmployeeSelfService';

const MY_LEAVES = {
  year: new Date().getFullYear(),
  leaves: [
    {
      leave_id: 'lv-pending',
      leave_type: 'CASUAL',
      from_date: '2026-09-10',
      to_date: '2026-09-11',
      days: 2,
      status: 'PENDING',
      reason: 'x',
      applied_at: '2026-09-01',
    },
    {
      leave_id: 'lv-approved',
      leave_type: 'SICK',
      from_date: '2026-08-02',
      to_date: '2026-08-02',
      days: 1,
      status: 'APPROVED',
      reason: 'y',
      applied_at: '2026-08-01',
    },
  ],
  summary: { approved_days: 1, pending_days: 2, by_type: { SICK: 1 } },
};

beforeEach(() => {
  getMyAttendance.mockResolvedValue({
    month: 9, year: 2026, days: {},
    summary: { present: 0, absent: 0, half_day: 0, leave: 0, holiday: 0, week_off: 0, lwp: 0, late: 0 },
  });
  getMyCommission.mockResolvedValue(null);
  getMyPayslip.mockResolvedValue({ payslip: null });
  getMyLeaves.mockResolvedValue(MY_LEAVES);
  applyMyLeave.mockResolvedValue({ leaveId: 'lv-new', message: 'ok', status: 'PENDING' });
  cancelMyLeave.mockResolvedValue({ message: 'ok', leave_id: 'lv-pending', status: 'CANCELLED' });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function openApplyForm() {
  render(<EmployeeSelfService />);
  const applyBtn = await screen.findByRole('button', { name: /apply for leave/i });
  fireEvent.click(applyBtn);
  return applyBtn;
}

describe('apply for leave', () => {
  it('submits the exact snake_case payload the backend reads and refetches the list', async () => {
    await openApplyForm();

    fireEvent.change(screen.getByLabelText('Leave type'), { target: { value: 'SICK' } });
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-10-05' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-10-07' } });
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '  fever  ' } });

    expect(getMyLeaves).toHaveBeenCalledTimes(1); // mount load only, so far
    fireEvent.click(screen.getByRole('button', { name: /submit request/i }));

    await waitFor(() => expect(applyMyLeave).toHaveBeenCalledTimes(1));
    expect(applyMyLeave).toHaveBeenCalledWith({
      leave_type: 'SICK',
      from_date: '2026-10-05',
      to_date: '2026-10-07',
      reason: 'fever',
    });
    // The list the staff member checks ("did it go through?") must refresh.
    await waitFor(() => expect(getMyLeaves).toHaveBeenCalledTimes(2));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("surfaces the server's overlap message verbatim (server stays the authority)", async () => {
    const serverMsg =
      'Leave overlaps with an existing PENDING leave (2026-09-10 to 2026-09-11).';
    applyMyLeave.mockRejectedValueOnce(new Error(serverMsg));
    await openApplyForm();

    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'trip' } });
    fireEvent.click(screen.getByRole('button', { name: /submit request/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith(serverMsg));
    expect(getMyLeaves).toHaveBeenCalledTimes(1); // no phantom refresh on failure
  });

  it('blocks a blank reason locally without calling the API', async () => {
    await openApplyForm();
    fireEvent.click(screen.getByRole('button', { name: /submit request/i }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(applyMyLeave).not.toHaveBeenCalled();
  });
});

describe('cancel a pending request', () => {
  it('offers Cancel ONLY on the PENDING row and posts that row id', async () => {
    render(<EmployeeSelfService />);
    // Both rows render; exactly one Cancel button (the APPROVED row gets none).
    await screen.findByText('PENDING');
    await screen.findByText('APPROVED');
    const cancelButtons = screen.getAllByRole('button', { name: /^cancel$/i });
    expect(cancelButtons).toHaveLength(1);

    fireEvent.click(cancelButtons[0]);
    await waitFor(() => expect(cancelMyLeave).toHaveBeenCalledWith('lv-pending'));
    await waitFor(() => expect(getMyLeaves).toHaveBeenCalledTimes(2));
  });

  it('does nothing when the confirm dialog is declined', async () => {
    (window.confirm as unknown as ReturnType<typeof vi.fn>).mockReturnValue(false);
    render(<EmployeeSelfService />);
    await screen.findByText('PENDING');
    fireEvent.click(screen.getAllByRole('button', { name: /^cancel$/i })[0]);
    expect(cancelMyLeave).not.toHaveBeenCalled();
  });
});

describe('buildLeaveApplication (mirror of the server checks, not a second rule)', () => {
  const base = { leaveType: 'CASUAL', fromDate: '2026-10-01', toDate: '2026-10-02', reason: 'r' };

  it('produces the exact snake_case payload', () => {
    const res = buildLeaveApplication({ ...base, reason: '  spaced  ' });
    expect(res).toEqual({
      ok: true,
      payload: {
        leave_type: 'CASUAL',
        from_date: '2026-10-01',
        to_date: '2026-10-02',
        reason: 'spaced',
      },
    });
  });

  it('rejects an inverted range', () => {
    const res = buildLeaveApplication({ ...base, fromDate: '2026-10-05', toDate: '2026-10-02' });
    expect(res.ok).toBe(false);
  });

  it('rejects missing dates, type, or a whitespace-only reason', () => {
    expect(buildLeaveApplication({ ...base, fromDate: '' }).ok).toBe(false);
    expect(buildLeaveApplication({ ...base, toDate: '' }).ok).toBe(false);
    expect(buildLeaveApplication({ ...base, leaveType: '' }).ok).toBe(false);
    expect(buildLeaveApplication({ ...base, reason: '   ' }).ok).toBe(false);
  });
});

describe('canCancelLeave', () => {
  it('is true only for PENDING', () => {
    expect(canCancelLeave('PENDING')).toBe(true);
    expect(canCancelLeave('pending')).toBe(true);
    for (const s of ['APPROVED', 'REJECTED', 'CANCELLED', '', undefined]) {
      expect(canCancelLeave(s as string)).toBe(false);
    }
  });
});
