// ============================================================================
// Catalog edit flow (PR1, owner-approved UX review 2026-10-23)
// ============================================================================
// The catalogue manager saved 598 edits in 20 days, each time back to the
// list. Now: Save & next opens the next product of the SAME list (its stash,
// written by the Catalog page), Skip moves on without saving (unsaved changes
// ask first), "Item N of M"; the product is fetched once (the flags panel
// reads the same load); the SKU preview is asked only when a field that
// decides the SKU moves; a new product's plain Save lands on the Catalog.

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Mansi', roles: ['CATALOG_MANAGER'], activeStoreId: '', storeIds: [] },
    hasRole: (r: string | string[]) => (Array.isArray(r) ? r : [r]).includes('CATALOG_MANAGER'),
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const product = (id: string, extra: Record<string, unknown> = {}) => ({
  product_id: id,
  sku: `SG-RAYBAN-${id}`,
  category: 'SG',
  brand: 'Ray-Ban',
  model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: id, lens_size: '54' },
  mrp: 7890,
  offer_price: 7890,
  hsn_code: '900410',
  gst_rate: 18,
  images: [],
  ...extra,
});

const known: Record<string, (...a: unknown[]) => unknown> = {
  getCategoryRegistry: async () => { throw new Error('offline'); },
  getBrandOptions: async () => ({ brands: [] }),
  getProduct: async (id: unknown) => product(String(id)),
  getProducts: async () => ({ products: [], total_count: 0 }),
  updateProduct: async () => ({}),
  createProduct: async () => ({ product_id: 'P-NEW', sku: 'SG-RAYBAN-RB4165-601' }),
  previewSku: async () => ({
    category: 'SUNGLASS',
    sku: 'SG-RAYBAN',
    taken: false,
    identity_fields: ['brand_name', 'colour_code', 'lens_size', 'model_name', 'model_no', 'subbrand'],
  }),
  getWebsiteVerdict: async () => ({ online: true, reason: null }),
};
const productSpies = vi.hoisted(() => ({}) as Record<string, ReturnType<typeof vi.fn>>);
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: new Proxy({}, {
    get: (_t, key: string) => (productSpies[key] ??= vi.fn(known[key] ?? (async () => ({})))),
  }),
}));
const http = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: {} })),
  put: vi.fn(async () => ({ data: {} })),
  post: vi.fn(async () => ({ data: {} })),
  patch: vi.fn(async () => ({ data: {} })),
  delete: vi.fn(async () => ({ data: {} })),
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: http,
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error { status = 0; },
  catalogProductsApi: {},
  catalogBulkApi: { fixFlags: vi.fn() },
}));
vi.mock('../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));

import { QuickAddPage } from '../QuickAddPage';
import { EDIT_QUEUE_KEY, readReviewQueue, writeReviewQueue } from '../reviewQueue';

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.pathname + l.search}</output>;
}
const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/catalog/add" element={<><QuickAddPage /><Where /></>} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
const where = () => screen.getByTestId('where').textContent || '';
const LIST_PARAMS = { sort: 'name_asc', brand: 'Ray-Ban', is_active: 'true', group: 'model' };
const stashQueue = (ids: string[], total = ids.length) =>
  writeReviewQueue(
    {
      ids,
      index: 0,
      offset: 50,
      total,
      filters: { page: 2, address: 'sort=name_asc&brand=Ray-Ban&rows=50&page=2' },
      list: { path: '/catalog', params: LIST_PARAMS, pageSize: 50 },
    },
    EDIT_QUEUE_KEY,
  );
const loadedIds = () => productSpies.getProduct.mock.calls.map((c) => c[0]);
const sleep = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  for (const fn of [...Object.values(http), ...Object.values(productSpies), ...Object.values(toast)]) fn.mockClear();
  for (const k of Object.keys(known)) productSpies[k] ??= vi.fn();
  for (const [k, fn] of Object.entries(productSpies)) fn.mockImplementation(known[k] ?? (async () => ({})));
  window.sessionStorage.clear();
});

describe('Save & next from the Catalog list', () => {
  it('saves, then opens the next product of the same list; shows Item N of M', async () => {
    stashQueue(['P-A', 'P-B', 'P-C'], 120);
    renderAt('/catalog/add?edit=P-B&from=list');
    expect(await screen.findByTestId('edit-queue-pos')).toHaveTextContent('Item 52 of 120');
    fireEvent.click(screen.getByRole('button', { name: /Save & next/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    expect(productSpies.updateProduct.mock.calls[0][0]).toBe('P-B');
    await waitFor(() => expect(loadedIds()).toEqual(['P-B', 'P-C']));
    expect(await screen.findByText('Item 53 of 120')).toBeInTheDocument();
    expect(where()).toMatch(/^\/catalog\/add/);
  });

  it('Ctrl+Shift+Enter is Save & next; Ctrl+Enter stays plain Save', async () => {
    stashQueue(['P-A', 'P-B']);
    renderAt('/catalog/add?edit=P-A&from=list');
    await screen.findByTestId('edit-queue-pos');
    await sleep(50); // the page's key handler is (re)attached after paint
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true, shiftKey: true });
    await waitFor(() => expect(loadedIds()).toEqual(['P-A', 'P-B']));
    expect(productSpies.updateProduct).toHaveBeenCalledWith('P-A', expect.anything());
    await screen.findByText('Item 52 of 52');
    await sleep(50);
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(where()).toMatch(/^\/catalog\?/));
    expect(new URLSearchParams(where().split('?')[1]).get('focus')).toBe('P-B');
  });

  it('Skip opens the next product without saving', async () => {
    stashQueue(['P-A', 'P-B']);
    renderAt('/catalog/add?edit=P-A&from=list');
    await screen.findByTestId('edit-queue-pos');
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    await waitFor(() => expect(loadedIds()).toEqual(['P-A', 'P-B']));
    expect(productSpies.updateProduct).not.toHaveBeenCalled();
  });

  it('Skip with unsaved changes asks first; "Cancel" stays on the product', async () => {
    stashQueue(['P-A', 'P-B']);
    renderAt('/catalog/add?edit=P-A&from=list');
    await screen.findByTestId('edit-queue-pos');
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '9990' } });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    await sleep(50);
    expect(loadedIds()).toEqual(['P-A']);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    await waitFor(() => expect(loadedIds()).toEqual(['P-A', 'P-B']));
    expect(productSpies.updateProduct).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('Skip with nothing changed does not ask', async () => {
    stashQueue(['P-A', 'P-B']);
    renderAt('/catalog/add?edit=P-A&from=list');
    await screen.findByTestId('edit-queue-pos');
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    await waitFor(() => expect(loadedIds()).toEqual(['P-A', 'P-B']));
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('end of the stashed page: the next rows come from the list query (same filter + sort)', async () => {
    stashQueue(['P-A', 'P-B'], 120);
    productSpies.getProducts.mockResolvedValue({
      // The rows after those queued (one already queued is left out).
      products: [{ product_id: 'P-A' }, { product_id: 'P-D' }],
      total_count: 120,
    });
    renderAt('/catalog/add?edit=P-B&from=list');
    await screen.findByTestId('edit-queue-pos');
    fireEvent.click(screen.getByRole('button', { name: /Save & next/ }));
    await waitFor(() => expect(loadedIds()).toEqual(['P-B', 'P-D']));
    expect(productSpies.getProducts).toHaveBeenCalledWith({ ...LIST_PARAMS, skip: 52, limit: 50 });
  });

  it('last product: Save & next saves, says so, and returns to the list as it was', async () => {
    stashQueue(['P-A', 'P-B']);
    renderAt('/catalog/add?edit=P-B&from=list');
    await screen.findByTestId('edit-queue-pos');
    fireEvent.click(screen.getByRole('button', { name: /Save & next/ }));
    await waitFor(() => expect(where()).toMatch(/^\/catalog\?/));
    expect(productSpies.updateProduct).toHaveBeenCalledTimes(1);
    expect(toast.info).toHaveBeenCalledWith('That was the last product in this list.');
    const sp = new URLSearchParams(where().split('?')[1]);
    expect(sp.get('sort')).toBe('name_asc');
    expect(sp.get('page')).toBe('2');
    expect(sp.get('focus')).toBe('P-B');
  });

  it('opened from elsewhere (not in the list): plain Save only, no queue', async () => {
    stashQueue(['P-A', 'P-B']);
    renderAt('/catalog/add?edit=P-Z&from=list');
    await waitFor(() => expect(loadedIds()).toEqual(['P-Z']));
    await screen.findByRole('button', { name: /Save changes/ });
    expect(screen.queryByRole('button', { name: /Save & next/ })).toBeNull();
    expect(screen.queryByTestId('edit-queue-pos')).toBeNull();
  });
});

describe('Save & next: review fixes (PR #1267)', () => {
  const pageOf = (skip: number, size: number, total: number) =>
    Array.from({ length: Math.max(0, Math.min(size, total - skip)) }, (_, i) => ({ product_id: `P${skip + i + 1}` }));

  it('walks past several pages: each refill asks for the rows after those queued', async () => {
    writeReviewQueue(
      { ids: ['P1', 'P2'], index: 0, offset: 0, total: 7, filters: { address: 'sort=name_asc' },
        list: { path: '/catalog', params: LIST_PARAMS, pageSize: 2 } },
      EDIT_QUEUE_KEY,
    );
    productSpies.getProducts.mockImplementation(async (q: { skip: number; limit: number }) => ({
      products: pageOf(q.skip, q.limit, 7),
      total_count: 7,
    }));
    renderAt('/catalog/add?edit=P1&from=list');
    for (let i = 2; i <= 7; i++) {
      await screen.findByText(`Item ${i - 1} of 7`);
      await sleep(30);
      fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
      await waitFor(() => expect(loadedIds().at(-1)).toBe(`P${i}`));
    }
    await screen.findByText('Item 7 of 7');
    await sleep(30);
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('That was the last product in this list.'));
    expect(loadedIds()).toEqual(['P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7']);
    expect(productSpies.getProducts.mock.calls.map((c) => [c[0].skip, c[0].limit])).toEqual([
      [2, 2], [4, 2], [6, 2], [7, 2],
    ]);
  });

  it('a next product that will not load is dropped and the walk moves on', async () => {
    stashQueue(['P-A', 'P-B', 'P-C']);
    productSpies.getProduct.mockImplementation(async (id: unknown) => {
      if (id === 'P-B') throw new Error('404');
      return product(String(id));
    });
    renderAt('/catalog/add?edit=P-A&from=list');
    await screen.findByTestId('edit-queue-pos');
    await sleep(30);
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    await waitFor(() => expect(loadedIds()).toEqual(['P-A', 'P-B', 'P-C']));
    await screen.findByText(/Updated|Item 52 of 52/);
    expect(readReviewQueue(EDIT_QUEUE_KEY)?.ids).toEqual(['P-A', 'P-C']);
    // The form is P-C's, and Save writes P-C.
    await sleep(30);
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    expect(productSpies.updateProduct.mock.calls[0][0]).toBe('P-C');
  });

  it('from Save & next until the next product is in the form, nothing can be saved', async () => {
    stashQueue(['P-A', 'P-B']);
    let release: (v: unknown) => void = () => {};
    productSpies.getProduct.mockImplementation((id: unknown) =>
      id === 'P-B' ? new Promise((r) => { release = r; }) : Promise.resolve(product(String(id))),
    );
    renderAt('/catalog/add?edit=P-A&from=list');
    await screen.findByTestId('edit-queue-pos');
    await sleep(30);
    fireEvent.click(screen.getByRole('button', { name: /Save & next/ }));
    await waitFor(() => expect(loadedIds()).toEqual(['P-A', 'P-B']));
    await sleep(30);
    expect(screen.getByRole('button', { name: /Save changes/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save & next/ })).toBeDisabled();
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });
    await sleep(30);
    expect(productSpies.updateProduct).toHaveBeenCalledTimes(1); // only P-A's own save
    await act(async () => { release(product('P-B')); });
    await screen.findByText('Item 52 of 52');
    await waitFor(() => expect(screen.getByRole('button', { name: /Save changes/ })).toBeEnabled());
  });

  it('opened from anywhere but the list: the old list is dropped, plain Save only', async () => {
    stashQueue(['P-A', 'P-B']);
    renderAt('/catalog/add?edit=P-A');
    await screen.findByRole('button', { name: /Save changes/ });
    await sleep(30);
    expect(screen.queryByRole('button', { name: /Save & next/ })).toBeNull();
    expect(readReviewQueue(EDIT_QUEUE_KEY)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(where()).toBe('/catalog?focus=P-A'));
  });
});

describe('the edit page loads the product once', () => {
  it('one GET for the form and the flags panel', async () => {
    productSpies.getProduct.mockImplementation(async (id: unknown) =>
      product(String(id), {
        flags: [{ flag_id: 'f1', type: 'photo_redo', status: 'open', note: 'Front photo is 3rd' }],
      }),
    );
    renderAt('/catalog/add?edit=P-A');
    expect(await screen.findByTestId('product-flags-panel')).toHaveTextContent('Front photo is 3rd');
    await sleep(50);
    expect(productSpies.getProduct).toHaveBeenCalledTimes(1);
  });
});

describe('SKU preview: asked only when the SKU can change', () => {
  it('a field that decides the SKU asks once after the pause; any other field does not', async () => {
    const user = userEvent.setup();
    renderAt('/catalog/add');
    await user.click(screen.getByText('Sunglasses'));
    fireEvent.change(screen.getByLabelText(/^Brand Name/), { target: { value: 'Ray' } });
    fireEvent.change(screen.getByLabelText(/^Brand Name/), { target: { value: 'Ray-Ban' } });
    await waitFor(() => expect(productSpies.previewSku).toHaveBeenCalledTimes(1));
    // Typing pauses: one call, with what was typed last.
    expect(productSpies.previewSku.mock.calls[0][1]).toMatchObject({ brand_name: 'Ray-Ban' });

    fireEvent.change(screen.getByLabelText(/^Product USP 1/), { target: { value: 'Light' } });
    await sleep(600);
    expect(productSpies.previewSku).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText(/^Model No/), { target: { value: 'RB4165' } });
    await sleep(200);
    expect(productSpies.previewSku).toHaveBeenCalledTimes(1); // still within the pause
    await waitFor(() => expect(productSpies.previewSku).toHaveBeenCalledTimes(2));
  });
});

describe('a new product: plain Save lands on the Catalog', () => {
  it('opens the Catalog with the new product in view (not Inventory)', async () => {
    const user = userEvent.setup();
    renderAt('/catalog/add');
    await user.click(screen.getByText('Sunglasses'));
    fireEvent.change(screen.getByLabelText(/^Brand Name/), { target: { value: 'Ray-Ban' } });
    fireEvent.change(screen.getByLabelText(/^Model No/), { target: { value: 'RB4165' } });
    fireEvent.change(screen.getByLabelText(/^Colour Code/), { target: { value: '601' } });
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '7890' } });
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(where()).toBe('/catalog?focus=P-NEW'));
  });

  it('Save + New stays on the form', async () => {
    const user = userEvent.setup();
    renderAt('/catalog/add');
    await user.click(screen.getByText('Sunglasses'));
    fireEvent.change(screen.getByLabelText(/^Brand Name/), { target: { value: 'Ray-Ban' } });
    fireEvent.change(screen.getByLabelText(/^Model No/), { target: { value: 'RB4165' } });
    fireEvent.change(screen.getByLabelText(/^Colour Code/), { target: { value: '601' } });
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '7890' } });
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));
    await waitFor(() => expect(productSpies.createProduct).toHaveBeenCalledTimes(1));
    await sleep(50);
    expect(where()).toBe('/catalog/add');
  });
});
