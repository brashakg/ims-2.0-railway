// ============================================================================
// S5 - ONE Reorder list (/inventory/reorders), owner 2026-10-22/23
// ============================================================================
// Buy Desk, Replenishment and Low stock became one list. The screen renders
// the server's numbers (THE buy rule) and decides none of them; "Make draft
// orders" makes one DRAFT per vendor and shop through the one PO door, with
// the quantities as edited and no price of its own (the server prices lines).
// The network is the one seam: the axios client is a spy.

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const auth = vi.hoisted(() => ({ roles: ['ADMIN'] as string[] }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'U', activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'], roles: auth.roles },
    hasRole: (roles: string[]) => roles.some((r) => auth.roles.includes(r)),
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const ROW = {
  sku: '', brand: 'Ray-Ban', vendor_id: 'V-LUX', vendor_name: 'Luxottica',
  store_id: 'BV-DHN-02', store_name: 'Dhanbad', level: null, reorder_quantity: null,
  max_stock: null, lead_time_days: null, sold_30d: 0, on_hand: 0, on_order: 0, in_draft: 0,
  runs_out_days: 0, band: 'out-of-stock', spares: [], transfer_instead: false,
};
const LIST = {
  store_id: null,
  rows: [
    { ...ROW, product_id: 'P-LVL', name: 'Ray-Ban RB3025', level: 3, reorder_quantity: 3, basis: 'level', sold_30d: 7,
      on_order: 1, in_draft: 2, buy: 3, unit_cost: 4000, est_value: 12000 },
    { ...ROW, product_id: 'P-SALES', name: 'Vogue VO5286', basis: 'sales', sold_30d: 5, on_hand: 1,
      runs_out_days: 6, band: 'low', buy: 4, unit_cost: 1000, est_value: 4000 },
    { ...ROW, product_id: 'P-SAF', name: 'Carrera 1234', vendor_id: 'V-SAF', vendor_name: 'Safilo',
      basis: 'sales', sold_30d: 2, buy: 2, unit_cost: null, est_value: null },
    { ...ROW, product_id: 'P-MOVE', name: 'Oakley Radar', basis: 'sales', sold_30d: 2, buy: 2,
      spares: [{ store_id: 'BV-PUN-01', store_name: 'Pune', spare: 6 }], transfer_instead: true },
  ],
  skipped: [
    { ...ROW, product_id: 'P-HSN', name: 'Carrera 8862', sku: 'CA-8862', reason: 'catalogue incomplete', missing: ['hsn_code'] },
    { ...ROW, product_id: 'P-NOV', name: 'Carrera 2045T', sku: 'CA-2045', reason: 'no vendor set' },
  ],
};

const http = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(async () => ({ data: {} })),
  post: vi.fn(async () => ({ data: { po_number: 'PO-1' } })),
  patch: vi.fn(async () => ({ data: {} })),
  delete: vi.fn(async () => ({ data: {} })),
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: http,
}));

import { ReorderListPage } from '../ReorderListPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/inventory/reorders']}>
        <Routes>
          <Route path="/inventory/reorders" element={<ReorderListPage />} />
          <Route path="/purchase/orders" element={<p>Purchase orders page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const rowOf = async (name: string) => (await screen.findByText(name)).closest('tr') as HTMLElement;
const posts = () => http.post.mock.calls.map((c) => (c as unknown[])[1] as Record<string, unknown>);
const putUrls = () => http.put.mock.calls.map((c) => String((c as unknown[])[0]));

beforeEach(() => {
  auth.roles = ['ADMIN'];
  for (const fn of Object.values(http)) fn.mockClear();
  Object.values(toast).forEach((f) => f.mockClear());
  http.get.mockImplementation(async (url: string) => ({
    data: url.includes('reorder-list') ? LIST : url.includes('stores') ? { stores: [] } : {},
  }));
  http.post.mockImplementation(async () => ({ data: { po_number: 'PO-1' } }));
});

describe('the one Reorder list shows the server numbers', () => {
  it('both bases, sent orders netted, drafts beside, and the server buy in the box', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: 'What to buy this week' })).toBeInTheDocument();
    const lvl = await rowOf('Ray-Ban RB3025');
    expect(within(lvl).getByText('level')).toBeInTheDocument();
    expect(within(lvl).getByText('+2 in draft')).toBeInTheDocument();
    expect(within(lvl).getByText('Critical')).toBeInTheDocument();
    expect(within(lvl).getByText('out now')).toBeInTheDocument();
    expect(within(lvl).getByRole('spinbutton')).toHaveValue(3);
    const sales = await rowOf('Vogue VO5286');
    expect(within(sales).getByText('30-day sales')).toBeInTheDocument();
    expect(within(sales).getByText('not set')).toBeInTheDocument();
    expect(within(sales).getByText('6 days')).toBeInTheDocument();
    expect(within(sales).getByRole('spinbutton')).toHaveValue(4);
  });

  it("another shop's spares suggest a transfer, and that row starts unticked", async () => {
    renderPage();
    const move = await rowOf('Oakley Radar');
    expect(within(move).getByText(/Pune has 6 spare - transfer instead of buying/)).toBeInTheDocument();
    expect(within(move).getByRole('checkbox')).not.toBeChecked();
    expect(within(await rowOf('Vogue VO5286')).getByRole('checkbox')).toBeChecked();
  });

  it('the Critical and Low filters and the vendor filter narrow the list', async () => {
    renderPage();
    await rowOf('Vogue VO5286');
    fireEvent.click(screen.getByRole('button', { name: /^Low/ }));
    expect(screen.queryByText('Ray-Ban RB3025')).toBeNull();
    expect(screen.getByText('Vogue VO5286')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^All/ }));
    fireEvent.change(screen.getByLabelText('Vendor'), { target: { value: 'V-SAF' } });
    expect(screen.getByText('Carrera 1234')).toBeInTheDocument();
    expect(screen.queryByText('Vogue VO5286')).toBeNull();
  });

  it('says why a product is not suggested and where to fix it', async () => {
    renderPage();
    expect(await screen.findByText(/catalogue incomplete: can't purchase · Missing: HSN/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Fix in Catalogue' })).toHaveAttribute('href', '/catalog?search=CA-8862');
    expect(screen.getByRole('link', { name: 'Set vendor' })).toHaveAttribute('href', '/catalog?search=CA-2045');
  });
});

describe('value at cost is for the cost roles only', () => {
  it('an admin sees the value column; a store manager does not', async () => {
    renderPage();
    expect(await screen.findByRole('columnheader', { name: 'Est. value at cost' })).toBeInTheDocument();
    expect(within(await rowOf('Ray-Ban RB3025')).getByText('₹ 12,000')).toBeInTheDocument();
    expect(within(await rowOf('Carrera 1234')).getByText('no cost')).toBeInTheDocument();
  });

  it('store manager', async () => {
    auth.roles = ['STORE_MANAGER'];
    renderPage();
    await rowOf('Ray-Ban RB3025');
    expect(screen.queryByRole('columnheader', { name: 'Est. value at cost' })).toBeNull();
    expect(screen.queryByText(/₹/)).toBeNull();
  });
});

describe('Make draft orders', () => {
  it('one draft per vendor and shop, the edited quantities, ticked rows only, no price sent', async () => {
    renderPage();
    const sales = await rowOf('Vogue VO5286');
    fireEvent.change(within(sales).getByRole('spinbutton'), { target: { value: '6' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make draft orders' }));
    await screen.findByText('Purchase orders page');
    const sent = posts();
    expect(sent).toHaveLength(2);
    const lux = sent.find((p) => p.vendor_id === 'V-LUX')!;
    expect(lux.delivery_store_id).toBe('BV-DHN-02');
    expect(lux.items).toEqual([
      { product_id: 'P-LVL', product_name: 'Ray-Ban RB3025', sku: '', quantity: 3 },
      { product_id: 'P-SALES', product_name: 'Vogue VO5286', sku: '', quantity: 6 },
    ]); // P-MOVE (transfer instead) is unticked
    expect(sent.find((p) => p.vendor_id === 'V-SAF')!.items).toHaveLength(1);
    expect(JSON.stringify(sent)).not.toMatch(/unit_price/);
  });

  it('after a failure, pressing again never makes a second draft for a vendor already done', async () => {
    http.post
      .mockImplementationOnce(async () => ({ data: { po_number: 'PO-1' } }))
      .mockImplementationOnce(async () => {
        throw new Error('Vendor not found');
      });
    renderPage();
    await rowOf('Vogue VO5286');
    fireEvent.click(screen.getByRole('button', { name: 'Make draft orders' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    const firstVendor = posts()[0].vendor_id;
    http.post.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Make draft orders' }));
    await screen.findByText('Purchase orders page');
    expect(posts().map((p) => p.vendor_id)).toEqual([firstVendor === 'V-LUX' ? 'V-SAF' : 'V-LUX']);
  });

  it('a catalogue manager reads the list but makes no draft (owner 2026-10-08)', async () => {
    auth.roles = ['CATALOG_MANAGER'];
    renderPage();
    await rowOf('Vogue VO5286');
    expect(screen.queryByRole('button', { name: 'Make draft orders' })).toBeNull();
  });
});

describe('reorder settings save, by role (ported from the Reorder dashboard)', () => {
  it("a store manager saves the ROW's shop level; the product-wide fields are not theirs", async () => {
    auth.roles = ['STORE_MANAGER'];
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Reorder settings for Ray-Ban RB3025 at Dhanbad' }));
    fireEvent.change(await screen.findByPlaceholderText('not set'), { target: { value: '5' } });
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(putUrls()).toContain('/inventory/reorder-levels/P-LVL'));
    const body = (http.put.mock.calls[0] as unknown[])[1];
    expect(body).toEqual({ store_id: 'BV-DHN-02', level: 5 });
    expect(putUrls()).not.toContain('/products/P-LVL');
  });

  it('an untouched level is never re-sent (round 4 #14)', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Reorder settings for Ray-Ban RB3025 at Dhanbad' }));
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(putUrls()).toContain('/products/P-LVL'));
    expect(putUrls().some((u) => u.includes('reorder-levels'))).toBe(false);
  });

  it('the level field is locked for a catalogue manager, open for an admin (round 5 #5)', async () => {
    auth.roles = ['CATALOG_MANAGER'];
    const view = renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Reorder settings for Ray-Ban RB3025 at Dhanbad' }));
    expect(await screen.findByPlaceholderText('not set')).toBeDisabled();
    view.unmount();
    auth.roles = ['ADMIN'];
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Reorder settings for Ray-Ban RB3025 at Dhanbad' }));
    expect(await screen.findByPlaceholderText('not set')).not.toBeDisabled();
  });

  it('a catalogue manager saves the product-wide fields and never the shop level', async () => {
    auth.roles = ['CATALOG_MANAGER'];
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Reorder settings for Ray-Ban RB3025 at Dhanbad' }));
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(putUrls()).toContain('/products/P-LVL'));
    expect(putUrls().some((u) => u.includes('reorder-levels'))).toBe(false);
  });
});
