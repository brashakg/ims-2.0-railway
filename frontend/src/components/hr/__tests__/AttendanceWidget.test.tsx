// ============================================================================
// R01: the attendance card shows only what the server holds. A refused or
// failed write stays visibly unsaved with a retry; nothing is cached in the
// browser; check-out goes to the server's own today-row door.
//
// Discriminating power: with the old widget (localStorage + catch-as-success)
// "a failed check-in stays unsaved" renders "Checked in at" and fails, "a
// failed check-out" renders "Day complete", "no location" sends 0,0, and the
// localStorage assertion sees a write.
// ============================================================================
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const m = vi.hoisted(() => ({ getMyAttendance: vi.fn(), checkIn: vi.fn(), checkOut: vi.fn(), setItem: vi.fn() }));
vi.mock('../../../services/api', () => ({
  hrApi: { getMyAttendance: m.getMyAttendance, checkIn: m.checkIn, checkOut: m.checkOut },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', roles: ['OPTOMETRIST'], activeStoreId: 'AUDTA' } }),
}));

import { AttendanceWidget } from '../AttendanceWidget';

const server500 = Object.assign(new Error('Request failed with status code 500'), {
  response: { status: 500, data: { detail: 'Internal Server Error' } },
});
const IN = { attendance_id: 'a1', date: '2026-11-13', status: 'PRESENT', check_in: '2026-11-13T10:52:00+05:30', check_out: null, is_late: false, store_id: 'AUDTA' };

function noLocation() {
  vi.stubGlobal('navigator', { ...navigator, geolocation: { getCurrentPosition: (_ok: unknown, err: () => void) => err() } });
}

describe('AttendanceWidget (R01)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    noLocation();
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: m.setItem, removeItem: vi.fn() });
  });

  it('a failed check-in stays unsaved and retryable', async () => {
    m.getMyAttendance.mockResolvedValue({ today: null });
    m.checkIn.mockRejectedValue(server500);
    render(<AttendanceWidget />);
    fireEvent.click(await screen.findByRole('button', { name: /check in/i }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByText(/checked in at/i)).toBeNull();
    expect(screen.getByRole('button', { name: /check in/i })).toBeTruthy();
    expect(m.setItem).not.toHaveBeenCalled();
  });

  it('no location sends no coordinates (the server rule decides, never 0,0)', async () => {
    m.getMyAttendance.mockResolvedValue({ today: null });
    m.checkIn.mockRejectedValue(Object.assign(new Error('x'), {
      response: { status: 403, data: { detail: 'Location access required for check-in. Please enable GPS and try again.' } },
    }));
    render(<AttendanceWidget />);
    fireEvent.click(await screen.findByRole('button', { name: /check in/i }));
    await waitFor(() => expect(m.checkIn).toHaveBeenCalled());
    expect(m.checkIn.mock.calls[0]).toEqual(['AUDTA', undefined, undefined]);
    expect((await screen.findByRole('alert')).textContent).toMatch(/Location access required/);
  });

  it('success shows the server row after re-reading it', async () => {
    m.getMyAttendance.mockResolvedValueOnce({ today: null }).mockResolvedValue({ today: IN });
    m.checkIn.mockResolvedValue({ message: 'Check-in recorded' });
    render(<AttendanceWidget />);
    fireEvent.click(await screen.findByRole('button', { name: /check in/i }));
    expect(await screen.findByText(/checked in at/i)).toBeTruthy();
    expect(m.getMyAttendance).toHaveBeenCalledTimes(2);
  });

  it('a failed check-out keeps the day open with the reason', async () => {
    m.getMyAttendance.mockResolvedValue({ today: IN });
    m.checkOut.mockRejectedValue(server500);
    render(<AttendanceWidget />);
    fireEvent.click(await screen.findByRole('button', { name: /check out/i }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByText(/day complete/i)).toBeNull();
    expect(m.checkOut).toHaveBeenCalledWith('AUDTA');
  });
});
