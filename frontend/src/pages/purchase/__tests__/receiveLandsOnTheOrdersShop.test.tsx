// ============================================================================
// IMS 2.0 - Purchase Orders 'Receive' lands on a cockpit that holds the order
// (review r1 #27, audit F63)
// ============================================================================
// An admin works Purchase on ALL STORES (or a shop he picked) while his own
// active shop is another one. The Purchase Orders list showed PO-PUN-0001
// (delivers to WizOpt Pune); pressing its 'Receive' button opened the cockpit
// for his OWN shop (?store_id=BV-DHN-01), where the server -- which filters
// open POs by delivery shop -- had none: 'No open POs for this vendor at this
// store.' for an order that is open.
//
// The cockpit now reads the one Purchase shop scope: the admin's pick, every
// shop when he picks none. Everyone else keeps their own shop, as before.
// Drives the REAL PurchaseTable button into the REAL cockpit.

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
  grnCockpitApi: {
    listVendors: vi.fn(),
    getCockpit: vi.fn(),
    uploadDoc: vi.fn(),
    createGRN: vi.fn(),
    expressReceive: vi.fn(),
  },
}));

vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getGRNs: vi.fn(),
    getPurchaseOrders: vi.fn(),
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

// The express panel has its own tests; here it only proves the PO was found.
vi.mock('../ExpressReceivePanel', () => ({
  ExpressReceivePanel: ({ po }: { po: { po_number: string } }) => (
    <div>express receive {po.po_number}</div>
  ),
}));

import { GoodsReceiptCockpit } from '../GoodsReceiptCockpit';
import { PurchaseTable } from '../PurchaseTable';
import { usePurchaseShop } from '../purchaseShop';
import { grnCockpitApi } from '../../../services/api/grnCockpit';
import { vendorsApi } from '../../../services/api/inventory';
import type { PurchaseOrder } from '../purchaseTypes';

const getCockpit = grnCockpitApi.getCockpit as unknown as ReturnType<typeof vi.fn>;
const listVendors = grnCockpitApi.listVendors as unknown as ReturnType<typeof vi.fn>;
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

const OPEN_PUNE = {
  po_id: 'PO2',
  po_number: 'PO-PUN-0001',
  status: 'SENT',
  expected_date: '2026-10-05',
  lines: [],
};

/** The server's cockpit rule: open POs filtered by delivery shop; no
 *  store_id (an admin) = every shop. Only the Pune order exists. */
function cockpitFor({ store_id }: { store_id?: string }) {
  const holdsPune = !store_id || store_id === 'WO-PUN-01';
  return Promise.resolve({
    vendor: { vendor_id: 'V2' },
    open_pos: holdsPune ? [OPEN_PUNE] : [],
    pending_not_received: [],
    pending_cataloged: [],
  });
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
          <Route path="/purchase/receive" element={<GoodsReceiptCockpit />} />
        </Routes>
      </Pick>
    </MemoryRouter>,
  );
}

async function pressReceive() {
  fireEvent.click(screen.getByRole('button', { name: /^receive$/i }));
  await waitFor(() => expect(getCockpit).toHaveBeenCalled());
}

beforeEach(() => {
  vi.clearAllMocks();
  roles = ['ADMIN'];
  activeStoreId = 'BV-DHN-01';
  getCockpit.mockImplementation(cockpitFor);
  listVendors.mockResolvedValue([{ vendor_id: 'V2', display_name: 'Pune Frames' }]);
  getGRNs.mockResolvedValue({ grns: [] });
  getPOs.mockResolvedValue({
    purchase_orders: [
      {
        po_id: 'PO2',
        po_number: 'PO-PUN-0001',
        vendor_id: 'V2',
        vendor_name: 'Pune Frames',
        status: 'SENT',
        delivery_store_id: 'WO-PUN-01',
        items: [{ product_id: 'P1', ordered_qty: 2, received_qty: 0 }],
      },
    ],
  });
});

describe('Receive from Purchase Orders lands on a cockpit holding the order', () => {
  it('admin on All stores: the cockpit reads every shop and opens the order', async () => {
    openOrdersWithPick('');
    await pressReceive();
    expect(getCockpit).toHaveBeenCalledWith({ vendor_id: 'V2', store_id: undefined });
    expect(getCockpit).not.toHaveBeenCalledWith(
      expect.objectContaining({ store_id: 'BV-DHN-01' }),
    );
    expect(await screen.findByText('express receive PO-PUN-0001')).toBeInTheDocument();
  });

  it('admin who picked Pune: the cockpit reads Pune and opens the order', async () => {
    openOrdersWithPick('WO-PUN-01');
    await pressReceive();
    expect(getCockpit).toHaveBeenCalledWith({ vendor_id: 'V2', store_id: 'WO-PUN-01' });
    expect(await screen.findByText('express receive PO-PUN-0001')).toBeInTheDocument();
    // The picker on the cockpit says which shop it is reading.
    expect((screen.getByLabelText('Purchase shop') as HTMLSelectElement).value).toBe('WO-PUN-01');
  });

  it('a store manager keeps his own shop whatever the pick says', async () => {
    roles = ['STORE_MANAGER'];
    activeStoreId = 'WO-PUN-01';
    openOrdersWithPick('BV-DHN-01');
    await pressReceive();
    expect(getCockpit).toHaveBeenCalledWith({ vendor_id: 'V2', store_id: 'WO-PUN-01' });
    expect(getCockpit).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('express receive PO-PUN-0001')).toBeInTheDocument();
    expect(screen.queryByLabelText('Purchase shop')).toBeNull();
  });

  it('admin moving the shop filter on the cockpit reloads the open vendor for it', async () => {
    openOrdersWithPick('');
    await pressReceive();
    await screen.findByText('express receive PO-PUN-0001');
    fireEvent.change(screen.getByLabelText('Purchase shop'), {
      target: { value: 'BV-DHN-01' },
    });
    await waitFor(() =>
      expect(getCockpit).toHaveBeenLastCalledWith({ vendor_id: 'V2', store_id: 'BV-DHN-01' }),
    );
    expect(await screen.findByText(/no open pos for this vendor at this store/i)).toBeInTheDocument();
  });
});

describe('Deliveries inbox follows the same scope', () => {
  function openInbox(shop: string) {
    render(
      <MemoryRouter initialEntries={['/purchase/receive']}>
        <Pick shop={shop}>
          <GoodsReceiptCockpit />
        </Pick>
      </MemoryRouter>,
    );
  }

  it('admin on All stores: every shop, and each box says which shop it is for', async () => {
    openInbox('');
    expect(await screen.findByText('PO-PUN-0001')).toBeInTheDocument();
    expect(getPOs).toHaveBeenCalledWith({});
    expect(screen.getByText(/^For/).textContent).toBe('For WizOpt Pune');
  });

  it('admin on one shop: that shop, no per-box shop line', async () => {
    openInbox('WO-PUN-01');
    expect(await screen.findByText('PO-PUN-0001')).toBeInTheDocument();
    expect(getPOs).toHaveBeenCalledWith({ store_id: 'WO-PUN-01' });
    expect(screen.queryByText(/^For/)).toBeNull();
  });

  it('store manager: his own shop, as before', async () => {
    roles = ['STORE_MANAGER'];
    activeStoreId = 'WO-PUN-01';
    openInbox('');
    expect(await screen.findByText('PO-PUN-0001')).toBeInTheDocument();
    expect(getPOs).toHaveBeenCalledWith({ store_id: 'WO-PUN-01' });
    expect(getPOs).not.toHaveBeenCalledWith({});
  });

  it('admin on All stores, a vendor with nothing open: not "at this store"', async () => {
    getCockpit.mockResolvedValue({
      vendor: { vendor_id: 'V9' },
      open_pos: [],
      pending_not_received: [],
      pending_cataloged: [],
    });
    render(
      <MemoryRouter initialEntries={['/purchase/receive?vendor_id=V9']}>
        <Pick shop="">
          <GoodsReceiptCockpit />
        </Pick>
      </MemoryRouter>,
    );
    expect(
      await screen.findByText('No open POs for this vendor in any store.'),
    ).toBeInTheDocument();
  });
});
