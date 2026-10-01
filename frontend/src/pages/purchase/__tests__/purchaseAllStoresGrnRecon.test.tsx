// ============================================================================
// Audit F63: on "All stores" every part of a Purchase tab says all stores
// ============================================================================
// Goods received: with the admin's Shop picker on All stores the receipts list
// covered every shop while the "Select purchase order" picker still asked for
// his own active shop (GET /vendors/purchase-orders?store_id=BV-DHN-01), so a
// Pune order never appeared and the empty state read as if nothing was open.
// One scope per tab: the picker now reads what the list reads, names the shop
// each order delivers to, and its empty state says which shops it covers. A
// receipt is booked at its order's shop (the server re-points a standard
// receipt); a Delivery Challan is booked at the receiver's own shop, so one
// against another shop's order is refused. A GRN printed from the all-stores
// history carries its own shop, not the admin's.
// Recon console: an empty queue on All stores no longer says "this store".

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useEffect, useState, type ReactNode } from 'react';
import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react';

vi.stubGlobal('requestIdleCallback', () => 0);

let roles: string[] = ['ADMIN'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Owner', roles, activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasRole: (want: string[]) => roles.some((r) => r === 'ADMIN' || want.includes(r)),
    hasPermission: () => true,
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../components/print/GRNPrint', () => ({ GRNPrint: () => null }));
const identity = vi.hoisted(() => ({ resolveStoreIdentity: vi.fn() }));
vi.mock('../../../components/print/storeIdentity', () => identity);

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  return { ...actual, default: http, api: http };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { usePurchaseShop } from '../purchaseShop';
import { GoodsReceiptNote } from '../GoodsReceiptNote';
import ReconConsole from '../ReconConsole';

type Po = { po_id: string; po_number: string; vendor_name: string; status: string; delivery_store_id: string; items: unknown[] };

const LINE = [{ po_item_id: 'l1', product_id: 'P-1', product_name: 'Acme Aviator', quantity: 2 }];
// Fifty closed Dhanbad orders raised first, then Pune's open one and Dhanbad's.
const CLOSED: Po[] = Array.from({ length: 50 }, (_, i) => ({
  po_id: `po-old-${i}`,
  po_number: `PO-OLD-${i}`,
  vendor_name: 'Old Vendor',
  status: 'RECEIVED',
  delivery_store_id: 'BV-DHN-01',
  items: LINE,
}));
const PUNE_OPEN: Po = { po_id: 'po-pun-7', po_number: 'PO-PUN-7', vendor_name: 'Frames Wala', status: 'SENT', delivery_store_id: 'BV-PUN-01', items: LINE };
const DHN_OPEN: Po = { po_id: 'po-dhn-3', po_number: 'PO-DHN-3', vendor_name: 'Jharkhand Optical', status: 'SENT', delivery_store_id: 'BV-DHN-01', items: LINE };

let orders: Po[] = [];
let grns: unknown[] = [];

/** The list route as the server answers it: filter by shop and status, then
 *  the first 50 in the order they were raised (find_many, default limit). */
function listOrders(params?: { store_id?: string; status?: string }) {
  return orders
    .filter((p) => !params?.store_id || p.delivery_store_id === params.store_id)
    .filter((p) => !params?.status || p.status === params.status)
    .slice(0, 50);
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  identity.resolveStoreIdentity.mockResolvedValue(null);
  orders = [...CLOSED, PUNE_OPEN, DHN_OPEN];
  grns = [];
  http.get.mockImplementation((url: string, cfg?: { params?: { store_id?: string; status?: string } }) =>
    Promise.resolve({
      data:
        url === '/vendors/purchase-orders'
          ? { purchase_orders: listOrders(cfg?.params) }
          : url === '/stores'
            ? {
                stores: [
                  { store_id: 'BV-DHN-01', store_name: 'Better Vision Dhanbad' },
                  { store_id: 'BV-PUN-01', store_name: 'WizOpt Pune' },
                ],
              }
            : url === '/vendors/grn'
              ? { grns }
              : url === '/vendors/purchase-invoices'
                ? { purchase_invoices: [] }
                : url === '/vendors/recon/worklists'
                  ? {
                      stock_yet_to_receive: [],
                      vendor_returns: [],
                      pending_credit_notes_scheme: [],
                      pending_credit_notes_return: [],
                    }
                  : {},
    }),
  );
  http.post.mockResolvedValue({ data: { grn_id: 'GRN-NEW' } });
});

/** Sets the admin's shop pick (the shared Purchase filter) once, before the tab
 *  mounts; the tab's own Shop picker may move it afterwards. */
function Pick({ shop, children }: { shop: string; children: ReactNode }) {
  const { setShop } = usePurchaseShop();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setShop(shop);
    setReady(true);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return ready ? <>{children}</> : null;
}

function open(node: ReactNode, shop: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <Pick shop={shop}>{node}</Pick>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The store_id of every read of `url` (undefined = none sent). */
const scopes = (url: string) =>
  http.get.mock.calls
    .filter(([u]) => u === url)
    .map(([, cfg]) => (cfg as { params?: { store_id?: string } } | undefined)?.params?.store_id);

const poPicker = () => screen.getByDisplayValue('Select a PO…') as HTMLSelectElement;

describe('F63: Goods received on All stores -- the PO picker reads what the list reads', () => {
  it("an admin on All stores sees every shop's open orders, each naming the shop it delivers to", async () => {
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, '');
    expect(await screen.findByText('PO-PUN-7 · Frames Wala · delivers to WizOpt Pune')).toBeInTheDocument();
    expect(screen.getByText('PO-DHN-3 · Jharkhand Optical · delivers to Better Vision Dhanbad')).toBeInTheDocument();
    // One scope for the tab: no read asks for his own shop, the list included.
    expect(new Set(scopes('/vendors/purchase-orders'))).toEqual(new Set([undefined]));
    expect(new Set(scopes('/vendors/grn'))).toEqual(new Set([undefined]));
  });

  it('an admin who picks Pune, and a manager on his own shop, see their shop only -- no shop names', async () => {
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, 'BV-PUN-01');
    expect(await screen.findByText('PO-PUN-7 · Frames Wala')).toBeInTheDocument();
    expect(screen.queryByText(/PO-DHN-3/)).toBeNull();
    expect(new Set(scopes('/vendors/purchase-orders'))).toEqual(new Set(['BV-PUN-01']));
    cleanup();
    http.get.mockClear();
    roles = ['STORE_MANAGER'];
    open(<GoodsReceiptNote />, '');
    expect(await screen.findByText('PO-DHN-3 · Jharkhand Optical')).toBeInTheDocument();
    expect(screen.queryByText(/PO-PUN-7/)).toBeNull();
    expect(new Set(scopes('/vendors/purchase-orders'))).toEqual(new Set(['BV-DHN-01']));
  });

  it('the empty picker says which shops it covers', async () => {
    orders = [...CLOSED];
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, '');
    expect(await screen.findByText(/No open purchase orders to receive against in any store\./)).toBeInTheDocument();
    cleanup();
    open(<GoodsReceiptNote />, 'BV-PUN-01');
    expect(
      await screen.findByText(/No open purchase orders to receive against at the shop picked above\./),
    ).toBeInTheDocument();
    cleanup();
    roles = ['STORE_MANAGER'];
    open(<GoodsReceiptNote />, '');
    expect(await screen.findByText(/No open purchase orders to receive against at your shop\./)).toBeInTheDocument();
  });

  it("a Delivery Challan against Pune's order is refused for an admin on Dhanbad (it would be booked at Dhanbad)", async () => {
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, '');
    await screen.findByText(/PO-PUN-7/);
    fireEvent.click(screen.getByLabelText(/This is a Delivery Challan/i, { selector: 'input' }));
    fireEvent.change(screen.getByPlaceholderText(/DC\/26\/05\/118/), { target: { value: 'DC/26/09/4' } });
    const picker = screen.getByDisplayValue('No PO — receiving over the counter') as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: 'po-pun-7' } });
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('This order delivers to another shop')),
    );
    expect(http.post).not.toHaveBeenCalled();

    // His own shop's order on a Delivery Challan still posts.
    fireEvent.change(picker, { target: { value: 'po-dhn-3' } });
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() => expect(http.post).toHaveBeenCalledWith('/vendors/grn', expect.anything()));
    expect((http.post.mock.calls[0][1] as { po_id: string }).po_id).toBe('po-dhn-3');
  });

  it('a Delivery Challan with no order still posts on All stores (it lands at his own shop, which All stores shows)', async () => {
    roles = ['ADMIN'];
    http.get.mockImplementation((url: string) =>
      Promise.resolve({
        data: url === '/vendors' || url === '/vendors/'
          ? { vendors: [{ vendor_id: 'V-77', trade_name: 'Frames Wala' }] }
          : url === '/vendors/grn' ? { grns: [] }
            : url === '/products'
              ? { products: [{ product_id: 'P-FR1', name: 'Acme Aviator', sku: 'FR-0001' }] }
              : { purchase_orders: [] },
      }),
    );
    open(<GoodsReceiptNote />, '');
    fireEvent.click(await screen.findByLabelText(/This is a Delivery Challan/i, { selector: 'input' }));
    fireEvent.change(screen.getByPlaceholderText(/DC\/26\/05\/118/), { target: { value: 'DC/26/09/5' } });
    await screen.findByText('Frames Wala');
    fireEvent.change(screen.getByDisplayValue('Select the vendor…'), { target: { value: 'V-77' } });
    fireEvent.change(screen.getByPlaceholderText(/Search a product to add/i), { target: { value: 'frame' } });
    fireEvent.click(await screen.findByText('Acme Aviator', {}, { timeout: 3000 }));
    fireEvent.click(screen.getByLabelText(/Tally line 1/));
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() => expect(http.post).toHaveBeenCalledWith('/vendors/grn', expect.anything()));
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("a standard receipt against Pune's order is sent against that order (the server books it at Pune)", async () => {
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, '');
    await screen.findByText(/PO-PUN-7/);
    fireEvent.change(poPicker(), { target: { value: 'po-pun-7' } });
    fireEvent.change(screen.getByPlaceholderText('e.g. JJ/24/04/2240'), { target: { value: 'INV-9' } });
    fireEvent.click(screen.getByLabelText(/Tally line 1/));
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() => expect(http.post).toHaveBeenCalledWith('/vendors/grn', expect.anything()));
    const body = http.post.mock.calls[0][1] as Record<string, unknown>;
    expect(body.po_id).toBe('po-pun-7');
    expect(body.grn_subtype).toBe('STANDARD');
    expect(body).not.toHaveProperty('store_id');
  });

  it("narrowing the shop after picking Pune's order drops the pick: nothing posts against an order no longer shown", async () => {
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, '');
    await screen.findByText(/PO-PUN-7/);
    fireEvent.click(screen.getByLabelText(/This is a Delivery Challan/i, { selector: 'input' }));
    fireEvent.change(screen.getByPlaceholderText(/DC\/26\/05\/118/), { target: { value: 'DC/26/09/6' } });
    fireEvent.change(screen.getByDisplayValue('No PO — receiving over the counter'), { target: { value: 'po-pun-7' } });
    expect(screen.getByRole('button', { name: /Post GRN/i })).toBeEnabled();
    // The admin narrows Shop to his own Dhanbad: Pune's order leaves the picker.
    const shop = screen.getByLabelText('Purchase shop');
    await within(shop).findByText('Better Vision Dhanbad');
    fireEvent.change(shop, { target: { value: 'BV-DHN-01' } });
    await waitFor(() => expect(screen.queryByText(/PO-PUN-7/)).toBeNull());
    await waitFor(() => expect(screen.getByRole('button', { name: /Post GRN/i })).toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    expect(http.post).not.toHaveBeenCalled();
  });

  it("a Pune GRN printed from the All-stores history carries Pune's identity, not the admin's Dhanbad", async () => {
    grns = [{ grn_id: 'g-pun', grn_number: 'GRN-PUN-1', po_number: 'PO-PUN-7', store_id: 'BV-PUN-01', items: [] }];
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, '');
    fireEvent.click(await screen.findByRole('button', { name: /History/ }));
    fireEvent.click(await screen.findByTitle('Print GRN'));
    await waitFor(() => expect(identity.resolveStoreIdentity).toHaveBeenCalled());
    expect(identity.resolveStoreIdentity).toHaveBeenCalledWith('BV-PUN-01');
    expect(identity.resolveStoreIdentity).not.toHaveBeenCalledWith('BV-DHN-01');
  });
});

describe('F63: the Recon console says which shops an empty queue covers', () => {
  it('All stores reads "in any store", a picked shop "the shop picked above", an accountant "your shop"', async () => {
    roles = ['ADMIN'];
    open(<ReconConsole />, '');
    expect(await screen.findByText('No purchase invoices found in any store.')).toBeInTheDocument();
    expect(screen.queryByText(/this store/i)).toBeNull();
    cleanup();
    open(<ReconConsole />, 'BV-PUN-01');
    expect(await screen.findByText('No purchase invoices found for the shop picked above.')).toBeInTheDocument();
    cleanup();
    roles = ['ACCOUNTANT'];
    open(<ReconConsole />, '');
    expect(await screen.findByText('No purchase invoices found for your shop.')).toBeInTheDocument();
  });
});
