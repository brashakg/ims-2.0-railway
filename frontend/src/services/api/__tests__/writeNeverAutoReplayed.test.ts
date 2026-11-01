// PR #1264 money review 11: the shared client never auto-replays a WRITE that
// failed with a 5xx -- it may already have landed (an "unclear" store credit
// answers 503 "do NOT redo"). Reads still retry; a 429 (refused before any
// work) retries for any method.
import { describe, it, expect, vi } from 'vitest';
import { AxiosError } from 'axios';
import api, { buildApiError, isRetryableError, WRITE_UNCONFIRMED_MESSAGE } from '../client';

const err = (status: number | undefined, method: string) =>
  ({ config: { method }, response: status === undefined ? undefined : { status } } as unknown as AxiosError);

describe('automatic retry', () => {
  it('replays a read on a 5xx but never a write', () => {
    for (const m of ['get', 'GET', 'head', 'options']) expect(isRetryableError(err(503, m))).toBe(true);
    for (const m of ['post', 'put', 'patch', 'delete', 'POST']) expect(isRetryableError(err(503, m))).toBe(false);
    expect(isRetryableError(err(500, 'post'))).toBe(false);
    expect(isRetryableError(err(429, 'post'))).toBe(true);
    expect(isRetryableError(err(undefined, 'get'))).toBe(false);
  });

  it('hands an unclear write straight back with the server sentence, never replayed', async () => {
    const sentence = 'The store credit could not be confirmed just now - Do NOT redo this return.';
    const replay = vi.spyOn(api, 'request').mockResolvedValue({ data: 'replayed' } as any);
    const handlers = (api.interceptors.response as any).handlers.filter(Boolean);
    const onError = handlers[handlers.length - 1].rejected as (e: unknown) => Promise<unknown>;
    const config: any = { method: 'post', url: '/returns', headers: {} };
    const response: any = { status: 503, statusText: '', data: { detail: sentence }, headers: {}, config };
    await expect(onError(new AxiosError('Request failed with status code 503', 'ERR_BAD_RESPONSE', config, null, response)))
      .rejects.toMatchObject({ status: 503, message: sentence });
    expect(replay).not.toHaveBeenCalled();
    replay.mockRestore();
  });
});

// Money review 12: a write whose answer was lost is never told "try again".
describe('a lost write answer', () => {
  const lost = (status: number | undefined, method: string, data: unknown = {}) =>
    ({ message: 'x', config: { method },
       response: status === undefined ? undefined : { status, data } } as unknown as AxiosError<any>);

  it('says it may already be saved for a write, keeps the old text for a read', () => {
    expect(buildApiError(lost(500, 'post')).message).toBe(WRITE_UNCONFIRMED_MESSAGE);
    expect(buildApiError(lost(502, 'patch')).message).toBe(WRITE_UNCONFIRMED_MESSAGE);
    expect(buildApiError(lost(undefined, 'put')).message).toBe(WRITE_UNCONFIRMED_MESSAGE);
    expect(buildApiError(lost(503, 'post', { detail: 'Nothing was recorded. Try again.' })).message)
      .toBe('Nothing was recorded. Try again.');
    expect(buildApiError(lost(500, 'get')).message).toBe('Server error. Please try again in a moment.');
    expect(buildApiError(lost(undefined, 'get')).message).toMatch(/Network error/);
  });
});
