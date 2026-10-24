import { describe, expect, it } from 'vitest';
import { buildApiError } from '../../services/api/client';
import { apiDetailMessage, forbiddenDetail, getErrorMessage, getErrorStatus, isForbiddenError } from '../errorHandler';

const delivered = (status: number | undefined, detail?: unknown) => buildApiError({
  message: 'Request failed',
  response: status === undefined ? undefined : { status, data: { detail } },
} as Parameters<typeof buildApiError>[0]);

describe('screen messages use the real client error contract', () => {
  it('preserves the clinical rejection through the actual client transform', () => {
    const error = delivered(400, 'Right eye CYL value -50 is outside the valid range (-6 to 6)');
    expect(getErrorMessage(error, 'Could not save prescription')).toBe(error.message);
    expect(apiDetailMessage(error, 'Could not save prescription')).toBe(error.message);
  });

  it('names the invalid field for pydantic validation', () => {
    const error = delivered(422, [{ loc: ['body', 'right_eye', 'axis'], msg: 'Must be between 1 and 180' }]);
    expect(getErrorMessage(error, 'Save failed')).toBe('axis: Must be between 1 and 180');
  });

  it('renders structured business rejections as text', () => {
    const error = delivered(409, { code: 'DUPLICATE_PRODUCT', message: 'This product already exists', existing: { sku: 'FR-1' } });
    expect(getErrorMessage(error, 'Save failed')).toBe('This product already exists');
  });

  it('recognizes canonical forbidden errors and keeps developer text hidden', () => {
    const error = delivered(403, 'Ask an administrator to change this setting.');
    expect(isForbiddenError(error)).toBe(true);
    expect(getErrorStatus(error)).toBe(403);
    expect(forbiddenDetail(error, 'Access denied')).toBe(error.message);
    expect(forbiddenDetail(delivered(403, 'Forbidden: GET /api/v1/payroll/run requires ADMIN'), 'Access denied')).toBe('Access denied');
  });

  it('preserves safe-stop and network guidance without revealing a 500 payload', () => {
    expect(getErrorMessage(delivered(503, { message: 'Nothing was created. Try again.' }), 'Save failed')).toBe('Nothing was created. Try again.');
    expect(getErrorMessage(delivered(500, { message: 'internal traceback' }), 'Save failed')).toBe('Server error. Please try again in a moment.');
    expect(getErrorMessage(delivered(undefined), 'Save failed')).toMatch(/Network error/);
  });

  it('supports raw axios adapters, ordinary Error, and absent or malformed data', () => {
    expect(getErrorMessage({ response: { data: { detail: { message: 'Use another name' } } } }, 'fallback')).toBe('Use another name');
    expect(getErrorMessage(new Error('Disconnected'), 'fallback')).toBe('Disconnected');
    for (const value of [null, undefined, {}, { detail: {} }, { detail: [null] }, { detail: ' ' }]) {
      expect(getErrorMessage(value, 'fallback')).toBe('fallback');
    }
  });
});
