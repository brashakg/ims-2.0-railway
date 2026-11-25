// PR #1421, Codex round 3 (F-1421-R3-A) and the read-fault hunt.
//
// 1. A Cash Register close that was saved but whose reply was lost: the next
//    press is told 409 and now receives the SAVED close. The screen used to
//    toast "Session already closed" and leave the count form up over a
//    register that was closed.
// 2. A failed load is not an empty screen. Cash Register used to show "Open
//    till" and "No cash register sessions yet"; the Blind EOD Tally used to
//    show a shop-day with no drawer and offer to open one.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../services/api/cashRegister', () => ({
  cashRegisterApi: { sessions: vi.fn(), open: vi.fn(), close: vi.fn() },
}));
vi.mock('../../../services/api/till', () => ({
  tillApi: {
    open: vi.fn(), blindSubmit: vi.fn(), lock: vi.fn(), reopen: vi.fn(),
    list: vi.fn(), get: vi.fn(), zread: vi.fn(),
  },
  paisaToInr: (p?: number | null) => `Rs ${((Math.round(Number(p) || 0)) / 100).toFixed(2)}`,
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-sm', id: 'u-sm', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: 'ZZ-SOLO' },
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

import CashRegisterPage from '../CashRegisterPage';
import BlindEodTallyPage from '../BlindEodTallyPage';
import { cashRegisterApi } from '../../../services/api/cashRegister';
import { tillApi } from '../../../services/api/till';
import { ApiError } from '../../../services/api/client';

const api = cashRegisterApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const till = tillApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const OPEN = {
  session_id: 'ZZ-CR-B',
  store_id: 'ZZ-SOLO',
  status: 'OPEN' as const,
  opening_float: 0,
  opening_denominations: [],
  opened_at: '2026-09-23T09:00:00+05:30',
};
const SAVED = {
  ...OPEN,
  status: 'CLOSED' as const,
  closed_at: '2026-09-23T21:00:00+05:30',
  counted: 3000,
  expected: 4000,
  variance: null,
  variance_status: 'SEVERAL_DRAWERS' as const,
  till_link_ok: false,
  till_drawers_on_day: 2,
  manager_followup: { pending: false, tasks: [{ kind: 'uncounted' }] },
};
const alreadyClosed = (session?: object) =>
  new ApiError('Session already closed', {
    status: 409,
    detail: { message: 'Session already closed', ...(session ? { session } : {}) },
  });

beforeEach(() => vi.clearAllMocks());

describe('Cash Register - the close was already saved', () => {
  it('shows the saved close and says the drawer still has to be counted on the till', async () => {
    api.sessions
      .mockResolvedValueOnce({ sessions: [], open_session: OPEN, expected_preview: null })
      .mockResolvedValue({ sessions: [SAVED], open_session: null, expected_preview: null });
    api.close.mockRejectedValue(alreadyClosed(SAVED));
    render(<CashRegisterPage />);

    fireEvent.click(await screen.findByRole('button', { name: /Close & Reconcile/i }));
    await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1));
    const said = String(toast.warning.mock.calls[0][0]);
    expect(said).toMatch(/already closed/);
    expect(said).toMatch(/did not reach the till/);
    expect(said).toMatch(/Blind EOD Tally \(Z-Read\)/);
    expect(toast.error).not.toHaveBeenCalled();
    // The list is read again: the count form is gone and the saved close shows.
    await waitFor(() => expect(api.sessions).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('ZZ-CR-B')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Close & Reconcile/i })).not.toBeInTheDocument();
  });

  it('a saved close that reached the till is reported plainly', async () => {
    api.sessions
      .mockResolvedValueOnce({ sessions: [], open_session: OPEN, expected_preview: null })
      .mockResolvedValue({ sessions: [], open_session: null, expected_preview: null });
    api.close.mockRejectedValue(
      alreadyClosed({ ...SAVED, till_link_ok: true, variance: 0, variance_status: 'BALANCED' }),
    );
    render(<CashRegisterPage />);

    fireEvent.click(await screen.findByRole('button', { name: /Close & Reconcile/i }));
    await waitFor(() => expect(toast.info).toHaveBeenCalledTimes(1));
    expect(String(toast.info.mock.calls[0][0])).toMatch(/already closed/);
    expect(toast.warning).not.toHaveBeenCalled();
    await waitFor(() => expect(api.sessions).toHaveBeenCalledTimes(2));
  });

  it('control: any other refusal keeps the count form and shows the server sentence', async () => {
    api.sessions.mockResolvedValue({ sessions: [], open_session: OPEN, expected_preview: null });
    api.close.mockRejectedValue(
      new ApiError('The register was not closed. Try again in a moment.', { status: 503 }),
    );
    render(<CashRegisterPage />);

    fireEvent.click(await screen.findByRole('button', { name: /Close & Reconcile/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    expect(api.sessions).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /Close & Reconcile/i })).toBeInTheDocument();
  });
});

describe('a failed load is not an empty screen', () => {
  it('Cash Register: no Open till form, no empty history; Try again recovers', async () => {
    api.sessions
      .mockRejectedValueOnce(new ApiError('The cash register could not be read just now.', { status: 503 }))
      .mockResolvedValue({ sessions: [], open_session: OPEN, expected_preview: null });
    render(<CashRegisterPage />);

    expect(await screen.findByTestId('cash-register-load-failed')).toHaveTextContent(
      /could not be loaded/,
    );
    expect(screen.queryByRole('button', { name: /Open Cash Register/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/No cash register sessions yet/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Try again/i }));
    expect(await screen.findByRole('button', { name: /Close & Reconcile/i })).toBeInTheDocument();
    expect(screen.queryByTestId('cash-register-load-failed')).not.toBeInTheDocument();
  });

  it('Blind EOD Tally: no "open a drawer" form; Try again recovers', async () => {
    till.list
      .mockRejectedValueOnce(new ApiError('The till could not be read just now.', { status: 503 }))
      .mockResolvedValue([]);
    render(<BlindEodTallyPage />);

    expect(await screen.findByTestId('till-load-failed')).toHaveTextContent(/could not be loaded/);
    expect(screen.queryByText(/No till sessions today/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Try again/i }));
    expect(await screen.findByText(/No till sessions today/)).toBeInTheDocument();
    expect(screen.queryByTestId('till-load-failed')).not.toBeInTheDocument();
  });

  it('Blind EOD Tally control: a permissions refusal keeps its empty view', async () => {
    till.list.mockRejectedValue(new ApiError('not permitted', { status: 403 }));
    render(<BlindEodTallyPage />);

    expect(await screen.findByText(/No till sessions today/)).toBeInTheDocument();
    expect(screen.queryByTestId('till-load-failed')).not.toBeInTheDocument();
  });
});
