import { describe, expect, it } from 'vitest';
import { AxiosError } from 'axios';
import { ApiError, buildApiError } from '../../services/api/client';
import { forbiddenDetail, getErrorDetail, getErrorMessage, getErrorStatus, isForbiddenError } from '../errorHandler';

const delivered = (status: number | undefined, detail?: unknown) => buildApiError({
  message: 'Request failed',
  response: status === undefined ? undefined : { status, data: { detail } },
} as Parameters<typeof buildApiError>[0]);

const MIDDLEWARE_403 = 'Forbidden: POST /api/v1/finance/journal-entries/JE1/approve requires one of ADMIN, SUPERADMIN';

describe('screen messages use the real client error contract', () => {
  it('preserves the clinical rejection through the actual client transform', () => {
    const text = 'Right eye CYL value -50 is outside the valid range (-6 to 6)';
    expect(getErrorMessage(delivered(400, text), 'Could not save prescription')).toBe(text);
  });

  it('reads the detail, not the envelope message, when the two differ', () => {
    // Only the detail path can produce these answers: the message says something else.
    expect(getErrorMessage(new ApiError('Request failed', { status: 400, detail: 'Pick a store first' }), 'fb')).toBe('Pick a store first');
    expect(getErrorMessage(new ApiError('Request failed', { status: 409, detail: { code: 'DUP', message: 'This product already exists' } }), 'fb')).toBe('This product already exists');
  });

  it('names the invalid field for pydantic validation', () => {
    const error = delivered(422, [{ loc: ['body', 'right_eye', 'axis'], msg: 'Must be between 1 and 180' }]);
    expect(error.message).toBe('Must be between 1 and 180');
    expect(getErrorMessage(error, 'Save failed')).toBe('axis: Must be between 1 and 180');
  });

  it('recognizes canonical forbidden errors and keeps developer text hidden', () => {
    const error = delivered(403, 'Ask an administrator to change this setting.');
    expect(isForbiddenError(error)).toBe(true);
    expect(getErrorStatus(error)).toBe(403);
    expect(forbiddenDetail(error, 'Access denied')).toBe('Ask an administrator to change this setting.');
    expect(forbiddenDetail(delivered(403, 'Forbidden: GET /api/v1/payroll/run requires ADMIN'), 'Access denied')).toBe('Access denied');
  });

  it('never shows the RBAC middleware text, on any reader', () => {
    expect(getErrorMessage(delivered(403, MIDDLEWARE_403), 'Approve failed')).toBe('Approve failed');
    expect(getErrorMessage(new ApiError(MIDDLEWARE_403, { status: 403 }), 'Approve failed')).toBe('Approve failed');
    expect(getErrorMessage({ response: { status: 403, data: { detail: MIDDLEWARE_403 } } }, 'Approve failed')).toBe('Approve failed');
    expect(forbiddenDetail(delivered(403, MIDDLEWARE_403), 'Approve failed')).toBe('Approve failed');
  });

  it('preserves safe-stop and network guidance without revealing a 500 payload', () => {
    expect(getErrorMessage(delivered(503, { message: 'Nothing was created. Try again.' }), 'Save failed')).toBe('Nothing was created. Try again.');
    expect(getErrorMessage(delivered(500, { message: 'internal traceback' }), 'Save failed')).toBe('Server error. Please try again in a moment.');
    expect(getErrorMessage(delivered(undefined), 'Save failed')).toMatch(/Network error/);
  });

  it('applies the 5xx barrier to raw axios adapters too', () => {
    const raw500 = { response: { status: 500, data: { detail: 'KeyError: store_id' } } };
    expect(getErrorDetail(raw500)).toBeUndefined();
    expect(getErrorMessage(raw500, 'Save failed')).toBe('Save failed');
    expect(getErrorMessage({ response: { status: 503, data: { detail: 'Nothing was created.' } } }, 'fb')).toBe('Nothing was created.');
  });

  it("keeps the caller's fallback for local faults and raw transport text", () => {
    expect(getErrorMessage(new TypeError("Cannot read properties of undefined (reading 'x')"), 'Failed to record QC')).toBe('Failed to record QC');
    let parseError: unknown;
    try { JSON.parse('{x'); } catch (e) { parseError = e; }
    expect(getErrorMessage(parseError, 'Invalid JSON')).toBe('Invalid JSON');
    expect(getErrorMessage(new AxiosError('Network Error', 'ERR_NETWORK'), 'Failed to save. Try again.')).toBe('Failed to save. Try again.');
    expect(getErrorMessage(new DOMException('The operation either timed out or was not allowed.', 'NotAllowedError'), 'Cancelled')).toBe('Cancelled');
  });

  it('shows a sentence our own code threw on purpose', () => {
    // e.g. printDocuments rethrows the server's refusal as a plain Error.
    expect(getErrorMessage(new Error('Ship the transfer first.'), 'Action failed')).toBe('Ship the transfer first.');
  });

  it('supports raw axios adapters and absent or malformed data', () => {
    expect(getErrorMessage({ response: { data: { detail: { message: 'Use another name' } } } }, 'fallback')).toBe('Use another name');
    for (const value of [null, undefined, {}, { detail: {} }, { detail: [null] }, { detail: ' ' }]) {
      expect(getErrorMessage(value, 'fallback')).toBe('fallback');
    }
  });
});
