// ============================================================================
// IMS 2.0 - a SKU that shares its Shopify item reads as that, not "Not online"
// ============================================================================
// Multi-location PR 4, round 19: a SKU on a LIVE listing whose Shopify item
// another IMS product also claims is refused by the writer. The page names the
// cause and the fix, and "Show only at-risk" (ticked by default) keeps it.
// Drop the label or the filter leg -> fails.

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

describe('the reconciliation screen for a shared-item SKU', () => {
  beforeEach(() => vi.clearAllMocks());

  it('names the cause and the fix, and keeps the row under "Show only at-risk"', async () => {
    (onlineStockApi.reconcile as any).mockResolvedValue({
      items: [
        { sku: 'SKU-1', name: 'A', in_store: 3, online: 0, recommended: 0, delta: 0, status: 'SHARES_SHOPIFY_ITEM' },
      ],
      summary: { safety_buffer: 0, shares_item: 1 },
      online_configured: true,
      listed_qty_live: true,
      listed_live_rows: 0,
      listed_mapped_rows: 0,
      live_listings_unknown: false,
    });
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStockApi.reconcile).toHaveBeenCalled());
    expect(await screen.findByText('Shares a Shopify item with another product - fix in IMS')).toBeInTheDocument();
    expect(screen.queryByText('Not online')).toBeNull();
  });
});
