// FE-3: GSTR-1 and GSTR-3B open on the IST month, not the UTC month.
import { it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.mock('../../../services/api', () => ({
  reportsApi: { getGSTR3BReport: vi.fn(), getGSTR3BGstnJson: vi.fn(), getGSTR1Report: vi.fn(), getGSTR1GstnJson: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-1', roles: ['ACCOUNTANT'], activeStoreId: 'S1' } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { GSTR3BReport } from '../GSTR3BReport';
import { GSTR1Report } from '../GSTR1Report';
import { reportsApi } from '../../../services/api';

const api = reportsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
beforeEach(() => {
  // Only the requested month is under test: never answer, never render a report.
  api.getGSTR3BReport.mockReturnValue(new Promise(() => {}));
  api.getGSTR1Report.mockReturnValue(new Promise(() => {}));
});
afterEach(() => vi.useRealTimers());

const reports = [['GSTR3B', GSTR3BReport, 'getGSTR3BReport'], ['GSTR1', GSTR1Report, 'getGSTR1Report']] as const;
const clocks = [
  ['2026-12-31T20:00:00Z', '2027-01'], // night: IST 1 Jan
  ['2026-12-31T09:00:00Z', '2026-12'], // day control
  ['2027-03-31T20:00:00Z', '2027-04'], // night: IST 1 Apr, new FY
] as const;

it.each(reports.flatMap(([name, Report, fn]) => clocks.map(([at, month]) => [name, at, month, Report, fn] as const)))(
  '%s at %s opens on %s',
  async (_name, at, month, Report, fn) => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(at));
    render(<Report />);
    await waitFor(() => expect(api[fn]).toHaveBeenCalled());
    expect(api[fn].mock.calls[0][0]).toBe(month);
  },
);
