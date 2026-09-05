// ============================================================================
// IMS 2.0 - the error shape the api client ACTUALLY delivers (P0-3 launch gate)
// ============================================================================
// Every rejected API call reaches components through buildApiError — the real
// production transform behind the axios interceptor's handleFinalError. These
// tests pin the delivered shape so no component (or test fixture) can drift
// back to reading the `.response` axios property the client strips: that dead
// read is how the EXPRESS_PARTIAL recovery banner silently never rendered and
// staff were told "try again" — the retry that double-mints stock.

import { describe, it, expect } from 'vitest';
import type { AxiosError } from 'axios';
import { ApiError, buildApiError, PUSH_TIMEOUT_MESSAGE } from '../client';

type Data = { message?: string; detail?: string | Array<Record<string, unknown>> };

const axiosErr = (status: number | undefined, data: unknown, message = `Request failed with status code ${status}`) =>
  ({
    message,
    response: status === undefined ? undefined : { status, data },
  }) as unknown as AxiosError<Data>;

describe('buildApiError — the one delivered error shape', () => {
  it('a structured 409 keeps message, code, status and the raw detail — and has NO .response', () => {
    const detail = {
      code: 'EXPRESS_PARTIAL',
      grn_id: 'g1',
      grn_number: 'GRN-9',
      message: 'Receipt GRN-9 was created but not accepted',
    };
    const e = buildApiError(axiosErr(409, { detail }));
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toBeInstanceOf(Error);
    expect(e.message).toBe('Receipt GRN-9 was created but not accepted');
    expect(e.status).toBe(409);
    expect(e.code).toBe('EXPRESS_PARTIAL');
    expect(e.detail).toEqual(detail);
    // The strip is real: components must never key off err.response.
    expect((e as unknown as { response?: unknown }).response).toBeUndefined();
  });

  it('a string detail survives as both message and detail', () => {
    const e = buildApiError(axiosErr(409, { detail: 'PO is not in receivable status' }));
    expect(e.message).toBe('PO is not in receivable status');
    expect(e.detail).toBe('PO is not in receivable status');
    expect(e.code).toBeUndefined();
    expect(e.status).toBe(409);
  });

  it('a pydantic array detail joins the msgs', () => {
    const e = buildApiError(
      axiosErr(422, { detail: [{ msg: 'field a bad' }, { msg: 'field b bad' }] }),
    );
    expect(e.message).toBe('field a bad. field b bad');
  });

  it('a plain 5xx stays generic and carries NO structured payload (leak barrier)', () => {
    const e = buildApiError(
      axiosErr(500, { detail: { code: 'BOOM', message: 'stack trace here' } }),
    );
    expect(e.message).toBe('Server error. Please try again in a moment.');
    expect(e.code).toBeUndefined();
    expect(e.detail).toBeUndefined();
    expect(e.status).toBe(500);
  });

  it('a 503 safe-stop STRING detail survives into the message (by design)', () => {
    const e = buildApiError(
      axiosErr(503, { detail: '4 units were received before we stopped' }),
    );
    expect(e.message).toBe('4 units were received before we stopped');
    expect(e.status).toBe(503);
  });

  it('a network error (no response) keeps the connection message and no status', () => {
    const e = buildApiError(axiosErr(undefined, undefined, 'Network Error'));
    expect(e.message).toBe(
      'Network error. Please check your internet connection and try again.',
    );
    expect(e.status).toBeUndefined();
    expect(e.detail).toBeUndefined();
  });
});

// Prod 2026-09-05: the sweep POST outlived the client's 10 s timeout, the server
// finished all 10 products, and the screen said "check your internet
// connection and try again" -- the sentence that invites a second press.
describe('buildApiError -- a timed-out push says the truth', () => {
  const timeout = (url: string, code: string | undefined = 'ECONNABORTED') =>
    ({
      message: 'timeout of 180000ms exceeded',
      code,
      config: { url },
      response: undefined,
    }) as unknown as AxiosError<Data>;

  it('a push sweep timeout says the push is still running, not "check your internet"', () => {
    const e = buildApiError(timeout('/online-store/push/all-pending?entities=products&limit=100'));
    expect(e.message).toBe(PUSH_TIMEOUT_MESSAGE);
    expect(e.message).toMatch(/still running on the server/);
    expect(e.message).toMatch(/do not press again/);
    expect(e.message).not.toMatch(/internet connection/);
    expect(e.status).toBeUndefined();
  });

  it('a single-entity push timeout gets the same honest message', () => {
    expect(buildApiError(timeout('/online-store/push/product/P1')).message).toBe(PUSH_TIMEOUT_MESSAGE);
  });

  it('a timeout on any OTHER endpoint keeps the generic connection message', () => {
    expect(buildApiError(timeout('/customers?search=riya')).message).toBe(
      'Network error. Please check your internet connection and try again.',
    );
  });

  it('a real network failure (ERR_NETWORK) on a push URL is NOT called "still running"', () => {
    expect(buildApiError(timeout('/online-store/push/all-pending', 'ERR_NETWORK')).message).toBe(
      'Network error. Please check your internet connection and try again.',
    );
  });
});
