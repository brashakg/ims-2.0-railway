// PR #1421 review of head 4b470d57: a close whose count did NOT reach the till.
//
// When the till link fails (for example the day's drawers could not be read),
// the drawer is still OPEN on the till and nothing has checked it. Both close
// doors used to say the opposite: Cash Register toasted "each drawer's own
// count and variance is recorded" and Day-End said "Shown for each drawer
// below" over an empty list. Staff were told the drawer was recorded, so
// nobody counted it on the Z-Read. And the reconciliation console turned a
// failed read into "No closed cash sessions in this range".

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

vi.mock('../../../services/api/cashRegister', () => ({
  cashRegisterApi: { sessions: vi.fn(), open: vi.fn(), close: vi.fn() },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-sm', id: 'u-sm', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: 'ZZ-SOLO' },
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));
vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => ({ storeName: 'Better Vision', storeAddress: '' }),
}));
vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn() },
  reportsApi: { getDayEndClose: vi.fn(), createDayEndClose: vi.fn() },
}));
const recon = vi.hoisted(() => ({ summary: vi.fn(), signoff: vi.fn() }));
vi.mock('../../../services/api/cashReconciliation', () => ({ cashReconciliationApi: recon }));

import CashRegisterPage from '../CashRegisterPage';
import CashReconciliationPage from '../CashReconciliationPage';
import DayEndReport from '../../reports/DayEndReport';
import { cashRegisterApi } from '../../../services/api/cashRegister';
import { orderApi, reportsApi } from '../../../services/api';

const api = cashRegisterApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const createDayEndClose = reportsApi.createDayEndClose as unknown as ReturnType<typeof vi.fn>;
const getDayEndClose = reportsApi.getDayEndClose as unknown as ReturnType<typeof vi.fn>;
const getOrders = orderApi.getOrders as unknown as ReturnType<typeof vi.fn>;

const OPEN = {
  session_id: 'ZZ-CR-OPEN',
  store_id: 'ZZ-SOLO',
  status: 'OPEN' as const,
  opening_float: 0,
  opening_denominations: [],
  opened_at: '2026-08-24T09:00:00+05:30',
};

// Codex's F-A sequence after the fix: B's count never reached the till.
const CLOSED_LINK_FAILED = {
  ...OPEN,
  session_id: 'ZZ-CR-B',
  status: 'CLOSED' as const,
  closed_at: '2026-08-24T21:00:00+05:30',
  counted: 3000,
  expected: 4000,
  variance: null,
  variance_status: 'SEVERAL_DRAWERS' as const,
  till_link_ok: false,
  till_link_error: 'drawer_read_failed',
  till_drawers_on_day: 2,
  till_drawers: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  getOrders.mockImplementation(async () => ({ orders: [] }));
  getDayEndClose.mockImplementation(async () => ({ closed: false, close: null }));
});

describe('Cash Register - a count that did not reach the till', () => {
  it('the close toast says to count the drawer on the Z-Read, never that it is recorded', async () => {
    api.sessions.mockResolvedValue({ sessions: [], open_session: OPEN, expected_preview: null });
    api.close.mockResolvedValue(CLOSED_LINK_FAILED);
    render(<CashRegisterPage />);

    fireEvent.click(await screen.findByRole('button', { name: /Close & Reconcile/i }));
    await waitFor(() => expect(api.close).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1));
    const said = String(toast.warning.mock.calls[0][0]);
    expect(said).toMatch(/did not reach the till/);
    expect(said).toMatch(/Z-Read/);
    expect(said).not.toMatch(/recorded/);
  });

  it('the history row says the count is not on the till', async () => {
    api.sessions.mockResolvedValue({ sessions: [CLOSED_LINK_FAILED], open_session: null, expected_preview: null });
    render(<CashRegisterPage />);

    const row = within((await screen.findByText('ZZ-CR-B')).closest('tr') as HTMLElement);
    expect(row.getByTestId('count-not-on-till')).toHaveTextContent(/Z-Read/);
  });

  it('control: a close that reached the till shows no such notice', async () => {
    api.sessions.mockResolvedValue({
      sessions: [{ ...CLOSED_LINK_FAILED, till_link_ok: true, till_link_error: null }],
      open_session: null,
      expected_preview: null,
    });
    render(<CashRegisterPage />);

    await screen.findByText('ZZ-CR-B');
    expect(screen.queryByTestId('count-not-on-till')).not.toBeInTheDocument();
  });
});

describe('Day-End - a count that did not reach the till', () => {
  const record = (extra: Record<string, unknown>) => ({
    store_id: 'ZZ-SOLO',
    date: '2026-08-24',
    closing_cash: 3000,
    system_cash: 4000,
    variance: null,
    closed_at: '2026-08-24T15:00:00',
    till_link_ok: false,
    till_link_error: 'drawer_read_failed',
    till_drawers_on_day: 2,
    till_drawers: null,
    variance_withheld_several_drawers: true,
    ...extra,
  });

  it('says to count on the Z-Read, not "Shown for each drawer below" over nothing', async () => {
    createDayEndClose.mockImplementation(async () => ({ closed: true, close: record({}) }));
    render(<DayEndReport />);
    fireEvent.click(await screen.findByRole('button', { name: /Confirm Day Closing/i }));
    await waitFor(() => expect(screen.getByText('Day Closed')).toBeInTheDocument());

    expect(screen.getByTestId('day-end-count-not-on-till')).toHaveTextContent(/Z-Read/);
    expect(screen.queryByText('Shown for each drawer below')).not.toBeInTheDocument();
    expect(toast.warning).toHaveBeenCalledTimes(1);
    expect(String(toast.warning.mock.calls[0][0])).toMatch(/did not reach the till/);
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('control: a close that reached the till keeps its per-drawer wording', async () => {
    createDayEndClose.mockImplementation(async () => ({
      closed: true,
      close: record({ till_link_ok: true, till_link_error: null, till_drawers: [] }),
    }));
    render(<DayEndReport />);
    fireEvent.click(await screen.findByRole('button', { name: /Confirm Day Closing/i }));
    await waitFor(() => expect(screen.getByText('Day Closed')).toBeInTheDocument());

    expect(screen.queryByTestId('day-end-count-not-on-till')).not.toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledTimes(1);
  });
});

describe('Cash reconciliation - a failed read is not an empty range', () => {
  it('shows a read error row, never "No closed cash sessions in this range"', async () => {
    recon.summary.mockRejectedValue(Object.assign(new Error('Closes could not be read'), { response: { status: 503 } }));
    render(<CashReconciliationPage />);

    expect(await screen.findByTestId('recon-load-error')).toHaveTextContent(/could not be read/);
    expect(screen.queryByText('No closed cash sessions in this range.')).not.toBeInTheDocument();
  });

  it('control: a range that really has no closes still says so', async () => {
    recon.summary.mockResolvedValue({ rows: [], totals: null });
    render(<CashReconciliationPage />);

    expect(await screen.findByText('No closed cash sessions in this range.')).toBeInTheDocument();
    expect(screen.queryByTestId('recon-load-error')).not.toBeInTheDocument();
  });
});
