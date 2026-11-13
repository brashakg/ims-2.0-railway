// ============================================================================
// R14: reopening a recorded day shows its times, and saving without touching
// them does not send them (the server keeps the stored times).
// R15: the save names the store the grid is showing, not the login's store.
//
// Discriminating power: the old grid opened with empty time boxes (first
// assertion fails), and its save payload had no store_id (second fails).
// ============================================================================
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const m = vi.hoisted(() => ({
  getAttendanceGrid: vi.fn(), getLwpReport: vi.fn(), getAttendance: vi.fn(), markAttendance: vi.fn(),
}));
vi.mock('../../../services/api', () => ({ hrApi: m }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'adm', activeStoreId: 'AUDTA' }, hasRole: () => true }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

import { MonthlyAttendanceGrid } from '../MonthlyAttendanceGrid';

describe('MonthlyAttendanceGrid edit (R14, R15)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    m.getAttendanceGrid.mockResolvedValue({
      month: '2026-11', days: [1, 2, 3],
      employees: [{ employee_id: 'opt', name: 'Opto One', days: { '3': 'P' }, summary: {} }],
      totals: {},
    });
    m.getLwpReport.mockResolvedValue(null);
    m.getAttendance.mockResolvedValue({ records: [{
      employeeId: 'opt', date: '2026-11-03', checkIn: '2026-11-03T10:00:00+05:30', checkOut: '2026-11-03T18:00:00+05:30',
    }] });
    m.markAttendance.mockResolvedValue({ message: 'ok' });
  });

  it('reopen shows the recorded times; an unchanged save omits them and names the viewed store', async () => {
    render(<MonthlyAttendanceGrid storeId="AUDTB" initialMonth="2026-11" />);
    fireEvent.click(await screen.findByRole('button', { name: /Edit Opto One day 3/ }));
    await waitFor(() => expect((document.getElementById('att-in') as HTMLInputElement).value).toBe('10:00'));
    expect((document.getElementById('att-out') as HTMLInputElement).value).toBe('18:00');
    expect(m.getAttendance).toHaveBeenCalledWith('AUDTB', '2026-11-03', 'opt');
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(m.markAttendance).toHaveBeenCalled());
    const body = m.markAttendance.mock.calls[0][0];
    expect(body.check_in).toBeUndefined();
    expect(body.check_out).toBeUndefined();
    expect(body.store_id).toBe('AUDTB');
  });

  it('emptying a time box clears it deliberately (null)', async () => {
    render(<MonthlyAttendanceGrid storeId="AUDTB" initialMonth="2026-11" />);
    fireEvent.click(await screen.findByRole('button', { name: /Edit Opto One day 3/ }));
    const out = document.getElementById('att-out') as HTMLInputElement;
    await waitFor(() => expect(out.value).toBe('18:00'));
    fireEvent.change(out, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(m.markAttendance).toHaveBeenCalled());
    expect(m.markAttendance.mock.calls[0][0].check_out).toBeNull();
    expect(m.markAttendance.mock.calls[0][0].check_in).toBeUndefined();
  });
});
