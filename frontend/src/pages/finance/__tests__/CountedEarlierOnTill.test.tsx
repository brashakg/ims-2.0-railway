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

  it('says when expected cash starts from an earlier count, not the float typed', async () => {
    history(closed({ opening_float: 2000 }));
    render(<CashRegisterPage />);
    const tag = await screen.findByTestId('standing-count');
    // D120: the starting point can be an earlier session's sheet, so the tag
    // no longer calls it the till's count.
    expect(tag.textContent).toContain('Earlier count');
    expect(tag.textContent).not.toContain('Till count');
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

// D121: a later Cash Register close on an already counted drawer is never in
// the range totals (D88). The totals block must say so in words and show
// those closes' own differences, so a second-shift shortage cannot be missed.
describe('Cash reconciliation - later closes named beside the totals (D121)', () => {
  const totals = { sessions: 1, also_counted_on_till: 2, cash_sales: 3000, counted_cash: 3000 };
  const rows = [
    row({ session_id: 'CR-3', variance: 0, variance_status: 'BALANCED', counted_cash: 3500 }),
    row({ session_id: 'CR-2', variance: -500, variance_status: 'SHORTAGE', expected_cash: 3500 }),
    row({ session_id: 'CR-1', also_counted_on_till: false, variance: 0, variance_status: 'BALANCED' }),
  ];
  const lineText = async () =>
    ((await screen.findByTestId('later-closes')).textContent || '').replace(/\s+/g, ' ');

  it('says how many there are and their own shortage', async () => {
    recon.summary.mockResolvedValue({
      rows, totals: { ...totals, later_closes: 2, later_closes_short: 500, later_closes_over: 0 },
    });
    render(<CashReconciliationPage />);
    const text = await lineText();
    expect(text).toContain(
      '2 later Cash Register close(s) on an already counted drawer are on their own rows and not in these totals; their own differences:',
    );
    expect(text).toMatch(/500(\.00)? short/);
    expect(text).not.toContain('over');
    expect(text).toContain('The totals describe each drawer at its first count of the day.');
    expect(screen.getByTestId('later-closes').className).toContain('text-red-600');
  });

  it('keeps a shortage and an excess apart, so they cannot cancel', async () => {
    recon.summary.mockResolvedValue({
      rows, totals: { ...totals, later_closes: 2, later_closes_short: 500, later_closes_over: 500 },
    });
    render(<CashReconciliationPage />);
    expect(await lineText()).toMatch(/500(\.00)? short and .*500(\.00)? over/);
  });

  it('says "none" when the later closes all balanced', async () => {
    recon.summary.mockResolvedValue({
      rows, totals: { ...totals, later_closes: 2, later_closes_short: 0, later_closes_over: 0 },
    });
    render(<CashReconciliationPage />);
    expect(await lineText()).toContain('their own differences: none.');
    expect(screen.getByTestId('later-closes').className).not.toContain('text-red-600');
  });

  it('CONTROL: a range with no such close shows no such line', async () => {
    recon.summary.mockResolvedValue({
      rows: [rows[2]], totals: { ...totals, also_counted_on_till: 0, later_closes: 0 },
    });
    render(<CashReconciliationPage />);
    await screen.findByText(/Totals/);
    expect(screen.queryByTestId('later-closes')).toBeNull();
  });
});

// D128(1): a later register close SHOWS the drawer's earlier count that is
// beyond the band (its note is taken at the Z-Read lock). D127(2): a close
// not compared because an earlier close could not be placed says why.
// D128(2): a till row counted and not yet locked is on the console.
describe('Round 22 (D127, D128)', () => {
  it('shows the earlier count short, explained on the till', async () => {
    history(closed({ till_count_variance: -500, till_standing_count: 2500 }));
    render(<CashRegisterPage />);
    const tag = await screen.findByTestId('earlier-count-difference');
    expect(tag.textContent).toMatch(/Earlier count .*500(\.00)? short - explained on the till/);
  });

  it('says why nothing was compared after a close IMS could not place', async () => {
    history(closed({
      variance: null, variance_status: 'COMPARISON_UNAVAILABLE',
      comparison_withheld_reason: 'EARLIER_CLOSE_DRAWER_UNKNOWN',
      comparison_withheld_message: 'An earlier Cash Register close today could not be placed on one drawer.',
    }));
    render(<CashRegisterPage />);
    const chip = await screen.findByTestId('comparison-unavailable-verdict');
    expect(chip.getAttribute('title')).toContain('could not be placed on one drawer');
  });

  it('CONTROL: no earlier-count difference, no tag', async () => {
    history(closed({ till_count_variance: null }));
    render(<CashRegisterPage />);
    await screen.findByText('CR-2');
    expect(screen.queryByTestId('earlier-count-difference')).toBeNull();
  });

  it('a till row counted and not yet locked says so', async () => {
    recon.summary.mockResolvedValue({
      rows: [row({
        session_id: 'TILL-1', source: 'BLIND_EOD', blind: true, locked: false, zread_number: null,
        counted_cash: 2500, expected_cash: 3000, variance: -500, variance_status: 'SHORT',
        also_counted_on_till: false,
      })],
      totals: { sessions: 1, also_counted_on_till: 0, cash_sales: 3000, counted_cash: 2500 },
    });
    render(<CashReconciliationPage />);
    const line = await screen.findByTestId('zread-not-locked');
    expect(line.textContent).toContain('Counted, not locked yet');
  });
});
