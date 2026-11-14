// ============================================================================
// IMS 2.0 - blind EOD: a refused reopen tells the manager WHY
// ============================================================================
// Audit L3-28. Reopening a locked Z-Read while another drawer is open for the
// same shop and day was refused by the server with a 500 carrying the
// database's duplicate-key text, and the screen said only "Could not reopen".
// The server now answers 409 { code: 'another_drawer_open', message } (backend:
// test_audit_b12_budget_till_pettycash.py). HERE: the screen shows that
// sentence -- and still shows the plain fallback for a refusal that carries no
// sentence written for staff, so a bare server code never reaches a toast.

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

const LOCKED = {
  session_id: 'TILL-BV-1-AM',
  store_id: 'BV-1',
  session_date: '2026-11-04',
  status: 'LOCKED' as const,
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
  locked_at: '2026-11-04T08:00:00',
  locked_by: 'u-mgr',
};

const SENTENCE =
  'Another cash drawer is already open for this shop on 2026-11-04. Count and lock that drawer first, then reopen this Z-Read.';

beforeEach(() => {
  vi.clearAllMocks();
  api.list.mockResolvedValue([LOCKED]);
  api.zread.mockResolvedValue({});
});

async function reopenWithReason() {
  render(<BlindEodTallyPage />);
  const reason = await screen.findByPlaceholderText('Reason to reopen');
  fireEvent.change(reason, { target: { value: 'recount the morning drawer' } });
  fireEvent.click(screen.getByRole('button', { name: /reopen/i }));
  await waitFor(() => expect(api.reopen).toHaveBeenCalledWith('TILL-BV-1-AM', 'recount the morning drawer'));
}

describe('BlindEodTallyPage - a refused reopen', () => {
  it('shows the sentence the server wrote for the conflict', async () => {
    api.reopen.mockRejectedValue(
      new ApiError(SENTENCE, { status: 409, code: 'another_drawer_open' }),
    );
    await reopenWithReason();

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(SENTENCE));
    // The refused reopen is never reported as done.
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('keeps the plain fallback for a refusal that is only a server code', async () => {
    api.reopen.mockRejectedValue(new ApiError('not_locked', { status: 409 }));
    await reopenWithReason();

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not reopen'));
  });

  it('POSITIVE CONTROL: a reopen that works still says so', async () => {
    api.reopen.mockResolvedValue({ ...LOCKED, status: 'BLIND_SUBMITTED' });
    await reopenWithReason();

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Z-Read reopened'));
    expect(toast.error).not.toHaveBeenCalled();
  });
});
