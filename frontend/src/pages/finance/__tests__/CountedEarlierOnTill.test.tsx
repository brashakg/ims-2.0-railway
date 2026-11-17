// PR #1421, lead decision D111: a Cash Register session that opened on a
// drawer already counted that day.
//
// * No count of its own: the server stores no comparison (COUNTED_EARLIER).
//   The screens used to have no word for it, so the row would have read
//   "Not counted" beside a counted figure. They now say "Counted earlier
//   today on the till", on Cash Register and on the reconciliation console.
// * Its own count: expected cash starts from the count standing on the till,
//   not from the float typed at opening. When the two differ the Cash
//   Register row says which figure the expected cash starts from.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../services/api/cashRegister', () => ({
  cashRegisterApi: { sessions: vi.fn(), open: vi.fn(), close: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-sm', id: 'u-sm', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: 'BV-1' },
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
const recon = vi.hoisted(() => ({ summary: vi.fn(), signoff: vi.fn() }));
vi.mock('../../../services/api/cashReconciliation', () => ({ cashReconciliationApi: recon }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: vi.fn(async () => ({ stores: [] })) } }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));

import CashRegisterPage from '../CashRegisterPage';
import CashReconciliationPage from '../CashReconciliationPage';
import { cashRegisterApi } from '../../../services/api/cashRegister';

const api = cashRegisterApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const today = new Date(Date.now() + 5.5 * 3600 * 1000).toISOString().slice(0, 10);

const closed = (over: Record<string, unknown>) => ({
  session_id: 'CR-2',
  store_id: 'BV-1',
  status: 'CLOSED' as const,
  opening_float: 3000,
  opening_denominations: [],
  opened_at: `${today}T12:30:00`,
  closed_at: `${today}T13:00:00`,
  counted: 3500,
  expected: 3500,
  variance: 0,
  variance_status: 'BALANCED' as const,
  till_link_ok: true,
  till_counted_earlier: true,
  till_standing_count: 3000,
  ...over,
});

const history = (session: object) =>
  api.sessions.mockResolvedValue({ sessions: [session], open_session: null, expected_preview: null });

beforeEach(() => vi.clearAllMocks());

describe('Cash Register - a session on a drawer counted earlier (D111)', () => {
  it('with no count of its own says so, not "Not counted"', async () => {
    history(closed({
      counted: 3000, variance: null, variance_status: 'COUNTED_EARLIER',
      counted_from_shared_record: true,
    }));
    render(<CashRegisterPage />);
    const chip = await screen.findByTestId('counted-earlier-verdict');
    expect(chip.textContent).toBe('Counted earlier today on the till');
    expect(screen.queryByText('Not counted')).toBeNull();
    // The count shown is the one standing on the till, not one taken at Day-End.
    expect(screen.queryByTestId('counted-at-day-end')).toBeNull();
  });

  it('says when expected cash starts from the till count, not the float typed', async () => {
    history(closed({ opening_float: 2000 }));
    render(<CashRegisterPage />);
    const tag = await screen.findByTestId('standing-count');
    expect(tag.textContent).toContain('Till count');
    expect(tag.textContent).toContain('3,000');
  });

  it('CONTROL: the float typed equals the till count - no extra tag', async () => {
    history(closed({}));
    render(<CashRegisterPage />);
    await screen.findByText('CR-2');
    expect(screen.queryByTestId('standing-count')).toBeNull();
    expect(screen.queryByTestId('counted-earlier-verdict')).toBeNull();
  });
});

const row = (over: Record<string, unknown>) => ({
  session_id: 'CR-2', source: 'CASH_REGISTER', store_id: 'BV-1', store_name: 'BV-1',
  session_date: today, shift: 'PM', opening_float: 3000, cash_sales: 500, cash_refunds: 0,
  cash_expenses: 0, bank_deposit: 0, expected_cash: 3500, counted_cash: 3000, blind: false,
  variance: null, variance_status: 'COUNTED_EARLIER', tolerance: 0, by_mode: {},
  signoff: { reviewed: false }, also_counted_on_till: true, ...over,
});

describe('Cash reconciliation - a session on a drawer counted earlier (D111)', () => {
  it('a session with no count of its own reads "Counted earlier today on the till"', async () => {
    recon.summary.mockResolvedValue({
      rows: [row({})],
      totals: { sessions: 0, also_counted_on_till: 1, cash_sales: 0, counted_cash: 0 },
    });
    render(<CashReconciliationPage />);
    expect(await screen.findByText('Counted earlier today on the till')).toBeTruthy();
    expect(screen.queryByText('Not counted')).toBeNull();
  });

  it('CONTROL: a close nobody counted still reads "Not counted"', async () => {
    recon.summary.mockResolvedValue({
      rows: [row({ counted_cash: null, variance_status: 'NOT_COUNTED', also_counted_on_till: false })],
      totals: { sessions: 1, also_counted_on_till: 0, cash_sales: 500, counted_cash: 0 },
    });
    render(<CashReconciliationPage />);
    expect((await screen.findAllByText('Not counted')).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('Counted earlier today on the till')).toBeNull();
  });
});
