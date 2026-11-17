// ============================================================================
// IMS 2.0 - blind EOD: a write that FAILED reloads the drawers
// ============================================================================
// PR #1421 round 15. A lock or reopen can land on the till while its reply is
// lost (the server answers 500). The screen said "Could not lock" / "Could
// not reopen" and kept showing the drawer as it was BEFORE the press: a
// reopened Z-Read still read LOCKED with a Reopen button. The server now
// repairs what such a write owes (backend:
// test_audit_b12_budget_till_pettycash.py, TestR12...). HERE: after a failed
// write the screen reads the drawers again and shows what the till holds.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/till', () => ({
  tillApi: {
    open: vi.fn(), blindSubmit: vi.fn(), lock: vi.fn(), reopen: vi.fn(),
    list: vi.fn(), get: vi.fn(), zread: vi.fn(),
  },
  paisaToInr: (p?: number | null) => `Rs ${((Math.round(Number(p) || 0)) / 100).toFixed(2)}`,
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-mgr', roles: ['STORE_MANAGER'], activeStoreId: 'BV-1' },
  }),
}));
const toast = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

import BlindEodTallyPage from '../BlindEodTallyPage';
import { tillApi } from '../../../services/api/till';
import { ApiError } from '../../../services/api/client';

const api = tillApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const COUNTED = {
  session_id: 'TILL-BV-1-AM',
  store_id: 'BV-1',
  session_date: '2026-11-04',
  status: 'BLIND_SUBMITTED' as const,
  shift: 'AM',
  opening_float_paisa: 0,
  opening_denominations: [],
  opened_at: '2026-11-04T04:00:00',
  blind_count_paisa: 100000,
  blind_denominations: [],
  expected_cash_paisa: 100000,
  variance_paisa: 0,
  variance_status: 'BALANCED' as const,
  zread_number: 'BV1/2026-11-04/001',
};
const LOCKED = {
  ...COUNTED,
  status: 'LOCKED' as const,
  locked_at: '2026-11-04T08:00:00',
  locked_by: 'u-mgr',
};

beforeEach(() => {
  vi.clearAllMocks();
  api.zread.mockResolvedValue({});
});

describe('BlindEodTallyPage - a write whose reply was lost', () => {
  it('a failed reopen reloads: the Z-Read the till reopened shows as awaiting lock', async () => {
    api.list.mockResolvedValueOnce([LOCKED]).mockResolvedValue([COUNTED]);
    api.reopen.mockRejectedValue(new ApiError('reopen_failed', { status: 500 }));
    render(<BlindEodTallyPage />);
    fireEvent.change(await screen.findByPlaceholderText('Reason to reopen'), {
      target: { value: 'recount' },
    });
    fireEvent.click(screen.getByRole('button', { name: /reopen/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not reopen'));
    expect(await screen.findByRole('button', { name: /lock z-read/i })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Reason to reopen')).not.toBeInTheDocument();
    expect(api.list).toHaveBeenCalledTimes(2);
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('a failed lock reloads: the drawer the till locked shows as locked', async () => {
    api.list.mockResolvedValueOnce([COUNTED]).mockResolvedValue([LOCKED]);
    api.lock.mockRejectedValue(new ApiError('lock_failed', { status: 500 }));
    render(<BlindEodTallyPage />);
    fireEvent.click(await screen.findByRole('button', { name: /lock z-read/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not lock'));
    expect(await screen.findByPlaceholderText('Reason to reopen')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /lock z-read/i })).not.toBeInTheDocument();
    expect(api.list).toHaveBeenCalledTimes(2);
  });

  it('a write that did not land reloads to the same drawer', async () => {
    api.list.mockResolvedValue([LOCKED]);
    api.reopen.mockRejectedValue(new ApiError('reopen_failed', { status: 500 }));
    render(<BlindEodTallyPage />);
    fireEvent.change(await screen.findByPlaceholderText('Reason to reopen'), {
      target: { value: 'recount' },
    });
    fireEvent.click(screen.getByRole('button', { name: /reopen/i }));

    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
    expect(await screen.findByPlaceholderText('Reason to reopen')).toBeInTheDocument();
  });
});
