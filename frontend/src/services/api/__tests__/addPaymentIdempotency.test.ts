// ============================================================================
// A payment leg must reach the server with an Idempotency-Key
// ============================================================================
// The server only de-duplicates a payment when the header is PRESENT: with an
// empty key `add_payment` skips its replay guard entirely and writes a second
// row. `orderApi.addPayment` sent no header at all, so a retried leg was
// recorded twice -- the mechanism behind a confirmed double-charge at the
// delivery counter.
//
// This test exists because the screen-level tests CANNOT see this: they mock
// `services/api/sales` wholesale, so removing the header from this module left
// every one of them green. Measured, not assumed. The assertion therefore
// lives here, against the real function and a fake axios.

import { describe, it, expect, vi, beforeEach } from 'vitest';

const post = vi.fn();
vi.mock('../client', () => ({
  default: { post: (...a: unknown[]) => post(...a), get: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import { orderApi } from '../sales';

beforeEach(() => {
  post.mockReset().mockResolvedValue({ data: {} });
});

describe('addPayment carries an idempotency key', () => {
  it('sends the key as the Idempotency-Key header', async () => {
    await orderApi.addPayment('o-1', { method: 'CASH', amount: 3000 } as never, 'leg-abc');

    expect(post).toHaveBeenCalledTimes(1);
    const [url, body, config] = post.mock.calls[0] as [string, unknown, any];
    expect(url).toBe('/orders/o-1/payments');
    expect(body).toMatchObject({ method: 'CASH', amount: 3000 });
    expect(config?.headers?.['Idempotency-Key']).toBe('leg-abc');
  });

  it('omits the header when no key is given, rather than sending an empty one', async () => {
    // An empty-string key is WORSE than none: it looks like a key to a reader
    // and is ignored by the server's replay guard.
    await orderApi.addPayment('o-1', { method: 'CASH', amount: 3000 } as never);
    const config = (post.mock.calls[0] as [string, unknown, any])[2];
    expect(config?.headers).toBeUndefined();
  });
});
