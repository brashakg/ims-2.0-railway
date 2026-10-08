// ============================================================================
// Review round 2, #18: a Purchase list is the NEWEST page, and the screen
// never calls that page a total
// ============================================================================
// GET /vendors/purchase-orders and GET /vendors/grn used to answer the first
// 50 documents ever written, with `total` = the page's length. On All stores
// (an admin's default) Purchase Orders and Analytics held the chain's 50 OLDEST
// orders ("Total POs 50", a "Total Value" over those 50) and Goods received
// read "Total GRNs 50 · all stores". The server now answers newest first with
// `total` = every matching row (backend/tests/test_purchase_lists_newest_first
// .py); these pin the screen half: the count shown is the server's total, and
// a page that is a cut says "latest N of M" -- never N as a total.
//
// The HTTP client is mocked, not vendorsApi, so the request/response plumbing
// (purchaseQueries, vendorsApi.getGRNs) is exercised for real.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';

vi.stubGlobal('requestIdleCallback', () => 0);

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Owner', roles: ['ADMIN'], activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasRole: (want: string[]) => want.includes('ADMIN'),
    hasPermission: () => true,
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
vi.mock('../../../hooks/useStorePrintInfo', () => ({
  useStorePrintInfo: () => ({ storeName: '', address: '', city: '', state: '', pincode: '', stateCode: '' }),
}));
vi.mock('../../../components/print/GRNPrint', () => ({ GRNPrint: () => null }));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { meta: vi.fn().mockResolvedValue({ state_codes: [], entity_types: [] }) },
}));

const get = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { PurchaseOrdersSection } from '../PurchaseOrdersSection';
import { PurchaseAnalyticsSection } from '../PurchaseAnalyticsSection';
import { GoodsReceiptNote } from '../GoodsReceiptNote';

const po = (i: number) => ({
  po_id: `po-${i}`,
  po_number: `PO-${i}`,
  vendor_id: 'V-1',
  vendor_name: 'Frames Wala',
  status: 'SENT',
  delivery_store_id: 'BV-DHN-01',
  total_amount: 1000,
  created_at: '2026-09-20T10:00:00',
  items: [],
});
const grn = (i: number) => ({
  grn_id: `grn-${i}`,
  grn_number: `GRN-${i}`,
  po_id: `po-${i}`,
  po_number: `PO-${i}`,
  store_id: 'BV-DHN-01',
  status: 'ACCEPTED',
  total_received: 2,
  total_accepted: 2,
  total_rejected: 0,
  created_at: '2026-09-20T10:00:00',
  items: [],
});
const range = (n: number) => Array.from({ length: n }, (_, i) => i);

/** What the list routes answer: `rows` rows of a page, `total` matching in all
 *  (undefined = a server that sends no count). */
function serve(orders: { rows: number; total?: number }, grns: { rows: number; total?: number }) {
  get.mockImplementation((url: string) =>
    Promise.resolve({
      data:
        url === '/vendors/purchase-orders'
          ? { purchase_orders: range(orders.rows).map(po), ...(orders.total === undefined ? {} : { total: orders.total }) }
          : url === '/vendors/grn'
            ? { grns: range(grns.rows).map(grn), ...(grns.total === undefined ? {} : { total: grns.total }) }
            : url === '/vendors' || url === '/vendors/'
              ? { vendors: [], total: 0 }
              : url === '/stores'
                ? { stores: [] }
                : {},
    }),
  );
}

function open(node: ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The figure printed under a tile's label. */
const figureUnder = (label: string) => screen.getByText(label).nextElementSibling?.textContent;

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('#18 Purchase Orders: the latest page says it is the latest N of M', () => {
  it('50 of 312 orders on all stores: the list says so, and its filters say where they look', async () => {
    serve({ rows: 50, total: 312 }, { rows: 0, total: 0 });
    open(<PurchaseOrdersSection />);
    const cut = await screen.findByTestId('po-list-cut');
    expect(cut.textContent).toMatch(/Latest 50 of 312 orders, newest first\./);
    expect(cut.textContent).toMatch(/look only in these 50/);
  });

  it('a page that holds every order says nothing extra', async () => {
    serve({ rows: 3, total: 3 }, { rows: 0, total: 0 });
    open(<PurchaseOrdersSection />);
    await screen.findByText('PO-2');
    expect(screen.queryByTestId('po-list-cut')).toBeNull();
  });

  it('a server that sends no count is read as just the rows in hand', async () => {
    serve({ rows: 4 }, { rows: 0 });
    open(<PurchaseOrdersSection />);
    await screen.findByText('PO-3');
    expect(screen.queryByTestId('po-list-cut')).toBeNull();
  });
});

describe('#18 Analytics: Total POs is the real count; page sums are labelled as the latest N', () => {
  it('50 of 312: Total POs 312, and the value and pending tiles name the 50 they sum', async () => {
    serve({ rows: 50, total: 312 }, { rows: 0, total: 0 });
    open(<PurchaseAnalyticsSection />);
    await screen.findByText('Total POs');
    expect(figureUnder('Total POs')).toBe('312');
    expect(screen.getByText('Value of the latest 50 orders')).toBeInTheDocument();
    // 50 x Rs 1,000 is under a lakh: whole rupees, not "Rs 0.5L" (review r3 #16).
    expect(figureUnder('Value of the latest 50 orders')).toBe('₹50,000');
    expect(screen.queryByText('Total Value')).toBeNull();
    expect(screen.getByText('Pending approval, latest 50')).toBeInTheDocument();
  });

  it('every order in hand: the tiles are totals and say so', async () => {
    serve({ rows: 3, total: 3 }, { rows: 0, total: 0 });
    open(<PurchaseAnalyticsSection />);
    await screen.findByText('Total POs');
    expect(figureUnder('Total POs')).toBe('3');
    expect(screen.getByText('Total Value')).toBeInTheDocument();
    expect(screen.getByText('Pending Approval')).toBeInTheDocument();
  });
});

describe('#18 Goods received: the GRN count is the real count, the list is the latest cut', () => {
  it('50 of 120 receipts on all stores: Total GRNs 120, "latest 50 shown", and the history says latest 50 of 120', async () => {
    serve({ rows: 0, total: 0 }, { rows: 50, total: 120 });
    open(<GoodsReceiptNote />);
    await waitFor(() => expect(figureUnder('Total GRNs')).toBe('120'));
    expect(screen.getByText('all stores · latest 50 shown')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /History/ }));
    expect((await screen.findByTestId('grn-list-cut')).textContent).toBe(
      'Latest 50 of 120 receipts, newest first.',
    );
  });

  it('every receipt in hand: the tile is the count and the caption is just the scope', async () => {
    serve({ rows: 0, total: 0 }, { rows: 2, total: 2 });
    open(<GoodsReceiptNote />);
    await waitFor(() => expect(figureUnder('Total GRNs')).toBe('2'));
    expect(screen.getByText('all stores')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /History/ }));
    await screen.findByText('GRN-1');
    expect(screen.queryByTestId('grn-list-cut')).toBeNull();
  });
});
