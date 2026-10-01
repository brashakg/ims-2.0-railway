// ============================================================================
// IMS 2.0 - the reconciliation screen never reads "No overselling risk" when
// IMS could not read which listings are live
// ============================================================================
// Multi-location PR 4, round 17 review round 2: a dead live-listing read
// marks every row LISTED_UNKNOWN; "Show only at-risk" (ticked by default)
// hides them, and the empty table said "No overselling risk — everything is
// within safe allocation" under the 'could not read' note. It now says
// nothing could be verified. Drop the branch -> fails.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/onlineStock', () => ({
  onlineStockApi: { reconcile: vi.fn() },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn().mockResolvedValue({ stores: [] }) },
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: vi.fn(), success: vi.fn() }),
}));

import OnlineStockPage from '../OnlineStockPage';
import { onlineStockApi } from '../../../services/api/onlineStock';

function page(liveUnknown: boolean, onlineConfigured = true) {
  const status = liveUnknown ? 'LISTED_UNKNOWN' : 'OK';
  return {
    items: [{ sku: 'SKU-1', name: 'A', in_store: 1, online: liveUnknown ? null : 1, recommended: 1, delta: liveUnknown ? null : 0, status }],
    summary: { safety_buffer: 0, listed_unknown: liveUnknown ? 1 : 0, ok: liveUnknown ? 0 : 1 },
    online_configured: onlineConfigured,
    listed_qty_live: !liveUnknown,
    listed_live_rows: 0,
    listed_mapped_rows: 0,
    live_listings_unknown: liveUnknown,
  };
}

describe('the reconciliation screen when the live-listing read failed', () => {
  beforeEach(() => vi.clearAllMocks());

  it('says nothing could be verified, never "No overselling risk"', async () => {
    (onlineStockApi.reconcile as any).mockResolvedValue(page(true));
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStockApi.reconcile).toHaveBeenCalled());
    expect(await screen.findByText(/could not read which products are live/i)).toBeInTheDocument();
    expect(screen.getByText(/could be verified right now/i)).toBeInTheDocument();
    expect(screen.queryByText(/No overselling risk/i)).toBeNull();
  });

  it('shows the note over "not mapped yet" when the whole catalogue read died', async () => {
    (onlineStockApi.reconcile as any).mockResolvedValue(page(true, false));
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStockApi.reconcile).toHaveBeenCalled());
    expect(await screen.findByText(/could not read which products are live/i)).toBeInTheDocument();
    expect(screen.queryByText(/No products are mapped to Shopify yet/i)).toBeNull();
  });

  it('still says "No overselling risk" when the read worked and nothing is at risk', async () => {
    (onlineStockApi.reconcile as any).mockResolvedValue(page(false));
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStockApi.reconcile).toHaveBeenCalled());
    expect(await screen.findByText(/No overselling risk/i)).toBeInTheDocument();
  });
});
