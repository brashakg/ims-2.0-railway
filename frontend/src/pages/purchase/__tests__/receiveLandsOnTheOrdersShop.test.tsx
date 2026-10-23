// ============================================================================
// IMS 2.0 - Purchase Orders 'Receive' lands on a receive screen that holds the order
// (review r1 #27, audit F63)
// ============================================================================
// An admin works Purchase on ALL STORES (or a shop he picked) while his own
// active shop is another one. The Purchase Orders list showed PO-PUN-0001
// (delivers to WizOpt Pune); pressing its 'Receive' button opened the cockpit
// for his OWN shop (?store_id=BV-DHN-01), where the server -- which filters
// open POs by delivery shop -- had none: 'No open POs for this vendor at this
// store.' for an order that is open.
//
// The receiving screen reads the one Purchase shop scope: the admin's pick,
// every shop when he picks none. Everyone else keeps their own shop, as
// before. Drives the REAL PurchaseTable button into the REAL receive screen.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useEffect, useState, type ReactNode } from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock('../../../services/api/grnCockpit', () => ({
  grnCockpitApi: { uploadDoc: vi.fn(), expressReceive: vi.fn() },
}));

vi.mock('../../../services/api/products', () => ({
  productApi: { getProduct: vi.fn(async (id: string) => ({ product_id: id })), getProducts: vi.fn() },
}));

vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getGRNs: vi.fn(),
    getPurchaseOrders: vi.fn(),
    getVendors: vi.fn(),
    acceptGRN: vi.fn(),
    voidGRN: vi.fn(),
  },
}));

vi.mock('../../../services/api/labels', () => ({
  default: { getProductLabel: vi.fn() },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => toastMock,
}));

let roles: string[] = ['ADMIN'];
let activeStoreId = 'BV-DHN-01';
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', roles, activeStoreId },
    hasRole: (want: string[]) => want.some((r) => roles.includes(r)),
  }),
}));

vi.mock('../../../hooks/useIsOnlineStore', () => ({
  useIsOnlineStore: () => false,
}));

vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({
    data: [
      { store_id: 'BV-DHN-01', store_name: 'Better Vision Dhanbad' },
      { store_id: 'WO-PUN-01', store_name: 'WizOpt Pune' },
    ],
  }),
}));

import { ReceiveGoods } from '../ReceiveGoods';
import { PurchaseTable } from '../PurchaseTable';
import { usePurchaseShop } from '../purchaseShop';
import { vendorsApi } from '../../../services/api/inventory';
import type { PurchaseOrder } from '../purchaseTypes';

const getGRNs = vendorsApi.getGRNs as unknown as ReturnType<typeof vi.fn>;
const getPOs = vendorsApi.getPurchaseOrders as unknown as ReturnType<typeof vi.fn>;

// The Pune order as the Purchase Orders list holds it.
const PUNE_PO: PurchaseOrder = {
  id: 'PO2',
  poNumber: 'PO-PUN-0001',
  supplierId: 'V2',
  supplierName: 'Pune Frames',
  date: '2026-09-20',
  expectedDelivery: '2026-10-05',
  status: 'SENT',
  items: [
    {
      productId: 'P1',
      productName: 'Frame A',
      sku: 'FA-1',
      quantity: 2,
      unitCost: 100,
      taxRate: 5,
      total: 210,
      receivedQty: 0,
    },
  ],
  subtotal: 200,
  taxAmount: 10,
  total: 210,
};

const PUNE_ROW = {
  po_id: 'PO2',
  po_number: 'PO-PUN-0001',
  vendor_id: 'V2',
  vendor_name: 'Pune Frames',
  status: 'SENT',
  delivery_store_id: 'WO-PUN-01',
  items: [{ product_id: 'P1', product_name: 'Frame A', ordered_qty: 2, received_qty: 0 }],
};

/** The server's list rule: orders filtered by delivery shop; no store_id (an
 *  admin) = every shop. Only the Pune order exists. */
function ordersFor(params?: { store_id?: string }) {
  const holdsPune = !params?.store_id || params.store_id === 'WO-PUN-01';
  return Promise.resolve({ purchase_orders: holdsPune ? [PUNE_ROW] : [] });
}

/** Sets the shared Purchase shop pick before the page mounts. */
function Pick({ shop, children }: { shop: string; children: ReactNode }) {
  const { setShop } = usePurchaseShop();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setShop(shop);
    setReady(true);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return ready ? <>{children}</> : null;
}

function openOrdersWithPick(shop: string) {
  render(
    <MemoryRouter initialEntries={['/purchase/orders']}>
      <Pick shop={shop}>
        <Routes>
          <Route
            path="/purchase/orders"
            element={<PurchaseTable purchaseOrders={[PUNE_PO]} onViewPO={() => {}} />}
          />
          <Route path="/purchase/receive" element={<ReceiveGoods />} />
        </Routes>
      </Pick>
    </MemoryRouter>,
  );
}

async function pressReceive() {
  fireEvent.click(screen.getByRole('button', { name: /^receive$/i }));
  await waitFor(() => expect(getPOs).toHaveBeenCalled());
}

const orderIsOpen = () => screen.findByRole('heading', { name: /Receive PO-PUN-0001/ });

beforeEach(() => {
  vi.clearAllMocks();
  roles = ['ADMIN'];
  activeStoreId = 'BV-DHN-01';
  getGRNs.mockResolvedValue({ grns: [] });
  getPOs.mockImplementation(ordersFor);
});

describe('Receive from Purchase Orders lands on a receive screen holding the order', () => {
  it('admin on All stores: the screen reads every shop and opens the order', async () => {
    openOrdersWithPick('');
    await pressReceive();
    expect(getPOs).toHaveBeenCalledWith({ receipt_candidates: true });
    expect(getPOs).not.toHaveBeenCalledWith(expect.objectContaining({ store_id: 'BV-DHN-01' }));
    expect(await orderIsOpen()).toBeInTheDocument();
  });

  it('admin who picked Pune: the screen reads Pune and opens the order', async () => {
    openOrdersWithPick('WO-PUN-01');
    await pressReceive();
    expect(getPOs).toHaveBeenCalledWith({ store_id: 'WO-PUN-01', receipt_candidates: true });
    expect(await orderIsOpen()).toBeInTheDocument();
    // The picker on the screen says which shop it is reading.
    expect((screen.getByLabelText('Purchase shop') as HTMLSelectElement).value).toBe('WO-PUN-01');
  });

  it('a store manager keeps his own shop whatever the pick says', async () => {
    roles = ['STORE_MANAGER'];
    activeStoreId = 'WO-PUN-01';
    openOrdersWithPick('BV-DHN-01');
    await pressReceive();
    expect(getPOs).toHaveBeenCalledWith({ store_id: 'WO-PUN-01', receipt_candidates: true });
    expect(await orderIsOpen()).toBeInTheDocument();
    expect(screen.queryByLabelText('Purchase shop')).toBeNull();
  });

  it('admin narrowing the shop to one the order does not deliver to drops the open order', async () => {
    openOrdersWithPick('');
    await pressReceive();
    await orderIsOpen();
    fireEvent.change(screen.getByLabelText('Purchase shop'), { target: { value: 'BV-DHN-01' } });
    await waitFor(() => expect(getPOs).toHaveBeenLastCalledWith({ store_id: 'BV-DHN-01', receipt_candidates: true }));
    expect(screen.queryByRole('heading', { name: /Receive PO-PUN-0001/ })).toBeNull();
    expect(await screen.findByText(/No open purchase orders to receive against at the shop picked above/)).toBeInTheDocument();
  });
});

describe('Deliveries inbox follows the same scope', () => {
  function openInbox(shop: string) {
    render(
      <MemoryRouter initialEntries={['/purchase/receive']}>
        <Pick shop={shop}>
          <ReceiveGoods />
        </Pick>
      </MemoryRouter>,
    );
  }

  it('admin on All stores: every shop, and each box says which shop it is for', async () => {
    openInbox('');
    expect(await screen.findByText('PO-PUN-0001')).toBeInTheDocument();
    expect(getPOs).toHaveBeenCalledWith({ receipt_candidates: true });
    expect(screen.getByText(/^For/).textContent).toBe('For WizOpt Pune');
  });

  it('admin on one shop: that shop, no per-box shop line', async () => {
    openInbox('WO-PUN-01');
    expect(await screen.findByText('PO-PUN-0001')).toBeInTheDocument();
    expect(getPOs).toHaveBeenCalledWith({ store_id: 'WO-PUN-01', receipt_candidates: true });
    expect(screen.queryByText(/^For/)).toBeNull();
  });

  it('store manager: his own shop, as before', async () => {
    roles = ['STORE_MANAGER'];
    activeStoreId = 'WO-PUN-01';
    openInbox('');
    expect(await screen.findByText('PO-PUN-0001')).toBeInTheDocument();
    expect(getPOs).toHaveBeenCalledWith({ store_id: 'WO-PUN-01', receipt_candidates: true });
    expect(getPOs).not.toHaveBeenCalledWith({ receipt_candidates: true });
  });

  it('admin on All stores with nothing open: not "at this store"', async () => {
    getPOs.mockResolvedValue({ purchase_orders: [] });
    openInbox('');
    expect(await screen.findByText(/No open purchase orders to receive against in any store\./)).toBeInTheDocument();
  });
});
