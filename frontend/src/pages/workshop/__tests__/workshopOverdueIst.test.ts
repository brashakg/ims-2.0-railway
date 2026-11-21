// L2-WS-F02 follow-up: promisedDate is now filled from expected_date, so the
// board's overdue rule is live. It must match the backend's find_overdue
// (late only from the next IST day), not compare a UTC-midnight instant.
import { describe, it, expect } from 'vitest';
import { isPastDueIst } from '../shared';

// 10:00 IST on 9 Nov 2026 = 04:30 UTC.
const TEN_AM_IST = new Date('2026-11-09T04:30:00Z');

describe('isPastDueIst', () => {
  it('a job due today (IST) is not overdue at 10:00 IST', () => {
    expect(isPastDueIst('2026-11-09', TEN_AM_IST)).toBe(false);
  });
  it('a job due today is not overdue just before IST midnight', () => {
    expect(isPastDueIst('2026-11-09', new Date('2026-11-09T18:29:00Z'))).toBe(false);
  });
  it('a job due yesterday (IST) is overdue', () => {
    expect(isPastDueIst('2026-11-08', TEN_AM_IST)).toBe(true);
  });
  it('becomes overdue at IST midnight, not UTC midnight', () => {
    expect(isPastDueIst('2026-11-09', new Date('2026-11-09T18:31:00Z'))).toBe(true);
  });
  it('a full ISO timestamp is read as its IST day', () => {
    // 2026-11-08T20:00Z is 01:30 IST on 9 Nov -> due today, not overdue.
    expect(isPastDueIst('2026-11-08T20:00:00Z', TEN_AM_IST)).toBe(false);
  });
  it('no promise date on file is not overdue', () => {
    expect(isPastDueIst(null, TEN_AM_IST)).toBe(false);
    expect(isPastDueIst(undefined, TEN_AM_IST)).toBe(false);
    expect(isPastDueIst('', TEN_AM_IST)).toBe(false);
  });
});
