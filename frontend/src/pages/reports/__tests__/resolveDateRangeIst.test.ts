// IST clock sweep lane B, FE-2: the Reports date filter's "today" is the IST
// business day. NIGHT 2026-12-31T20:00Z is 1 Jan 01:30 IST; DAY is the control.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { resolveDateRange } from '../reportsQueries';

function at(iso: string) {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(iso));
}

afterEach(() => vi.useRealTimers());

describe('resolveDateRange reads the IST day', () => {
  it('night: 01:30 IST on 1 Jan is 1 Jan, not 31 Dec', () => {
    at('2026-12-31T20:00:00Z');
    expect(resolveDateRange('today')).toEqual({ startDate: '2027-01-01', endDate: '2027-01-01' });
    expect(resolveDateRange('week')).toEqual({ startDate: '2026-12-25', endDate: '2027-01-01' });
    expect(resolveDateRange('month')).toEqual({ startDate: '2026-12-01', endDate: '2027-01-01' });
    expect(resolveDateRange('quarter')).toEqual({ startDate: '2026-10-01', endDate: '2027-01-01' });
  });

  it('day control: same UTC date, afternoon IST', () => {
    at('2026-12-31T09:00:00Z');
    expect(resolveDateRange('today')).toEqual({ startDate: '2026-12-31', endDate: '2026-12-31' });
    expect(resolveDateRange('week')).toEqual({ startDate: '2026-12-24', endDate: '2026-12-31' });
  });

  it('a month back from 31 Mar is 28 Feb, not 3 Mar', () => {
    at('2027-03-31T09:00:00Z');
    expect(resolveDateRange('month').startDate).toBe('2027-02-28');
    expect(resolveDateRange('quarter').startDate).toBe('2026-12-31');
  });
});
