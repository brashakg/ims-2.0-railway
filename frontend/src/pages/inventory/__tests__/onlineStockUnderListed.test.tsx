// ============================================================================
// IMS 2.0 - "Show only at-risk" keeps the rows a drift task names
// ============================================================================
// Multi-location PR 4, round 18 review item 3: nightly parity files drift both
// ways, so an UNDER-listed SKU (IMS sends 10, Shopify lists 2) gets a task
// that sends the manager to this page -- but the row is status OK / delta 0
// and "Show only at-risk" (ticked by default) hid it under "No overselling
// risk". A row whose live number is not the one IMS sends now stays visible.
// Drop `listedOffRecommended` from the filter -> fails.

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

const row = (sku: string, online: number | null, recommended: number | null) => ({
  sku, name: 'Name of ' + sku, in_store: 10, online, recommended, delta: 0, status: 'OK',
});

describe('Show only at-risk', () => {
  beforeEach(() => vi.clearAllMocks());

  it('keeps an under-listed row, and hides a row that matches what IMS sends', async () => {
    (onlineStockApi.reconcile as any).mockResolvedValue({
      items: [row('UNDER-1', 2, 10), row('SAME-1', 5, 5), row('UNKNOWN-1', null, 5)],
      summary: { safety_buffer: 0, ok: 3 },
      online_configured: true, listed_qty_live: true, listed_live_rows: 3, listed_mapped_rows: 3,
      live_listings_unknown: false,
    });
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStockApi.reconcile).toHaveBeenCalled());
    expect(await screen.findByText('UNDER-1')).toBeInTheDocument();
    expect(screen.queryByText('SAME-1')).toBeNull();
    expect(screen.queryByText('UNKNOWN-1')).toBeNull();
  });
});
