// ============================================================================
// IMS 2.0 - D86(3): a drawer with no comparison shows no cash figures
// ============================================================================
// A drawer counted after an older-version drawer (COMPARISON_UNAVAILABLE)
// used to show the WHOLE day's cash as its own "Cash sales"; the server now
// stores its cash figures as null (backend: test_audit_b12_budget_till_
// pettycash.py, d86 cases). HERE: the Blind EOD Tally and the reconciliation
// console say "Not available", never Rs 0 or a figure; a compared drawer still
// shows its own figure (control).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

vi.mock('../../../services/api/till', () => ({
  tillApi: {
    open: vi.fn(), blindSubmit: vi.fn(), lock: vi.fn(), reopen: vi.fn(),
    list: vi.fn(), get: vi.fn(), zread: vi.fn(),
  },
  paisaToInr: (p?: number | null) => `Rs ${((Math.round(Number(p) || 0)) / 100).toFixed(2)}`,
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u-mgr', id: 'u-mgr', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER', activeStoreId: 'BV-1' },
  }),
}));
const toast = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
const recon = vi.hoisted(() => ({ summary: vi.fn(), signoff: vi.fn() }));
vi.mock('../../../services/api/cashReconciliation', () => ({ cashReconciliationApi: recon }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: vi.fn(async () => ({ stores: [] })) } }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));

import BlindEodTallyPage from '../BlindEodTallyPage';
import CashReconciliationPage from '../CashReconciliationPage';
import { tillApi } from '../../../services/api/till';

const api = tillApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const today = new Date(Date.now() + 5.5 * 3600 * 1000).toISOString().slice(0, 10);

const drawer = (over: Record<string, unknown>) => ({
  session_id: 'TILL-C',
  store_id: 'BV-1',
  session_date: today,
  status: 'LOCKED' as const,
  shift: 'PM',
  opening_float_paisa: 0,
  opening_denominations: [],
  opened_at: `${today}T09:00:00`,
  blind_count_paisa: 50000,
  blind_denominations: [],
  zread_number: 'BV1/001',
  locked_at: `${today}T12:00:00`,
  locked_by: 'u-mgr',
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  api.zread.mockResolvedValue({});
});

describe('Blind EOD Tally - D86(3)', () => {
  it('an uncompared drawer shows its cash sales as not available', async () => {
    api.list.mockResolvedValue([drawer({
      variance_status: 'COMPARISON_UNAVAILABLE', expected_cash_paisa: null, variance_paisa: null,
      cash_sales_paisa: null, cash_refunds_paisa: null, cash_payouts_paisa: null,
    })]);
    render(<BlindEodTallyPage />);
    const label = await screen.findByText('Cash sales (gross)');
    const cell = label.parentElement as HTMLElement;
    expect(within(cell).getByText('Not available')).toBeTruthy();
    expect(screen.getAllByText('Not available')).toHaveLength(3);
  });

  it('CONTROL: a compared drawer shows its own cash sales', async () => {
    api.list.mockResolvedValue([drawer({
      variance_status: 'BALANCED', expected_cash_paisa: 300000, variance_paisa: 0,
      cash_sales_paisa: 300000, cash_refunds_paisa: 0, cash_payouts_paisa: 0,
    })]);
    render(<BlindEodTallyPage />);
    const label = await screen.findByText('Cash sales (gross)');
    expect(within(label.parentElement as HTMLElement).getByText('Rs 3000.00')).toBeTruthy();
    expect(screen.queryByText('Not available')).toBeNull();
  });
});

const row = (over: Record<string, unknown>) => ({
  session_id: 'TILL-C', source: 'BLIND_EOD', store_id: 'BV-1', store_name: 'BV-1',
  session_date: today, shift: 'PM', opening_float: 0, cash_sales: 3000, cash_refunds: 0,
  cash_expenses: 0, bank_deposit: 0, expected_cash: 3000, counted_cash: 500, blind: true,
  variance: null, variance_status: 'BALANCED', tolerance: 0, by_mode: {},
  signoff: { reviewed: false }, ...over,
});

describe('Cash reconciliation - D86(3)', () => {
  it('an uncompared drawer row says Not available for cash sales', async () => {
    recon.summary.mockResolvedValue({
      rows: [row({ cash_sales: null, cash_refunds: null, cash_expenses: null, expected_cash: null,
                   variance_status: 'COMPARISON_UNAVAILABLE' })],
      totals: { sessions: 1, cash_sales: 0 },
    });
    render(<CashReconciliationPage />);
    expect(await screen.findByText('Not available')).toBeTruthy();
    fireEvent.click(screen.getByText('BV-1'));
    expect(screen.getAllByText('Not available').length).toBeGreaterThanOrEqual(1);
  });

  it('CONTROL: a compared row shows its cash sales', async () => {
    recon.summary.mockResolvedValue({ rows: [row({})], totals: { sessions: 1, cash_sales: 3000 } });
    render(<CashReconciliationPage />);
    await screen.findByText('BV-1');
    expect(screen.queryByText('Not available')).toBeNull();
  });
});

describe('Cash reconciliation - D88(1)', () => {
  it('a close also counted on the till is marked and the footer says it is not added', async () => {
    recon.summary.mockResolvedValue({
      rows: [
        row({ session_id: 'TILL-B', counted_cash: 3000, expected_cash: 3000, variance: 0 }),
        row({ session_id: 'CR-1', source: 'CASH_REGISTER', blind: false, counted_cash: 3000,
              cash_sales: null, expected_cash: null, variance_status: 'COMPARISON_UNAVAILABLE',
              also_counted_on_till: true }),
      ],
      totals: { sessions: 1, also_counted_on_till: 1, cash_sales: 3000, counted_cash: 3000 },
    });
    render(<CashReconciliationPage />);
    expect(await screen.findByText('Also counted on the till')).toBeTruthy();
    expect(screen.getByText('1 also counted on the till, not added')).toBeTruthy();
  });

  it('CONTROL: an ordinary close carries no marker', async () => {
    recon.summary.mockResolvedValue({
      rows: [row({ session_id: 'CR-2', source: 'CASH_REGISTER', blind: false })],
      totals: { sessions: 1, also_counted_on_till: 0, cash_sales: 3000 },
    });
    render(<CashReconciliationPage />);
    await screen.findAllByText('BV-1');
    expect(screen.queryByText('Also counted on the till')).toBeNull();
    expect(screen.queryByText(/not added/)).toBeNull();
  });
});
