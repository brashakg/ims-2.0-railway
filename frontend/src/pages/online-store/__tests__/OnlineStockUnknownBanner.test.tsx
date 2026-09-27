// ============================================================================
// IMS 2.0 - the stock tally says "on-hand unknown" ONCE, and only when it is
// ============================================================================
// Recheck round 3: a scripted edit applied twice left the amber "IMS could not
// read the shops' on-hand" banner in OnlineStockPage.tsx as two identical JSX
// blocks, so it rendered stacked twice -- and nothing in the frontend pinned
// the banner at all (delete both copies and every suite stayed green).
//
// BOTH DIRECTIONS: present exactly once when summary.on_hand_unknown is true,
// absent when it is not. Paste the block back -> 2 -> fails; delete it -> 0 ->
// fails.

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

function tally(onHandUnknown: boolean) {
  return {
    items: [],
    summary: {
      total_skus: 0,
      at_risk: 0,
      online_configured: true,
      listed_qty_live: true,
      on_hand_unknown: onHandUnknown,
    },
    available: true,
    reason: null,
  };
}

describe('the on-hand UNKNOWN banner on the stock tally', () => {
  beforeEach(() => vi.clearAllMocks());

  it('is said exactly once when the shops could not be read', async () => {
    (onlineStoreApi.getStockTally as any).mockResolvedValue(tally(true));
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStoreApi.getStockTally).toHaveBeenCalled());
    const banners = await screen.findAllByText(/could not read the shops' on-hand right now/i);
    expect(banners).toHaveLength(1);
    expect(banners[0]).toHaveTextContent(/never shown as 0 on hand/);
  });

  it('is absent when the on-hand read worked', async () => {
    (onlineStoreApi.getStockTally as any).mockResolvedValue(tally(false));
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStoreApi.getStockTally).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(/Loading/i)).toBeNull());
    expect(screen.queryByText(/could not read the shops' on-hand right now/i)).toBeNull();
  });
});
