// ============================================================================
// IMS 2.0 - hr.ts field-mapper tests
// ============================================================================
// getAttendance maps the backend camelCase keys
//   attendanceId / employeeId / employeeName / checkIn / checkOut
// onto the FE keys id / userId / userName / checkInTime / checkOutTime.
// getLeaves maps RAW snake_case leave docs leave_id / from_date / to_date onto
// id / startDate / endDate and computes an inclusive `days` count.

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../client';
import { hrApi } from '../hr';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
});

describe('hrApi.getAttendance -> camelCase BE keys mapped to FE keys', () => {
  it('maps attendanceId/employeeId/employeeName/checkIn/checkOut onto FE keys', async () => {
    mockGet.mockResolvedValue({
      data: {
        records: [
          {
            attendanceId: 'att_1',
            employeeId: 'emp_1',
            employeeName: 'Ramesh Kumar',
            checkIn: '2026-05-01T09:05:00',
            checkOut: '2026-05-01T18:10:00',
            status: 'PRESENT',
          },
        ],
      },
    });

    const result = await hrApi.getAttendance('store_1', '2026-05-01');

    expect(mockGet).toHaveBeenCalledWith('/hr/attendance', {
      params: { store_id: 'store_1', date: '2026-05-01' },
    });

    const rec = result.records[0];
    expect(rec.id).toBe('att_1');
    expect(rec.userId).toBe('emp_1');
    expect(rec.userName).toBe('Ramesh Kumar');
    expect(rec.checkInTime).toBe('2026-05-01T09:05:00');
    expect(rec.checkOutTime).toBe('2026-05-01T18:10:00');
    // Raw fields are preserved alongside the mapped ones.
    expect(rec.status).toBe('PRESENT');
    expect(rec.attendanceId).toBe('att_1');
  });

  it('defaults missing check-in/out to null and ids to empty string', async () => {
    mockGet.mockResolvedValue({
      data: { records: [{ employeeName: 'No Times Yet' }] },
    });

    const rec = (await hrApi.getAttendance('store_1')).records[0];
    expect(rec.id).toBe('');
    expect(rec.userId).toBe('');
    expect(rec.userName).toBe('No Times Yet');
    expect(rec.checkInTime).toBeNull();
    expect(rec.checkOutTime).toBeNull();
  });

  it('accepts a bare top-level array (not wrapped in {records})', async () => {
    mockGet.mockResolvedValue({
      data: [
        { attendanceId: 'a', employeeId: 'e', checkIn: 'T1', checkOut: 'T2' },
      ],
    });
    const result = await hrApi.getAttendance('store_1');
    expect(result.records).toHaveLength(1);
    expect(result.records[0].id).toBe('a');
    expect(result.records[0].userId).toBe('e');
    expect(result.records[0].checkInTime).toBe('T1');
  });
});

describe('hrApi.getLeaves -> snake_case leave docs mapped + days computed', () => {
  it('maps leave_id/from_date/to_date onto id/startDate/endDate', async () => {
    mockGet.mockResolvedValue({
      data: {
        leaves: [
          {
            leave_id: 'lv_1',
            employee_id: 'emp_9',
            employee_name: 'Sita Devi',
            leave_type: 'CASUAL',
            from_date: '2026-05-10',
            to_date: '2026-05-12',
            status: 'PENDING',
            applied_at: '2026-05-08',
            approved_by: null,
          },
        ],
      },
    });

    const result = await hrApi.getLeaves({ userId: 'emp_9' });

    expect(mockGet).toHaveBeenCalledWith('/hr/leaves', { params: { userId: 'emp_9' } });

    const lv = result.leaves[0];
    expect(lv.id).toBe('lv_1');
    expect(lv.userId).toBe('emp_9');
    expect(lv.userName).toBe('Sita Devi');
    expect(lv.leaveType).toBe('CASUAL');
    expect(lv.startDate).toBe('2026-05-10');
    expect(lv.endDate).toBe('2026-05-12');
    // Inclusive day count: 10th, 11th, 12th -> 3 days.
    expect(lv.days).toBe(3);
    expect(lv.appliedAt).toBe('2026-05-08');
    expect(lv.approvedBy).toBeNull();
    // raw leave_id retained so approveLeave still gets a valid id.
    expect(lv.leave_id).toBe('lv_1');
  });

  it('computes a single day when from_date == to_date', async () => {
    mockGet.mockResolvedValue({
      data: { leaves: [{ leave_id: 'lv_2', from_date: '2026-06-01', to_date: '2026-06-01' }] },
    });
    const lv = (await hrApi.getLeaves()).leaves[0];
    expect(lv.startDate).toBe('2026-06-01');
    expect(lv.endDate).toBe('2026-06-01');
    expect(lv.days).toBe(1);
  });

  it('honours an explicit days field instead of recomputing', async () => {
    mockGet.mockResolvedValue({
      data: { leaves: [{ leave_id: 'lv_3', from_date: '2026-06-01', to_date: '2026-06-10', days: 4 }] },
    });
    const lv = (await hrApi.getLeaves()).leaves[0];
    expect(lv.days).toBe(4);
  });

  it('defaults endDate to startDate and days to 1 when to_date is absent', async () => {
    mockGet.mockResolvedValue({
      data: { leaves: [{ leave_id: 'lv_4', from_date: '2026-06-05' }] },
    });
    const lv = (await hrApi.getLeaves()).leaves[0];
    expect(lv.startDate).toBe('2026-06-05');
    expect(lv.endDate).toBe('2026-06-05');
    expect(lv.days).toBe(1);
  });
});
