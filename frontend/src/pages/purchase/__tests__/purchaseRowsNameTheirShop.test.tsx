// ============================================================================
// Review round 3: #10 every Purchase list row names its shop on All stores,
// #12 Goods received counts taken over the newest page say so, #13 the
// Create-from-GRN picker says when it is a cut, #16 the Analytics PO value
// tile reads whole rupees below a lakh
// ============================================================================
// #10: an admin on All stores (his default since F63) saw two shops' bills
// from one supplier as identical rows -- no row, card or return said which
// shop owed it, was waiting on the order, or had received the goods. On All
// stores each row now names its shop; with one shop picked the filter says
// it, so the rows do not repeat it.
//
// #12: Total GRNs is the server's count (120), but the Quality tiles and the
// tab badges counted the 50-row page beside it without saying so.
// #13: the Create-from-GRN picker held the newest 50 accepted receipts chain-
// wide with nothing saying the list was cut, although the route sends `total`.
// #16: the Analytics PO value tile printed Rs 3,000 as "Rs 0.0L".
//
// Only the HTTP client is mocked, so vendorsApi / vendorAp / the mappers run
// as in the app.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useEffect, useState, type ReactNode } from 'react';
import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react';

vi.stubGlobal('requestIdleCallback', () => 0);

let roles: string[] = ['ADMIN'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Owner', roles, activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasRole: (want: string[]) => roles.some((r) => r === 'ADMIN' || r === 'SUPERADMIN' || want.includes(r)),
    hasPermission: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../components/print/GRNPrint', () => ({ GRNPrint: () => null }));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));

const get = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { usePurchaseShop } from '../purchaseShop';
import { PurchaseInvoicesTab } from '../invoices/PurchaseInvoicesTab';
import { PurchaseTable } from '../PurchaseTable';
import { mapPOtoPurchaseOrder } from '../purchaseMappers';
import { GoodsReceiptNote } from '../GoodsReceiptNote';
import { VendorReturns } from '../VendorReturns';
import { PurchaseAnalytics } from '../PurchaseAnalytics';
import type { PurchaseOrder } from '../purchaseTypes';

const STORES = [
  { store_id: 'BV-DHN-01', store_name: 'Better Vision Dhanbad' },
  { store_id: 'WO-PUN-01', store_name: 'WizOpt Pune' },
];

const bill = (id: string, no: string, store: string) => ({
  purchase_invoice_id: id, vendor_id: 'V-ESS', vendor_name: 'Essilor', vendor_invoice_no: no,
  vendor_invoice_date: '2026-09-20', store_id: store, taxable_amount: 1000, cgst: 25, sgst: 25,
  total_amount: 1050, status: 'OUTSTANDING',
});
const BILLS = [bill('PI-D', 'INV-D', 'BV-DHN-01'), bill('PI-P', 'INV-P', 'WO-PUN-01')];

const po = (n: string, store?: string) => ({
  po_id: `po-${n}`, po_number: `PO-${n}`, vendor_id: 'V-ESS', vendor_name: 'Essilor', status: 'DRAFT',
  total_amount: 60, created_at: '2026-09-20T10:00:00', items: [],
  ...(store ? { delivery_store_id: store } : {}),
});

const grn = (i: number, store = 'BV-DHN-01', rejected = 0) => ({
  grn_id: `grn-${i}`, grn_number: `GRN-${i}`, po_id: `po-${i}`, po_number: `PO-${i}`, store_id: store,
  status: 'ACCEPTED', total_received: 2, total_accepted: 2 - rejected, total_rejected: rejected,
  created_at: '2026-09-20T10:00:00', items: [],
});

const ret = (id: string, store: string) => ({
  return_id: id, vendor_id: 'V-ESS', vendor_name: 'Essilor', store_id: store, items: [],
  return_type: 'credit_note', status: 'created', created_at: '2026-09-20T10:00:00', created_by: 'u1', notes: '',
});

/** What the list routes answer in a test. */
let lists: {
  bills: unknown[];
  grns: unknown[];
  grnTotal?: number;
  pickerGrns?: Record<string, { rows: unknown[]; total?: number }>;
  returns: unknown[];
};

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  roles = ['ADMIN'];
  lists = { bills: [], grns: [], returns: [] };
  get.mockImplementation(async (url: string, cfg?: { params?: Record<string, unknown> }) => {
    const p = cfg?.params ?? {};
    if (url === '/stores') return { data: { stores: STORES } };
    if (url === '/vendors/purchase-invoices') return { data: { purchase_invoices: lists.bills, total: lists.bills.length } };
    if (url === '/vendors/grn') {
      // The Create-from-GRN picker reads one status per call; the tab reads all.
      const page = p.status ? lists.pickerGrns?.[String(p.status)] : undefined;
      if (p.status) return { data: { grns: page?.rows ?? [], ...(page?.total === undefined ? {} : { total: page.total }) } };
      return { data: { grns: lists.grns, ...(lists.grnTotal === undefined ? {} : { total: lists.grnTotal }) } };
    }
    if (url.startsWith('/vendor-returns')) return { data: { returns: lists.returns } };
    if (url === '/vendors' || url === '/vendors/') return { data: { vendors: [] } };
    return { data: null };
  });
});

/** Sets the admin's shop pick (the shared Purchase filter) before the screen mounts. */
function Pick({ shop, children }: { shop: string; children: ReactNode }) {
  const { setShop } = usePurchaseShop();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setShop(shop);
    setReady(true);
  }, [shop, setShop]);
  return ready ? <>{children}</> : null;
}

function open(node: ReactNode, shop = '') {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <Pick shop={shop}>{node}</Pick>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The shop line printed on the row/card holding `anchor`. */
async function shopOf(anchor: HTMLElement, box: string) {
  const row = anchor.closest(box) as HTMLElement;
  const line = within(row).getByTestId('row-shop');
  await waitFor(() => expect(line.textContent).not.toMatch(/^For [A-Z]{2}-/)); // name, not the id
  return line.textContent;
}

describe('#10 Purchase Invoices: on All stores each bill names the shop it is booked to', () => {
  it('two shops\' bills from one supplier are told apart by their shop', async () => {
    lists.bills = BILLS;
    open(<PurchaseInvoicesTab suppliers={[]} />);
    expect(await shopOf(await screen.findByText('INV-D'), 'tr')).toBe('For Better Vision Dhanbad');
    expect(await shopOf(screen.getByText('INV-P'), 'tr')).toBe('For WizOpt Pune');
  });

  it('with one shop picked the rows do not repeat it', async () => {
    lists.bills = [BILLS[1]];
    open(<PurchaseInvoicesTab suppliers={[]} />, 'WO-PUN-01');
    await screen.findByText('INV-P');
    expect(screen.queryAllByTestId('row-shop')).toHaveLength(0);
  });

  it('an accountant (his own shop) sees no per-row shop', async () => {
    roles = ['ACCOUNTANT'];
    lists.bills = [BILLS[0]];
    open(<PurchaseInvoicesTab suppliers={[]} />);
    await screen.findByText('INV-D');
    expect(screen.queryAllByTestId('row-shop')).toHaveLength(0);
  });
});

describe('#10 Purchase Orders: on All stores each card names the shop the order delivers to', () => {
  const orders = (): PurchaseOrder[] =>
    [po('D', 'BV-DHN-01'), po('P', 'WO-PUN-01'), po('OLD')].map(mapPOtoPurchaseOrder);

  it('each card carries its delivery shop (the mapper keeps delivery_store_id)', async () => {
    open(<PurchaseTable purchaseOrders={orders()} onViewPO={() => {}} />);
    expect(await shopOf(await screen.findByText('PO-D'), '.card')).toBe('For Better Vision Dhanbad');
    expect(await shopOf(screen.getByText('PO-P'), '.card')).toBe('For WizOpt Pune');
    const old = screen.getByText('PO-OLD').closest('.card') as HTMLElement;
    expect(within(old).getByTestId('row-shop').textContent).toBe('No shop on record');
  });

  it('with one shop picked the cards do not repeat it', async () => {
    open(<PurchaseTable purchaseOrders={orders().slice(1, 2)} onViewPO={() => {}} />, 'WO-PUN-01');
    await screen.findByText('PO-P');
    expect(screen.queryAllByTestId('row-shop')).toHaveLength(0);
  });
});

describe('#10 Goods received: on All stores each receipt names the shop it was booked at', () => {
  it('the History and Discrepancies cards name each receipt\'s shop', async () => {
    lists.grns = [grn(1, 'BV-DHN-01'), grn(2, 'WO-PUN-01', 1)];
    lists.grnTotal = 2;
    open(<GoodsReceiptNote />);
    fireEvent.click(await screen.findByRole('button', { name: /History/ }));
    expect(await shopOf(await screen.findByText('GRN-1'), '.card')).toBe('For Better Vision Dhanbad');
    expect(await shopOf(screen.getByText('GRN-2'), '.card')).toBe('For WizOpt Pune');
    fireEvent.click(screen.getByRole('button', { name: /Discrepancies/ }));
    expect(await shopOf(await screen.findByText('GRN-2'), '.card')).toBe('For WizOpt Pune');
  });

  it('with one shop picked the cards do not repeat it', async () => {
    lists.grns = [grn(2, 'WO-PUN-01')];
    lists.grnTotal = 1;
    open(<GoodsReceiptNote />, 'WO-PUN-01');
    fireEvent.click(await screen.findByRole('button', { name: /History/ }));
    await screen.findByText('GRN-2');
    expect(screen.queryAllByTestId('row-shop')).toHaveLength(0);
  });
});

describe('#10 Vendor Returns: on All stores each return names the shop it was raised at', () => {
  it('each return card carries its shop', async () => {
    lists.returns = [ret('RET-D', 'BV-DHN-01'), ret('RET-P', 'WO-PUN-01')];
    open(<VendorReturns />);
    expect(await shopOf(await screen.findByText('Return ID: RET-D'), 'button')).toBe('For Better Vision Dhanbad');
    expect(await shopOf(screen.getByText('Return ID: RET-P'), 'button')).toBe('For WizOpt Pune');
  });

  it('with one shop picked the cards do not repeat it', async () => {
    lists.returns = [ret('RET-P', 'WO-PUN-01')];
    open(<VendorReturns />, 'WO-PUN-01');
    await screen.findByText('Return ID: RET-P');
    expect(screen.queryAllByTestId('row-shop')).toHaveLength(0);
  });
});

/** The caption printed under a stat-strip tile's figure. */
const captionUnder = (label: string) =>
  screen.getByText(label).parentElement?.querySelector('.d')?.textContent;
/** A tab button's text. */
const tabText = (name: RegExp) => screen.getByRole('button', { name }).textContent;

describe('#12 Goods received: counts over the newest page say they cover the latest N', () => {
  it('50 of 120: the Quality tiles and both tab badges name the latest 50', async () => {
    lists.grns = Array.from({ length: 50 }, (_, i) => grn(i, 'BV-DHN-01', i < 3 ? 1 : 0));
    lists.grnTotal = 120;
    open(<GoodsReceiptNote />);
    await waitFor(() => expect(captionUnder('Total GRNs')).toBe('all stores · latest 50 shown'));
    expect(captionUnder('Quality passed')).toBe('clean receipts · of latest 50');
    expect(captionUnder('Conditional')).toBe('partial accept · of latest 50');
    expect(captionUnder('Failed quality')).toBe('debit note raised · of latest 50');
    expect(tabText(/History/)).toBe('History· latest 50 of 120');
    expect(tabText(/Discrepancies/)).toBe('Discrepancies· 3 in latest 50');
    fireEvent.click(screen.getByRole('button', { name: /Discrepancies/ }));
    expect(screen.getByText(/Checked over the latest 50 of 120 receipts only\./)).toBeInTheDocument();
  });

  it('a cut page with no discrepancy says none in the latest N -- not none at all', async () => {
    lists.grns = Array.from({ length: 50 }, (_, i) => grn(i));
    lists.grnTotal = 120;
    open(<GoodsReceiptNote />);
    await waitFor(() => expect(captionUnder('Total GRNs')).toBe('all stores · latest 50 shown'));
    fireEvent.click(screen.getByRole('button', { name: /Discrepancies/ }));
    expect(screen.getByText('No discrepancies in the latest 50 receipts.')).toBeInTheDocument();
  });

  it('every receipt in hand: the tiles and badges are plain counts', async () => {
    lists.grns = [grn(1), grn(2, 'BV-DHN-01', 1)];
    lists.grnTotal = 2;
    open(<GoodsReceiptNote />);
    await waitFor(() => expect(captionUnder('Total GRNs')).toBe('all stores'));
    expect(captionUnder('Quality passed')).toBe('clean receipts');
    expect(captionUnder('Conditional')).toBe('partial accept');
    expect(captionUnder('Failed quality')).toBe('debit note raised');
    expect(tabText(/History/)).toBe('History· 2');
    expect(tabText(/Discrepancies/)).toBe('Discrepancies· 1');
  });
});

describe('#13 Create from GRN: a picker that is a cut says latest N of M', () => {
  const accepted = (n: number, from = 0) => Array.from({ length: n }, (_, k) => ({
    ...grn(from + k), grn_number: `RCPT ${from + k}`, vendor_name: 'Essilor',
  }));
  const pickerOpen = async () => {
    open(<PurchaseInvoicesTab suppliers={[]} />);
    fireEvent.click(await screen.findByRole('button', { name: /Create from GRN/ }));
    return within(document.querySelector('.fixed') as HTMLElement);
  };

  it('50 of 130 accepted receipts on All stores: the picker says so and how to narrow it', async () => {
    lists.pickerGrns = { ACCEPTED: { rows: accepted(50), total: 130 }, PARTIALLY_ACCEPTED: { rows: [], total: 0 } };
    const modal = await pickerOpen();
    expect((await modal.findByTestId('grn-picker-cut')).textContent).toBe(
      'Latest 50 of 130 receipts, newest first. Pick a shop in the Shop filter to narrow the list.',
    );
  });

  it('an accountant\'s cut picker says latest N of M (he has no shop filter to point to)', async () => {
    roles = ['ACCOUNTANT'];
    lists.pickerGrns = { ACCEPTED: { rows: accepted(50), total: 61 }, PARTIALLY_ACCEPTED: { rows: accepted(2, 100), total: 2 } };
    const modal = await pickerOpen();
    expect((await modal.findByTestId('grn-picker-cut')).textContent).toBe('Latest 52 of 63 receipts, newest first.');
  });

  it('every receipt in hand (or a server that sends no count): nothing extra', async () => {
    lists.pickerGrns = { ACCEPTED: { rows: accepted(3), total: 3 }, PARTIALLY_ACCEPTED: { rows: accepted(1, 100) } };
    const modal = await pickerOpen();
    await modal.findByText(/RCPT 2/);
    expect(modal.queryByTestId('grn-picker-cut')).toBeNull();
  });
});

describe('#16 Analytics: the PO value tile is whole rupees below a lakh, never Rs 0.0L', () => {
  const orders = (n: number, each: number) =>
    Array.from({ length: n }, (_, i) => ({ ...mapPOtoPurchaseOrder(po(String(i))), total: each }));
  const valueTile = () =>
    screen.getByText(/^(Value of the latest|Total Value)/).nextElementSibling?.textContent;

  it('the latest 50 orders at Rs 60 each read Rs 3,000', () => {
    render(<PurchaseAnalytics purchaseOrders={orders(50, 60)} totalOrders={312} suppliers={[]} />);
    expect(valueTile()).toBe('₹3,000');
  });

  it('just under a lakh stays in rupees; a lakh and up reads in lakhs', () => {
    render(<PurchaseAnalytics purchaseOrders={orders(1, 99999.4)} suppliers={[]} />);
    expect(valueTile()).toBe('₹99,999');
    cleanup();
    render(<PurchaseAnalytics purchaseOrders={orders(3, 150000)} suppliers={[]} />);
    expect(valueTile()).toBe('₹4.5L');
  });
});
