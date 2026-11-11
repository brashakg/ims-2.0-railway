// Audit L3-12 -- the Day-End Report and a drawer the till already counted.
//
// The blind count IS the close (owner ruling 2026-08-25). When the till was
// already counted (and locked) at Rs 1,000 and someone then typed Rs 3,000 on
// this report, the server used to store 3,000 here while the Z-Read held 1,000.
// The server now answers with the count that stands (backend:
// test_audit_b12_budget_till_pettycash.py). What is proved HERE is that the
// screen shows THAT figure and says why it is not the one that was typed --
// a number the closer did not enter must not appear unexplained.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn() },
  reportsApi: { getDayEndClose: vi.fn(), createDayEndClose: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-PUN-01', roles: ['STORE_MANAGER'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => ({ storeName: 'Better Vision', storeAddress: '' }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

import { orderApi, reportsApi } from '../../../services/api';
import DayEndReport from '../DayEndReport';

const createDayEndClose = reportsApi.createDayEndClose as unknown as ReturnType<typeof vi.fn>;
const getDayEndClose = reportsApi.getDayEndClose as unknown as ReturnType<typeof vi.fn>;
const getOrders = orderApi.getOrders as unknown as ReturnType<typeof vi.fn>;

const NOTE = 'day-end-count-from-till';

const closeRecord = (extra: Record<string, unknown>) => ({
  store_id: 'BV-PUN-01',
  date: '2026-11-04',
  closing_cash: 1000,
  system_cash: 4000,
  variance: -3000,
  notes: null,
  closed_by: 'u-1',
  closed_at: '2026-11-04T15:00:00',
  till_link_ok: true,
  ...extra,
});

beforeEach(() => {
  getOrders.mockImplementation(async () => ({ orders: [] }));
  getDayEndClose.mockImplementation(async () => ({ closed: false, close: null }));
});

async function typeAndClose(amount: string) {
  render(<DayEndReport />);
  await waitFor(() => expect(screen.getByText('Close Day')).toBeInTheDocument());
  const box = document.querySelector('input[type="number"]') as HTMLInputElement | null;
  if (box) fireEvent.change(box, { target: { value: amount } });
  fireEvent.click(screen.getByRole('button', { name: /Confirm Day Closing/i }));
  await waitFor(() => expect(createDayEndClose).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByText('Day Closed')).toBeInTheDocument());
}

describe('Day-End Report - the till was already counted', () => {
  it('shows the till count and says the typed amount was not used', async () => {
    createDayEndClose.mockImplementation(async () => ({
      closed: true,
      close: closeRecord({
        till_already_counted: true, counted_from_shared_record: true, till_count_differs: true,
      }),
    }));

    await typeAndClose('3000');

    // The count that stands, never the 3,000 that was typed.
    expect(screen.getByText(/Counted ₹1,000/)).toBeInTheDocument();
    expect(screen.queryByText(/Counted ₹3,000/)).not.toBeInTheDocument();
    expect(screen.getByTestId(NOTE).textContent).toMatch(/already counted at the Cash Register/i);
  });

  it('a reload of that closed day carries the same figure and the same note', async () => {
    getDayEndClose.mockImplementation(async () => ({
      closed: true,
      close: closeRecord({ till_already_counted: true, counted_from_shared_record: true }),
    }));
    render(<DayEndReport />);
    await waitFor(() => expect(screen.getByText('Day Closed')).toBeInTheDocument());
    expect(screen.getByText(/Counted ₹1,000/)).toBeInTheDocument();
    expect(screen.getByTestId(NOTE)).toBeInTheDocument();
  });

  it('POSITIVE CONTROL: a day this screen counted itself carries no such note', async () => {
    createDayEndClose.mockImplementation(async () => ({
      closed: true,
      close: closeRecord({
        closing_cash: 3000, system_cash: 3000, variance: 0,
        till_already_counted: false, counted_from_shared_record: false,
      }),
    }));

    await typeAndClose('3000');

    expect(screen.getByText(/Counted ₹3,000/)).toBeInTheDocument();
    expect(screen.queryByTestId(NOTE)).not.toBeInTheDocument();
  });
});
