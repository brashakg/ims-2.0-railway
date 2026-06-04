import { describe, it, expect } from 'vitest';
import {
  toDate,
  formatDateTimeIST,
  formatDateIST,
  formatTimeIST,
  IST_TZ,
} from '../datetime';

describe('toDate', () => {
  it('returns null for empty-ish inputs', () => {
    expect(toDate(null)).toBeNull();
    expect(toDate(undefined)).toBeNull();
    expect(toDate('')).toBeNull();
    expect(toDate('   ')).toBeNull();
  });

  it('returns a valid Date instance as-is, but null for an invalid Date', () => {
    const d = new Date('2026-05-22T10:00:00Z');
    expect(toDate(d)).toBe(d);
    expect(toDate(new Date('not-a-date'))).toBeNull();
  });

  it('accepts numeric epoch millis', () => {
    const ms = Date.UTC(2026, 4, 22, 16, 28, 24); // 2026-05-22 16:28:24 UTC
    const d = toDate(ms);
    expect(d).toBeInstanceOf(Date);
    expect(d!.getTime()).toBe(ms);
  });

  it('treats a NAIVE backend timestamp as UTC (the key bug this module fixes)', () => {
    // No tz designator -> must be interpreted as UTC, NOT browser-local.
    const d = toDate('2026-05-22T16:28:24.836000');
    expect(d!.toISOString()).toBe('2026-05-22T16:28:24.836Z');
  });

  it('normalizes a space-separated naive timestamp ("YYYY-MM-DD HH:MM:SS") to UTC', () => {
    const d = toDate('2026-05-22 16:28:24');
    expect(d!.toISOString()).toBe('2026-05-22T16:28:24.000Z');
  });

  it('respects an explicit Z / offset designator rather than re-stamping it', () => {
    expect(toDate('2026-05-22T16:28:24Z')!.toISOString()).toBe('2026-05-22T16:28:24.000Z');
    // +05:30 offset means the equivalent UTC instant is 5.5h earlier
    expect(toDate('2026-05-22T16:28:24+05:30')!.toISOString()).toBe('2026-05-22T10:58:24.000Z');
  });

  it('returns null for garbage strings', () => {
    expect(toDate('hello world')).toBeNull();
  });
});

describe('IST formatters', () => {
  // 16:28 UTC == 21:58 IST (UTC+5:30) on the same day.
  const naive = '2026-05-22T16:28:24.836000';

  it('exposes the Asia/Kolkata timezone constant', () => {
    expect(IST_TZ).toBe('Asia/Kolkata');
  });

  it('formatDateTimeIST renders the IST date and shifted time', () => {
    const out = formatDateTimeIST(naive);
    expect(out).toContain('22');
    expect(out).toContain('May');
    expect(out).toContain('2026');
    // 16:28 UTC -> 9:58 pm IST
    expect(out.toLowerCase()).toContain('9:58');
    expect(out.toLowerCase()).toContain('pm');
  });

  it('formatDateIST renders date only (no time)', () => {
    const out = formatDateIST(naive);
    expect(out).toContain('22');
    expect(out).toContain('May');
    expect(out).toContain('2026');
    expect(out.toLowerCase()).not.toContain('pm');
  });

  it('formatTimeIST renders the shifted IST time only', () => {
    const out = formatTimeIST(naive).toLowerCase();
    expect(out).toContain('9:58');
    expect(out).toContain('pm');
  });

  it('a naive timestamp near midnight UTC rolls into the next IST day (+5:30)', () => {
    // 22:00 UTC on May 22 == 03:30 IST on May 23
    const out = formatDateIST('2026-05-22T22:00:00');
    expect(out).toContain('23');
    expect(out).toContain('May');
  });

  it('returns the fallback for unparseable values and honors a custom fallback', () => {
    expect(formatDateTimeIST(null)).toBe('—');
    expect(formatDateIST(undefined)).toBe('—');
    expect(formatTimeIST('garbage', 'N/A')).toBe('N/A');
  });
});
