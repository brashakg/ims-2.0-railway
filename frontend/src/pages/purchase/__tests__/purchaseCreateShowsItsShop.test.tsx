// ============================================================================
// Audit F63: what an admin raises never vanishes from the list he is viewing
// ============================================================================
// The lists obey the admin's shop pick; a new order or return is raised at his
// OWN shop (the server's rule: POs deliver to the active store, W1.4; a return
// links the quarantined units of the shop it is raised at). Before: an admin on
// Dhanbad viewing Pune created a PO that went to Dhanbad but sat on top of the
// Pune list, and a new return simply disappeared. Now the list moves to the
// shop it was raised at. Goods receipt works the shown shop's orders (a
// receipt lands in its order's shop), and a receipt with no order -- which
// the server records at the receiver's own shop -- is refused while another
// shop is showing. The GRN counts say which shops they cover.

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
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));
// The PO form itself is not under test: it hands back a created order.
vi.mock('../PurchaseOrderForm', () => ({
  PurchaseOrderForm: ({ onCreated }: { onCreated: (po: unknown) => void }) => (
    <button
      type="button"
      onClick={() => onCreated({ id: 'po-new', poNumber: 'PO-NEW', supplierName: 'Jharkhand Optical', status: 'DRAFT', items: [] })}
    >
      make the PO
    </button>
  ),
}));

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  return { ...actual, default: http, api: http };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { usePurchaseShop } from '../purchaseShop';
import { PurchaseOrdersSection } from '../PurchaseOrdersSection';
import { VendorReturns } from '../VendorReturns';
import { GoodsReceiptNote } from '../GoodsReceiptNote';

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  http.get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url === '/vendors/purchase-orders'
        ? { purchase_orders: [] }
        : url === '/vendors/' || url === '/vendors'
          ? { vendors: [{ vendor_id: 'v1', legal_name: 'Jharkhand Optical', trade_name: 'Jharkhand Optical' }] }
          : url.startsWith('/vendor-returns')
            ? { returns: [] }
            : url === '/vendors/grn'
              ? { grns: [] }
              : {},
    }),
  );
  http.post.mockResolvedValue({ data: {} });
});

/** The admin's shop pick, set once before the screen mounts; `shopNow`
 *  reports where the screen moves it afterwards. */
let shopNow = '';
function Pick({ shop, children }: { shop: string; children: ReactNode }) {
  const { setShop, shop: current } = usePurchaseShop();
  const [ready, setReady] = useState(false);
  shopNow = current;
  useEffect(() => {
    setShop(shop);
    setReady(true);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return ready ? <>{children}</> : null;
}

function open(node: ReactNode, shop: string, at = '/purchase') {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[at]}>
        <Pick shop={shop}>{node}</Pick>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const reads = (url: string) =>
  http.get.mock.calls
    .filter(([u]) => u === url)
    .map(([, cfg]) => (cfg as { params?: { store_id?: string } } | undefined)?.params?.store_id);

describe('F63: a new PO or return shows in its own shop', () => {
  it('an admin viewing Pune creates a PO: it delivers to Dhanbad and the list moves to Dhanbad', async () => {
    roles = ['ADMIN'];
    open(<PurchaseOrdersSection />, 'BV-PUN-01', '/purchase/orders?new=1');
    fireEvent.click(await screen.findByRole('button', { name: 'make the PO' }));
    await waitFor(() => expect(shopNow).toBe('BV-DHN-01'));
    await waitFor(() => expect(reads('/vendors/purchase-orders')).toContain('BV-DHN-01'));
  });

  it('an admin viewing Pune raises a return at Dhanbad: the list moves to Dhanbad and shows it', async () => {
    roles = ['ADMIN'];
    open(<VendorReturns />, 'BV-PUN-01');
    fireEvent.click(await screen.findByRole('button', { name: /create return/i }));
    const vendorBox = (await screen.findByText('Select Vendor...')).closest('select')!;
    await within(vendorBox).findByText('Jharkhand Optical');
    fireEvent.change(vendorBox, { target: { value: 'v1' } });
    fireEvent.change(screen.getByPlaceholderText('Enter product name'), { target: { value: 'Frame' } });
    const buttons = screen.getAllByRole('button', { name: /create return/i });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(http.post).toHaveBeenCalled());
    expect((http.post.mock.calls[0][1] as { store_id: string }).store_id).toBe('BV-DHN-01');
    await waitFor(() => expect(shopNow).toBe('BV-DHN-01'));
    await waitFor(() => expect(reads('/vendor-returns/')).toContain('BV-DHN-01'));
  });
});

describe('F63: goods receipt works the shop on show', () => {
  it('an admin viewing Pune receives against Pune\'s orders', async () => {
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, 'BV-PUN-01');
    await waitFor(() => expect(reads('/vendors/purchase-orders')).toContain('BV-PUN-01'));
    expect(reads('/vendors/purchase-orders')).not.toContain('BV-DHN-01');
  });

  it('the counts say which shops they cover -- never "all stores" for one shop', async () => {
    roles = ['ADMIN'];
    open(<GoodsReceiptNote />, 'BV-PUN-01');
    expect(await screen.findByText('the shop picked above')).toBeInTheDocument();
    expect(screen.queryByText(/all stores in scope/i)).toBeNull();
    cleanup();
    open(<GoodsReceiptNote />, '');
    expect(await screen.findByText('all stores')).toBeInTheDocument();
    cleanup();
    roles = ['STORE_MANAGER'];
    open(<GoodsReceiptNote />, '');
    expect(await screen.findByText('your shop')).toBeInTheDocument();
  });

  it('a receipt with no order is refused while another shop is on show (it would land at Dhanbad)', async () => {
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
    open(<GoodsReceiptNote />, 'BV-PUN-01');
    fireEvent.click(await screen.findByLabelText(/This is a Delivery Challan/i, { selector: 'input' }));
    fireEvent.change(screen.getByPlaceholderText(/DC\/26\/05\/118/), { target: { value: 'DC/26/08/9' } });
    await screen.findByText('Frames Wala');
    fireEvent.change(screen.getByDisplayValue('Select the vendor…'), { target: { value: 'V-77' } });
    fireEvent.change(screen.getByPlaceholderText(/Search a product to add/i), { target: { value: 'frame' } });
    fireEvent.click(await screen.findByText('Acme Aviator', {}, { timeout: 3000 }));
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('recorded at your own shop')),
    );
    expect(http.post).not.toHaveBeenCalled();
  });
});
