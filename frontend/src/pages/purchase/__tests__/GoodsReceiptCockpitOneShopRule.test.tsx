// ============================================================================
// IMS 2.0 - Receive goods reads the ONE Purchase shop rule (audit F63)
// ============================================================================
// The cockpit used to re-derive its shop inline ('canPick ? pick : own shop')
// beside usePurchaseShop(), which already answers it. The two agreed, so no
// test noticed -- and a change to the one hook (R3's fail-closed rule, say)
// would never have reached Receive goods. Here the hook answers a shop the
// login's own token does not name: the cockpit must ask for the hook's.

import { describe, it, expect, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../services/api/grnCockpit', () => ({
  grnCockpitApi: { listVendors: vi.fn(async () => []), getCockpit: vi.fn() },
}));
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getGRNs: vi.fn(async () => ({ grns: [] })),
    getPurchaseOrders: vi.fn(async () => ({ purchase_orders: [] })),
  },
}));
vi.mock('../../../services/api/labels', () => ({ default: { getProductLabel: vi.fn() } }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-sm', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-01' },
    hasRole: (want: string[]) => want.includes('STORE_MANAGER'),
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));
vi.mock('../purchaseShop', async (importOriginal) => {
  const real = await importOriginal<typeof import('../purchaseShop')>();
  return {
    ...real,
    usePurchaseShop: () => ({
      storeId: 'HOOK-SHOP',
      ownStoreId: 'BV-DHN-01',
      canPick: false,
      noShop: false,
      shop: '',
      setShop: vi.fn(),
      showShopOf: vi.fn(),
    }),
  };
});

import { GoodsReceiptCockpit } from '../GoodsReceiptCockpit';
import { vendorsApi } from '../../../services/api/inventory';

describe('Receive goods', () => {
  it('lists the deliveries of the shop usePurchaseShop() answers', async () => {
    render(
      <MemoryRouter initialEntries={['/purchase/receive']}>
        <GoodsReceiptCockpit />
      </MemoryRouter>,
    );
    await waitFor(() => expect(vendorsApi.getPurchaseOrders).toHaveBeenCalled());
    expect(vendorsApi.getPurchaseOrders).toHaveBeenCalledWith({ store_id: 'HOOK-SHOP' });
    expect(vendorsApi.getPurchaseOrders).not.toHaveBeenCalledWith({ store_id: 'BV-DHN-01' });
  });
});
