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
  runs_out_days: 0, band: 'out-of-stock', transfer_from: [], transfer_qty: 0, transfer_instead: false,
  vendor_source: 'preferred',
};
const LIST = {
  store_id: null,
  auto_reorder_off: 12,
  rows: [
    { ...ROW, product_id: 'P-LVL', name: 'Ray-Ban RB3025', level: 3, reorder_quantity: 3, basis: 'level', sold_30d: 7,
      on_order: 1, in_draft: 2, buy: 3, unit_cost: 4000, est_value: 12000 },
    { ...ROW, product_id: 'P-SALES', name: 'Vogue VO5286', basis: 'sales', sold_30d: 5, on_hand: 1,
      runs_out_days: 6, band: 'low', buy: 4, unit_cost: 1000, est_value: 4000 },
    { ...ROW, product_id: 'P-SAF', name: 'Carrera 1234', vendor_id: 'V-SAF', vendor_name: 'Safilo',
      vendor_source: 'last order', basis: 'sales', sold_30d: 2, buy: 2, unit_cost: null, est_value: null },
    { ...ROW, product_id: 'P-MOVE', name: 'Oakley Radar', basis: 'sales', sold_30d: 2, buy: 2,
      transfer_from: [{ store_id: 'BV-PUN-01', store_name: 'Pune', qty: 2 }], transfer_qty: 2, transfer_instead: true },
    { ...ROW, product_id: 'P-NOV', name: 'Carrera 2045T', sku: 'CA-2045', vendor_id: null, vendor_name: '',
      vendor_source: null, basis: 'sales', sold_30d: 3, buy: 3 },
    // Drafted last visit: 3 in draft cover the buy of 3 -> starts unticked.
    { ...ROW, product_id: 'P-DRAFTED', name: 'Prada PR17', basis: 'sales', sold_30d: 3, in_draft: 3, buy: 3 },
    // Tracked and healthy (board: Carrera 1234, 5 weeks): shown, nothing to buy.
    { ...ROW, product_id: 'P-OK', name: 'Carrera 5050', level: 2, basis: 'level', sold_30d: 4, on_hand: 5,
      runs_out_days: 37, band: 'healthy', buy: 0 },
  ],
  skipped: [
    { ...ROW, product_id: 'P-HSN', name: 'Carrera 8862', sku: 'CA-8862', reason: 'catalogue incomplete', missing: ['hsn_code'] },
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
    data: url.includes('reorder-list')
      ? LIST
      : url.includes('stores')
        ? { stores: [] }
        : url === '/vendors'
          ? { vendors: [{ vendor_id: 'V-LUX', trade_name: 'Luxottica' }, { vendor_id: 'V-SAF', trade_name: 'Safilo' }] }
          : {},
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

  it("another shop's shared-out spares suggest a transfer, and that row starts unticked", async () => {
    renderPage();
    const move = await rowOf('Oakley Radar');
    expect(within(move).getByText(/Pune can send 2 - transfer instead of buying/)).toBeInTheDocument();
    expect(within(move).getByRole('checkbox')).not.toBeChecked();
    expect(within(await rowOf('Vogue VO5286')).getByRole('checkbox')).toBeChecked();
  });

  it('a row already covered by drafts starts unticked but stays editable (review NIT)', async () => {
    renderPage();
    const drafted = await rowOf('Prada PR17');
    expect(within(drafted).getByRole('checkbox')).not.toBeChecked();
    expect(within(drafted).getByRole('spinbutton')).not.toBeDisabled();
  });

  it('a healthy tracked line shows under All with nothing to buy, unticked (board)', async () => {
    renderPage();
    const ok = await rowOf('Carrera 5050');
    expect(within(ok).getByRole('checkbox')).not.toBeChecked();
    expect(within(ok).getByRole('spinbutton')).toHaveValue(0);
    expect(screen.getByRole('button', { name: /^All/ })).toHaveTextContent('7');
  });

  it("says where the vendor came from when it is the last order's", async () => {
    renderPage();
    expect(within(await rowOf('Carrera 1234')).getByText('(last order)')).toBeInTheDocument();
  });

  it('counts the products with auto-reorder off in the header', async () => {
    renderPage();
    expect(await screen.findByText('12 products have auto-reorder off')).toBeInTheDocument();
  });

  it('a list the server could not read says so and offers nothing to buy (503)', async () => {
    http.get.mockImplementation(async (url: string) => {
      if (url.includes('reorder-list')) {
        throw Object.assign(new Error('Request failed with status code 503'), {
          isAxiosError: true,
          response: { status: 503, data: { detail: 'Could not load the reorder list' } },
        });
      }
      return { data: url.includes('stores') ? { stores: [] } : {} };
    });
    renderPage();
    expect(await screen.findByText(/Could not load the reorder list/)).toBeInTheDocument();
    expect(screen.queryByRole('spinbutton')).toBeNull();
    expect(screen.getByRole('button', { name: 'Make draft orders' })).toBeDisabled();
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

  it('says why a product is not suggested and opens that product in the catalogue', async () => {
    renderPage();
    expect(await screen.findByText(/catalogue incomplete: can't purchase · Missing: HSN/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Fix in Catalogue' })).toHaveAttribute('href', '/catalog?focus=P-HSN');
  });

  it('a role that cannot edit products is told to ask the catalogue manager, not given a link', async () => {
    auth.roles = ['STORE_MANAGER'];
    renderPage();
    expect(await screen.findByText(/catalogue incomplete/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Fix in Catalogue' })).toBeNull();
    expect(screen.getByText('ask the catalogue manager')).toBeInTheDocument();
  });
});

describe('value at cost is for the cost roles only', () => {
  it('an admin sees the value column; a store manager does not', async () => {
    renderPage();
    expect((await screen.findAllByRole('columnheader', { name: 'Est. value at cost' })).length).toBeGreaterThan(0);
    expect(within(await rowOf('Ray-Ban RB3025')).getByText('₹ 12,000')).toBeInTheDocument();
    expect(within(await rowOf('Carrera 1234')).getByText('no cost')).toBeInTheDocument();
  });

  it('store manager', async () => {
    auth.roles = ['STORE_MANAGER'];
    renderPage();
    await rowOf('Ray-Ban RB3025');
    expect(screen.queryAllByRole('columnheader', { name: 'Est. value at cost' })).toHaveLength(0);
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
    // the row with no vendor is not drafted until one is chosen
    expect(JSON.stringify(sent)).not.toMatch(/P-NOV/);
  });

  it('a row with no vendor sits under "Choose vendor"; the vendor picked there drafts it (review BLOCK)', async () => {
    renderPage();
    const group = (await screen.findByText('Choose vendor')).closest('section') as HTMLElement;
    expect(within(group).getByText('Carrera 2045T')).toBeInTheDocument();
    const picker = within(group).getByLabelText('Vendor for Carrera 2045T at Dhanbad');
    await within(group).findByRole('option', { name: 'Safilo' });
    fireEvent.change(picker, { target: { value: 'V-SAF' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make draft orders' }));
    await screen.findByText('Purchase orders page');
    const saf = posts().find((p) => p.vendor_id === 'V-SAF')!;
    expect((saf.items as Array<{ product_id: string }>).map((i) => i.product_id)).toEqual(['P-SAF', 'P-NOV']);
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

  it('an untouched save writes nothing: no level, no max stock, no lead time (round 4 #14, review)', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Reorder settings for Ray-Ban RB3025 at Dhanbad' }));
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(http.put).not.toHaveBeenCalled();
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

  it('a catalogue manager saves only the product-wide field they changed, never the shop level', async () => {
    auth.roles = ['CATALOG_MANAGER'];
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Reorder settings for Ray-Ban RB3025 at Dhanbad' }));
    fireEvent.change(await screen.findByLabelText('Order in packs of'), { target: { value: '6' } });
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(putUrls()).toContain('/products/P-LVL'));
    expect((http.put.mock.calls[0] as unknown[])[1]).toEqual({ reorder_quantity: 6 });
    expect(putUrls().some((u) => u.includes('reorder-levels'))).toBe(false);
  });
});
