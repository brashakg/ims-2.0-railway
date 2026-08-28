// ============================================================================
// IMS 2.0 - Buy Desk bulk draft PO: the line opens at the product's own rate
// ============================================================================
// The FOURTH flat-18 site, and the only one on a screen: this modal opened
// every line at taxRate 18 regardless of the product. Frames, spectacle lenses
// and contact lenses are all 5%, so the preview over-taxed most of this shop's
// catalogue -- and the server never charged it, so the screen and the saved
// order disagreed.

import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01' } }),
}));
vi.mock('../../../services/api', () => ({
  vendorsApi: {
    getVendors: vi.fn().mockResolvedValue({ vendors: [] }),
    createPurchaseOrder: vi.fn(),
  },
}));
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getLastCost: vi.fn().mockResolvedValue({ costs: {} }) },
}));

import BuyDeskDraftPOModal from '../BuyDeskDraftPOModal';
import type { BuyDeskRow } from '../../../services/api/buyDesk';

const ROW = (over: Partial<BuyDeskRow> = {}): BuyDeskRow =>
  ({
    product_id: 'p1',
    sku: 'RB3025-GOLD',
    name: 'Ray-Ban RB3025',
    brand: 'Ray-Ban',
    category: 'FRAME',
    catalog_status: 'ACTIVE',
    readiness: { complete: true, missing: [], blockers: [], purchasable: true },
    ecom_state: 'NOT_LISTED',
    on_hand: 0,
    on_order: 0,
    buy_signal: 2,
    purchasable: true,
    hsn_code: '900311',
    gst_rate: 5,
    ...over,
  }) as BuyDeskRow;

function show(rows: BuyDeskRow[]) {
  return render(
    <BuyDeskDraftPOModal rows={rows} onClose={vi.fn()} onCreated={vi.fn()} />,
  );
}

describe('Buy Desk bulk draft PO', () => {
  it('opens a frame line at 5%, not a flat 18%', async () => {
    show([ROW()]);
    await waitFor(() => expect(screen.getByText(/^5%$/)).toBeTruthy());
    expect(screen.queryByText(/^18%$/)).toBeNull();
    expect(screen.getByText(/HSN 900311/)).toBeTruthy();
  });

  it('opens a sunglass line at 18% off its own HSN', async () => {
    show([ROW({ product_id: 'p2', name: 'Oakley', hsn_code: '900410', gst_rate: 18 })]);
    await waitFor(() => expect(screen.getByText(/^18%$/)).toBeTruthy());
  });

  it('says the rate is not set rather than inventing one for a product with no HSN', async () => {
    show([ROW({ hsn_code: null, gst_rate: null })]);
    await waitFor(() => expect(screen.getByText(/Rate not set/i)).toBeTruthy());
    expect(screen.queryByText(/^18%$/)).toBeNull();
  });
});
