// ============================================================================
// IMS 2.0 - the stock tally says "could not read which products are live"
// when the live-listing read failed, and never "No SKUs are listed online"
// ============================================================================
// Multi-location PR 4, round 17: the backend now reads THE one "is this
// listing live on Shopify" reader strictly; a dead read comes back as
// summary.live_listings_unknown with no rows. The page must say so ONCE,
// and the empty state must not tell the owner nothing is listed online.
// Delete the banner -> 0 -> fails; drop the empty-state branch -> "No SKUs
// are listed online yet" beside it -> fails.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/onlineStore', () => ({
  onlineStoreApi: { getStockTally: vi.fn() },
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

import OnlineStockPage from '../OnlineStockPage';
import { onlineStoreApi } from '../../../services/api/onlineStore';

function tally(liveUnknown: boolean) {
  return {
    items: [],
    summary: {
      total_skus: 0,
      at_risk: 0,
      online_configured: true,
      listed_qty_live: !liveUnknown,
      live_listings_unknown: liveUnknown,
    },
    available: true,
    reason: null,
  };
}

describe('the live-listings UNKNOWN banner on the stock tally', () => {
  beforeEach(() => vi.clearAllMocks());

  it('is said exactly once, and the empty state never says nothing is listed', async () => {
    (onlineStoreApi.getStockTally as any).mockResolvedValue(tally(true));
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStoreApi.getStockTally).toHaveBeenCalled());
    const banners = await screen.findAllByText(/could not read which products are live/i);
    expect(banners).toHaveLength(1);
    expect(screen.queryByText(/No SKUs are listed online yet/i)).toBeNull();
  });

  it('is absent when the read worked, and an empty tally says nothing is listed', async () => {
    (onlineStoreApi.getStockTally as any).mockResolvedValue(tally(false));
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStoreApi.getStockTally).toHaveBeenCalled());
    await screen.findByText(/No SKUs are listed online yet/i);
    expect(screen.queryByText(/could not read which products are live/i)).toBeNull();
  });
});
