// ============================================================================
// Audit F63, review r2 #17 + #19: the Invoices tab's pickers and booking form
// obey the one Purchase shop
// ============================================================================
// #17: 'Create from GRN' and 'Match DCs to Invoice' asked for the admin's
// topbar shop (Dhanbad) while the invoice list beside them asked for the shop
// he picked (Pune) -- or for every store. Pune's accepted receipt could not be
// billed from the tab, and the empty state said there was nothing to bill.
// They now read the list's scope; on All stores each row names its shop, and
// an empty picker says which shops it looked in.
//
// #19: the server books a manual bill to the booker's active shop (a receipt's
// bill to the receipt's shop), whatever shop the admin's list shows. A manual
// bill raised while he viewed Pune went to Dhanbad and vanished from the list
// with nothing said. The form now says where bills go, the toast names the
// shop, and the list moves there.
//
// Only the HTTP client is mocked, so vendorAp / vendorsApi run as in the app.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useEffect, useState, type ReactNode } from 'react';
import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react';

let roles: string[] = ['ADMIN'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Owner', roles, activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasRole: (want: string[]) => roles.some((r) => r === 'ADMIN' || r === 'SUPERADMIN' || want.includes(r)),
    hasPermission: () => true,
  }),
}));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get: http.get, post: http.post, put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { usePurchaseShop } from '../purchaseShop';
import { PurchaseInvoicesTab } from '../invoices/PurchaseInvoicesTab';

const STORES = [
  { store_id: 'BV-DHN-01', store_name: 'Better Vision Dhanbad' },
  { store_id: 'WO-PUN-01', store_name: 'WizOpt Pune' },
];
const PUNE_GRN = {
  grn_id: 'G-P', grn_number: 'RCPT 0007', vendor_id: 'V1', vendor_name: 'Frames Wala',
  store_id: 'WO-PUN-01', status: 'ACCEPTED', total_accepted: 4, po_number: 'PO-9',
};
const DHN_GRN = { ...PUNE_GRN, grn_id: 'G-D', grn_number: 'RCPT 0008', store_id: 'BV-DHN-01' };
const PUNE_DC = {
  grn_id: 'DC-P', dc_number: 'DC 31', vendor_id: 'V1', vendor_name: 'Frames Wala',
  store_id: 'WO-PUN-01', dc_date: '2026-09-20', total_accepted: 2,
};
// What the server's GET /from-dcs draft carries: no store_id. The /from-grn
// draft adds the shop the bill books to (the receipt's), as the server does.
const DRAFT = {
  status: 'DRAFT', vendor_id: 'V1', vendor_name: 'Frames Wala',
  lines: [{ product_id: 'P1', description: 'Frame', hsn: '9003', qty: 1, unit_price: 100, gst_rate: 5 }],
};
const PREVIEW = {
  interstate: false, supplier_state: '27', supply_place_recipient: '27', recipient_gstin: '27ZZZZZ9999Z1Z9',
  lines: [{ taxable: 100, gst_rate: 5, cgst: 2.5, sgst: 2.5, igst: 0, line_total: 105 }],
  taxable_total: 100, cgst_total: 2.5, sgst_total: 2.5, igst_total: 0, tax_total: 5, total: 105,
};

type Params = Record<string, unknown> | undefined;
let grns: Record<string, unknown>[] = [];
let dcs: Record<string, unknown>[] = [];
let bookedStore = 'BV-DHN-01';

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  grns = [];
  dcs = [];
  bookedStore = 'BV-DHN-01';
  http.get.mockImplementation(async (url: string, cfg?: { params?: Params }) => {
    const p = cfg?.params ?? {};
    if (url === '/stores') return { data: { stores: STORES } };
    if (url === '/vendors/purchase-invoices') return { data: { purchase_invoices: [], total: 0 } };
    if (url === '/vendors/grn') return { data: { grns: p.grn_subtype ? dcs : grns.filter((g) => g.status === p.status) } };
    if (url.startsWith('/vendors/purchase-invoices/from-grn/')) {
      const grn = grns.find((g) => url.endsWith(`/${g.grn_id}`));
      return { data: { ...DRAFT, store_id: grn?.store_id } };
    }
    if (url.startsWith('/vendors/purchase-invoices/from-')) return { data: DRAFT };
    return { data: null };
  });
  http.post.mockImplementation(async (url: string) =>
    url.endsWith('/preview')
      ? { data: PREVIEW }
      : { data: { bill_id: 'B9', vendor_id: 'V1', store_id: bookedStore, total_amount: 105 } },
  );
});

/** Sets the admin's shop pick (the shared Purchase filter) once, then mounts
 *  the tab -- and keeps it mounted if the tab itself moves the filter. */
function Pick({ shop, children }: { shop: string; children: ReactNode }) {
  const { setShop } = usePurchaseShop();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setShop(shop);
    setReady(true);
  }, [shop, setShop]);
  return ready ? <>{children}</> : null;
}

/** Shows the admin's current pick, as the Purchase header's picker would. */
function ShopNow() {
  const { shop } = usePurchaseShop();
  return <output data-testid="shop-now">{shop}</output>;
}

function openTab(shop: string) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <Pick shop={shop}>
          <ShopNow />
          <PurchaseInvoicesTab suppliers={[{ id: 'V1', name: 'Frames Wala', gstNumber: '27ABCDE1234F1Z5' }] as never} />
        </Pick>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** store_id of every GET to `url` matching `pick` (undefined = none sent). */
function scopes(url: string, pick: (p: Record<string, unknown>) => boolean = () => true) {
  return http.get.mock.calls
    .filter(([u, cfg]) => u === url && pick(((cfg as { params?: Params })?.params ?? {}) as Record<string, unknown>))
    .map(([, cfg]) => (cfg as { params?: { store_id?: string } })?.params?.store_id);
}
const isReceipt = (p: Record<string, unknown>) => !p.grn_subtype;
const isDc = (p: Record<string, unknown>) => p.grn_subtype === 'DELIVERY_CHALLAN';

async function openPicker(label: RegExp) {
  await waitFor(() => expect(scopes('/vendors/purchase-invoices').length).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole('button', { name: label }));
}
const modal = () => within(document.querySelector('.fixed') as HTMLElement);

describe('#17: Create from GRN reads the one Purchase shop', () => {
  it('an admin on Dhanbad who picks Pune is offered Pune\'s receipts, not Dhanbad\'s', async () => {
    roles = ['ADMIN'];
    openTab('WO-PUN-01');
    await openPicker(/Create from GRN/);
    await waitFor(() => expect(scopes('/vendors/grn', isReceipt)).toHaveLength(2));
    expect(new Set(scopes('/vendors/grn', isReceipt))).toEqual(new Set(['WO-PUN-01']));
    expect(await modal().findByText(/No accepted GRNs to invoice at WizOpt Pune\./)).toBeTruthy();
  });

  it('with no pick (All stores) it asks for every store and each row names its shop', async () => {
    roles = ['ADMIN'];
    grns = [PUNE_GRN, DHN_GRN];
    openTab('');
    await openPicker(/Create from GRN/);
    await waitFor(() => expect(scopes('/vendors/grn', isReceipt)).toHaveLength(2));
    expect(new Set(scopes('/vendors/grn', isReceipt))).toEqual(new Set([undefined]));
    const pune = (await modal().findByText('RCPT 0007')).closest('.border') as HTMLElement;
    await waitFor(() => expect(within(pune).getByText(/^For /).textContent).toBe('For WizOpt Pune'));
    const dhn = modal().getByText('RCPT 0008').closest('.border') as HTMLElement;
    expect(within(dhn).getByText(/^For /).textContent).toBe('For Better Vision Dhanbad');
  });

  it('an empty All-stores picker says it looked in every store', async () => {
    roles = ['ADMIN'];
    openTab('');
    await openPicker(/Create from GRN/);
    expect(await modal().findByText(/No accepted GRNs to invoice in any store\./)).toBeTruthy();
  });

  it('an accountant (no picker) is offered his own shop', async () => {
    roles = ['ACCOUNTANT'];
    openTab('');
    await openPicker(/Create from GRN/);
    await waitFor(() => expect(scopes('/vendors/grn', isReceipt)).toHaveLength(2));
    expect(new Set(scopes('/vendors/grn', isReceipt))).toEqual(new Set(['BV-DHN-01']));
  });

  it("billing Pune's receipt from All stores: the form says the bill goes to Pune", async () => {
    roles = ['ADMIN'];
    grns = [PUNE_GRN];
    openTab('');
    await openPicker(/Create from GRN/);
    fireEvent.click(await modal().findByRole('button', { name: /Invoice/ }));
    const line = await screen.findByText(/Bills booked here go to/);
    await waitFor(() => expect(line.textContent).toBe('Bills booked here go to WizOpt Pune'));
  });
});

describe('#17: Match DCs to Invoice reads the one Purchase shop', () => {
  it('an admin on Dhanbad who picks Pune is offered Pune\'s challans', async () => {
    roles = ['ADMIN'];
    openTab('WO-PUN-01');
    await openPicker(/Match DCs to Invoice/);
    await waitFor(() => expect(scopes('/vendors/grn', isDc).length).toBeGreaterThan(0));
    expect(new Set(scopes('/vendors/grn', isDc))).toEqual(new Set(['WO-PUN-01']));
    expect(await modal().findByText(/No open Delivery Challans for this filter at WizOpt Pune\./)).toBeTruthy();
  });

  it('with no pick (All stores) it asks for every store; each challan names its shop', async () => {
    roles = ['ADMIN'];
    dcs = [PUNE_DC];
    openTab('');
    await openPicker(/Match DCs to Invoice/);
    await waitFor(() => expect(scopes('/vendors/grn', isDc).length).toBeGreaterThan(0));
    expect(new Set(scopes('/vendors/grn', isDc))).toEqual(new Set([undefined]));
    const row = (await modal().findByText(/DC DC 31/)).closest('label') as HTMLElement;
    await waitFor(() => expect(within(row).getByText(/^For /).textContent).toBe('For WizOpt Pune'));
  });

  it('an empty All-stores picker says it looked in every store', async () => {
    roles = ['ADMIN'];
    openTab('');
    await openPicker(/Match DCs to Invoice/);
    expect(await modal().findByText(/No open Delivery Challans for this filter in any store\./)).toBeTruthy();
  });

  it("a bill drafted from Pune's challan says it goes to Pune, not the admin's Dhanbad", async () => {
    // The server books a DC bill to the DCs' shop; the draft names none, so
    // the picker carries it -- it carried the admin's topbar shop.
    roles = ['ADMIN'];
    dcs = [PUNE_DC];
    openTab('');
    await openPicker(/Match DCs to Invoice/);
    fireEvent.click(await modal().findByRole('checkbox'));
    fireEvent.click(modal().getByRole('button', { name: /Generate Draft Invoice/ }));
    const line = await screen.findByText(/Bills booked here go to/);
    await waitFor(() => expect(line.textContent).toBe('Bills booked here go to WizOpt Pune'));
  });
});

async function bookManualServicesBill() {
  await waitFor(() => expect(scopes('/vendors/purchase-invoices').length).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole('button', { name: /Manual invoice/i }));
  await screen.findByText('This bill is for');
  fireEvent.change(screen.getByDisplayValue(/Choose: goods, or services/), { target: { value: 'SERVICES' } });
  fireEvent.change(screen.getByDisplayValue('Select supplier...'), { target: { value: 'V1' } });
  fireEvent.change(screen.getByPlaceholderText(/As printed on the supplier's bill/), { target: { value: 'FW-1' } });
  fireEvent.change(screen.getByPlaceholderText('Item description'), { target: { value: 'Freight' } });
  await screen.findByText(/Intra-state supply:/);
}

describe("#19: a manual bill says which shop it books to", () => {
  it('an admin viewing Pune is told bills go to his own Dhanbad; booking names it and the list moves there', async () => {
    roles = ['ADMIN'];
    openTab('WO-PUN-01');
    await bookManualServicesBill();
    const line = screen.getByText(/Bills booked here go to/);
    await waitFor(() => expect(line.textContent).toBe('Bills booked here go to Better Vision Dhanbad'));

    const readsBefore = scopes('/vendors/purchase-invoices').length;
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() =>
      expect(toastMock.success).toHaveBeenCalledWith('Purchase invoice booked to Better Vision Dhanbad'),
    );
    // The list now shows Dhanbad -- where the bill went -- and reloads only it
    // (a reload of Pune racing it could land last and hide the bill again).
    await waitFor(() => expect(screen.getByTestId('shop-now').textContent).toBe('BV-DHN-01'));
    await waitFor(() => expect(scopes('/vendors/purchase-invoices').length).toBeGreaterThan(readsBefore));
    expect(new Set(scopes('/vendors/purchase-invoices').slice(readsBefore))).toEqual(new Set(['BV-DHN-01']));
  });

  it('on All stores the toast names the shop, and the list stays on All stores', async () => {
    roles = ['ADMIN'];
    openTab('');
    await bookManualServicesBill();
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() =>
      expect(toastMock.success).toHaveBeenCalledWith('Purchase invoice booked to Better Vision Dhanbad'),
    );
    expect(screen.getByTestId('shop-now').textContent).toBe('');
  });

  it('an admin viewing his own shop gets the plain toast', async () => {
    roles = ['ADMIN'];
    openTab('BV-DHN-01');
    await bookManualServicesBill();
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Purchase invoice booked'));
  });

  it('an accountant (one shop, no picker) sees no shop line and the plain toast', async () => {
    roles = ['ACCOUNTANT'];
    openTab('');
    await bookManualServicesBill();
    expect(screen.queryByText(/Bills booked here go to/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Book invoice/i }));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Purchase invoice booked'));
  });
});
