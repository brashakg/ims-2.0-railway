// ============================================================================
// IMS 2.0 - 403 handling for the payroll access notice
// ============================================================================
// Owner ruling 2026-08-09 closed the payroll screens to ACCOUNTANT /
// AREA_MANAGER / STORE_MANAGER. These two helpers decide what those users SEE
// when that happens, so they are the difference between an honest
// "you do not have access" panel and either (a) an empty table that reads as
// "nobody was paid this month" or (b) raw developer text with an HTTP method
// and a URL in it.

import { describe, it, expect } from 'vitest';
import { isForbiddenError, forbiddenDetail } from '../errorHandler';

const axiosErr = (status: number, detail?: unknown) => ({
  response: { status, data: detail === undefined ? {} : { detail } },
});

describe('isForbiddenError', () => {
  it('is true only for HTTP 403', () => {
    expect(isForbiddenError(axiosErr(403))).toBe(true);
    expect(isForbiddenError(axiosErr(404))).toBe(false);
    expect(isForbiddenError(axiosErr(500))).toBe(false);
    expect(isForbiddenError(axiosErr(200))).toBe(false);
  });

  it('does not throw on shapes that are not axios errors', () => {
    expect(isForbiddenError(undefined)).toBe(false);
    expect(isForbiddenError(null)).toBe(false);
    expect(isForbiddenError(new Error('network'))).toBe(false);
    expect(isForbiddenError({})).toBe(false);
  });
});

describe('forbiddenDetail', () => {
  it("shows the router's plain-English detail verbatim", () => {
    const err = axiosErr(
      403,
      'Payroll and salary data is restricted to administrators. Please ask an administrator.',
    );
    expect(forbiddenDetail(err, 'fallback')).toBe(
      'Payroll and salary data is restricted to administrators. Please ask an administrator.',
    );
  });

  it('suppresses the RBAC middleware text, which names a method and a URL', () => {
    const err = axiosErr(403, 'Forbidden: GET /api/v1/payroll/run/rows requires one of ADMIN');
    expect(forbiddenDetail(err, 'The payroll register is restricted to administrators.')).toBe(
      'The payroll register is restricted to administrators.',
    );
  });

  it('falls back when the body carries no usable detail', () => {
    expect(forbiddenDetail(axiosErr(403), 'fallback')).toBe('fallback');
    expect(forbiddenDetail(axiosErr(403, '   '), 'fallback')).toBe('fallback');
    expect(forbiddenDetail(axiosErr(403, { code: 'nope' }), 'fallback')).toBe('fallback');
    expect(forbiddenDetail(undefined, 'fallback')).toBe('fallback');
  });
});
