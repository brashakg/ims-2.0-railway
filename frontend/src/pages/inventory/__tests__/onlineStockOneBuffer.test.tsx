// ============================================================================
// IMS 2.0 - the reconciliation screen has ONE safety buffer: the writer's
// ============================================================================
// Multi-location PR 4 panel: "Recommended" used to be the pooled on-hand minus
// a buffer typed on this page -- a second website quantity the writer never
// sends. The backend now answers the writer's own number per location and
// reports the writer's buffer; the page asks with no buffer of its own and
// shows the one it was given.
//
// Put the "Safety buffer" input back (or send safety_buffer again) -> fails.

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

describe('the online vs in-store reconciliation screen', () => {
  beforeEach(() => vi.clearAllMocks());

  it('asks with no buffer of its own and shows the writer buffer', async () => {
    (onlineStockApi.reconcile as any).mockResolvedValue({
      items: [],
      summary: { safety_buffer: 2 },
      online_configured: true,
      listed_qty_live: true,
    });
    render(<OnlineStockPage />);
    await waitFor(() => expect(onlineStockApi.reconcile).toHaveBeenCalled());
    expect((onlineStockApi.reconcile as any).mock.calls[0][0]).toEqual({ store_id: undefined });
    expect(screen.queryByLabelText(/safety buffer/i)).toBeNull();
    expect(await screen.findByText(/minus the safety buffer \(2\)/)).toBeInTheDocument();
  });
});
