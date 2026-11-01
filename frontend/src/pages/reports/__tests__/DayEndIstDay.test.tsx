// FE-4: the day-end report opens on the IST business day, and that day is
// selectable (the date input's max is the IST day too).
import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.mock('../../../services/api', () => ({
  orderApi: { getOrders: vi.fn() },
  reportsApi: { getDayEndClose: vi.fn(), createDayEndClose: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-PUN-01', storeIds: ['BV-PUN-01'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => ({ name: 'Better Vision', address: '', phone: '', gstin: '' }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));

import { orderApi, reportsApi } from '../../../services/api';
import DayEndReport from '../DayEndReport';

const getOrders = orderApi.getOrders as unknown as ReturnType<typeof vi.fn>;
const getClose = reportsApi.getDayEndClose as unknown as ReturnType<typeof vi.fn>;
beforeEach(() => {
  getOrders.mockResolvedValue({ orders: [] });
  getClose.mockResolvedValue({ closed: false, close: null });
});
afterEach(() => vi.useRealTimers());

it.each([
  ['2026-12-31T20:00:00Z', '2027-01-01'], // night: IST 1 Jan
  ['2026-12-31T09:00:00Z', '2026-12-31'], // day control
])('at %s it opens on %s', async (at, day) => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(at));
  const { container } = render(<DayEndReport />);
  await waitFor(() => expect(getOrders).toHaveBeenCalled());
  expect(getOrders.mock.calls[0][0]).toMatchObject({ from_date: day, to_date: day });
  const input = container.querySelector('input[type="date"]') as HTMLInputElement;
  expect(input.value).toBe(day);
  expect(input.max).toBe(day);
});
