// FE-5: screens that default to "today" use the IST day, not the UTC day.
// The petty-cash day settlement is the money one; the other screens are the
// same expression, found and verified in the same sweep.
import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const m = vi.hoisted(() => ({
  listDaily: vi.fn(), getSettings: vi.fn(), getUsers: vi.fn(), staffHistory: vi.fn(), getAttendance: vi.fn(),
  prod: vi.fn(), adperf: vi.fn(), promo: vi.fn(), settlePos: vi.fn(), settleList: vi.fn(),
}));
vi.mock('../../../services/api', () => ({
  incentiveApi: { getSettings: m.getSettings, listDaily: m.listDaily, createBulk: vi.fn(), deleteDaily: vi.fn(), getStaffHistory: m.staffHistory },
  walkoutsApi: { walkinsStatus: vi.fn().mockResolvedValue(null) },
  reportsApi: { getWorkshopProductivity: m.prod },
  hrApi: { getAttendance: m.getAttendance, checkIn: vi.fn(), checkOut: vi.fn() },
}));
vi.mock('../../../services/api/stores', () => ({ adminUserApi: { getUsers: m.getUsers } }));
vi.mock('../../../services/api/marketing', () => ({ marketingApi: { getAdPerformance: m.adperf } }));
vi.mock('../../../services/api/promotions', () => ({ promotionsApi: { report: m.promo } }));
vi.mock('../../../services/api/expenses', () => ({
  expensesApi: { getPettyCashSettlementPosition: m.settlePos, listPettyCashSettlements: m.settleList, settlePettyCashDay: vi.fn() },
}));
vi.mock('../expenses/expenseShared', async (orig) => ({
  ...((await orig()) as object), useExpensesContext: () => ({ canManageFloat: true }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', user_id: 'u1', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: 'BV-DHN-01' } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

import { DailyScorecardPage } from '../../incentive/DailyScorecardPage';
import { PointsHistoryPage } from '../../incentive/PointsHistoryPage';
import { AttendanceWidget } from '../../../components/hr/AttendanceWidget';
import { WorkshopProductivityCard } from '../../reports/sections/WorkshopProductivityCard';
import AdPerformancePage from '../../marketing/AdPerformancePage';
import PromotionsReportPage from '../../promotions/PromotionsReportPage';
import { DaySettlementSection } from '../expenses/DaySettlementSection';

beforeEach(() => {
  const s: Record<string, string> = {};
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => s[k] ?? null,
    setItem: (k: string, v: string) => { s[k] = v; },
    removeItem: (k: string) => { delete s[k]; },
  });
  m.listDaily.mockResolvedValue({ items: [], rows: [], visibility: 'all' });
  m.getSettings.mockResolvedValue({ store_id: 'x', eligibility_bands: [], visibility: 'all' });
  m.getUsers.mockResolvedValue({ users: [] });
  m.staffHistory.mockResolvedValue({ items: [] });
  m.getAttendance.mockResolvedValue([]);
  m.prod.mockResolvedValue({ technicians: [], totals: {} });
  // Only the request's dates are under test: these two never answer, so the
  // screens never render a result shape this file does not build.
  m.adperf.mockReturnValue(new Promise(() => {}));
  m.promo.mockReturnValue(new Promise(() => {}));
  m.settlePos.mockResolvedValue({});
  m.settleList.mockResolvedValue(null);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const CLOCKS = [
  ['2026-12-31T20:00:00Z', '2027-01-01'], // night: IST 1 Jan
  ['2026-12-31T09:00:00Z', '2026-12-31'], // day control
] as const;

function at(clock: string) {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(clock));
}

it.each(CLOCKS)('day settlement at %s settles %s', async (clock, day) => {
  at(clock);
  render(<DaySettlementSection />);
  await waitFor(() => expect(m.settlePos).toHaveBeenCalled());
  expect(m.settlePos.mock.calls[0][1]).toBe(day);
});

it.each(CLOCKS)('daily scorecard at %s opens on %s', async (clock, day) => {
  at(clock);
  render(<MemoryRouter><DailyScorecardPage /></MemoryRouter>);
  await waitFor(() => expect(m.listDaily).toHaveBeenCalled());
  expect(m.listDaily.mock.calls[0][0]).toBe(day);
});

it.each(CLOCKS)('points history at %s runs to %s', async (clock, day) => {
  at(clock);
  render(
    <MemoryRouter initialEntries={['/h/S1']}>
      <Routes><Route path="/h/:staffId" element={<PointsHistoryPage />} /></Routes>
    </MemoryRouter>,
  );
  await waitFor(() => expect(m.staffHistory).toHaveBeenCalled());
  expect(m.staffHistory.mock.calls[0][2]).toBe(day);
});

it.each(CLOCKS)('attendance at %s reads %s', async (clock, day) => {
  at(clock);
  render(<AttendanceWidget />);
  await waitFor(() => expect(m.getAttendance).toHaveBeenCalled());
  expect(m.getAttendance.mock.calls[0][1]).toBe(day);
});

it.each(CLOCKS)('workshop productivity at %s runs to %s', async (clock, day) => {
  at(clock);
  render(<WorkshopProductivityCard storeId="S" />);
  await waitFor(() => expect(m.prod).toHaveBeenCalled());
  expect(JSON.stringify(m.prod.mock.calls[0])).toContain(day);
});

it.each(CLOCKS)('ad performance at %s runs to %s', async (clock, day) => {
  at(clock);
  render(<AdPerformancePage />);
  await waitFor(() => expect(m.adperf).toHaveBeenCalled());
  expect(m.adperf.mock.calls[0][0].to).toBe(day);
});

it.each(CLOCKS)('promotions report at %s runs to %s', async (clock, day) => {
  at(clock);
  render(<MemoryRouter><PromotionsReportPage /></MemoryRouter>);
  await waitFor(() => expect(m.promo).toHaveBeenCalled());
  expect(m.promo.mock.calls[0][0].end_date).toBe(day);
});
