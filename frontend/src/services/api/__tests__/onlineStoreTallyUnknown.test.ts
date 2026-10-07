// ============================================================================
// IMS 2.0 - getStockTally carries the backend's "unknown" flags to the page
// ============================================================================
// Multi-location PR 4, round 17 review round 3: the Stock Tally page's
// "could not read which products are live" note and its hidden zero strip
// read summary.live_listings_unknown, which only reaches the page through
// this normaliser. Drop the mapping -> the note never shows -> fails.

import { vi, beforeEach, describe, it, expect } from 'vitest';

vi.mock('../client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../client';
import { onlineStoreApi } from '../onlineStore';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => vi.clearAllMocks());

describe('onlineStoreApi.getStockTally', () => {
  it('carries live_listings_unknown and on_hand_unknown through', async () => {
    mockGet.mockResolvedValue({
      data: { items: [], summary: { online_configured: true, listed_qty_live: false, live_listings_unknown: true } },
    });
    const out = await onlineStoreApi.getStockTally();
    expect(out.summary.live_listings_unknown).toBe(true);
    expect(out.summary.on_hand_unknown).toBe(false);
  });

  it('reads an absent flag as false', async () => {
    mockGet.mockResolvedValue({ data: { items: [], summary: { online_configured: true, listed_qty_live: true } } });
    const out = await onlineStoreApi.getStockTally();
    expect(out.summary.live_listings_unknown).toBe(false);
  });
});
