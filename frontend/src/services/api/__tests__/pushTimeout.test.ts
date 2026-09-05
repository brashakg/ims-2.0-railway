// ============================================================================
// IMS 2.0 - push requests outlive the client's 10 s default timeout
// ============================================================================
// Prod 2026-09-05: the Products "Push" sweep took ~11 s on the server and the
// axios default (10 s, a CORS fail-fast bound) cut the screen off with a false
// "Network error". Every push call must carry its own, sweep-sized timeout;
// nothing else in the client changes its default.

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../client')>();
  return {
    ...orig,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

import api from '../client';
import { pushApi } from '../onlineStore';

const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  mockPost.mockResolvedValue({ data: { result: { ok: true } } });
});

const lastConfig = () => mockPost.mock.calls[0][2];

describe('pushApi request timeouts', () => {
  it('the all-pending sweep gets 180 s (the server pages up to 100 objects)', async () => {
    await pushApi.pushAllPending('products', 100);
    expect(mockPost.mock.calls[0][0]).toBe('/online-store/push/all-pending?entities=products&limit=100');
    expect(lastConfig()).toEqual({ timeout: 180_000 });
  });

  it.each([
    ['pushProduct', () => pushApi.pushProduct('P1'), '/online-store/push/product/P1'],
    ['takeDownProduct', () => pushApi.takeDownProduct('P1'), '/online-store/push/product/P1/take-down'],
    ['pushCollection', () => pushApi.pushCollection('C1'), '/online-store/push/collection/C1'],
    ['pushMenu', () => pushApi.pushMenu('M1'), '/online-store/push/menu/M1'],
    ['pushImage', () => pushApi.pushImage('I1'), '/online-store/push/image/I1'],
  ])('%s gets 60 s (one live Shopify round-trip)', async (_name, call, url) => {
    await call();
    expect(mockPost.mock.calls[0][0]).toBe(url);
    expect(lastConfig()).toEqual({ timeout: 60_000 });
  });
});
