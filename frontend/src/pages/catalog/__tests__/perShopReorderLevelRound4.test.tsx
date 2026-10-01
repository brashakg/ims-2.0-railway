// ============================================================================
// Quick add / edit product - reorder level, review round 4 (F73, owner D12)
// ============================================================================
// 11. An invalid typed level is refused (toast, no save).
// 13. With no active shop no level is sent.
// 15. Malformed number text (validity.badInput) is invalid, never blank-clears.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const SHOP = 'BV-DHN-02';
const auth = vi.hoisted(() => ({ store: 'BV-DHN-02' as string | undefined }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Avinash', roles: ['ADMIN'], activeStoreId: auth.store, storeIds: ['BV-DHN-02'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => toast,
}));

const SOURCE_PRODUCT = {
  product_id: 'P-SRC',
  sku: 'SG-RAYB-4165-001',
  category: 'SG',
  brand: 'Ray-Ban',
  model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '001', lens_size: '54' },
  mrp: 7890,
  offer_price: 7890,
  hsn_code: '900410',
  gst_rate: 18,
  images: [],
  // The old chain-wide value (the form default) and this shop's own level.
  reorder_point: 5,
  reorder_levels: { 'BV-DHN-02': 2 },
};

// productApi: the calls the page makes today answer as in the sibling suite;
// anything else the fix adds is a spy that resolves {}.
const known: Record<string, (...a: unknown[]) => unknown> = {
  getCategoryRegistry: async () => { throw new Error('offline'); },
  getBrandOptions: async () => ({ brands: [] }),
  getProduct: async () => SOURCE_PRODUCT,
  uploadProductImage: async () => ({ url: '/api/v1/products/image/f1' }),
  createProduct: async () => ({ product_id: 'P-NEW', sku: 'SG-RAYB-4165-601' }),
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
vi.mock('../../../services/api/productTemplates', () => ({
  productTemplatesApi: { list: vi.fn(async () => ({ templates: [] })) },
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error { status = 0; },
  catalogProductsApi: {},
}));
vi.mock('../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));

import { QuickAddPage } from '../QuickAddPage';

const renderPage = (url = '/catalog/add') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <QuickAddPage />
    </MemoryRouter>,
  );
const fill = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });
/** The reorder input for THIS shop (an admin may also see other shops'). */
const shopReorderInput = () => {
  const all = screen.getAllByLabelText(/reorder level/i).filter((el) => el.tagName === 'INPUT') as HTMLInputElement[];
  return all.find((el) => /BV-DHN-02|dhanbad/i.test(`${el.id} ${el.title} ${el.getAttribute('aria-label') ?? ''}`)) ?? all[0];
};


const putsToLevels = () =>
  http.put.mock.calls.filter((c) => String((c as unknown[])[0]).includes('reorder-levels'));

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  for (const fn of [...Object.values(http), ...Object.values(productSpies), ...Object.values(toast)]) fn.mockClear();
  auth.store = SHOP;
});

const fillSunglass = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByText('Sunglass'));
  fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
  fill(screen.getByLabelText(/^Model No/), 'RB4165');
  fill(screen.getByLabelText(/^Colour Code/), '601');
  fill(screen.getByLabelText(/^MRP/), '7890');
};

describe('review round 4 - quick add reorder level', () => {
  it('11. an invalid typed level is refused: toast, nothing saved, no level PUT', async () => {
    const user = userEvent.setup();
    renderPage();
    await fillSunglass(user);
    fill(shopReorderInput(), '1e2');
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(productSpies.createProduct?.mock.calls.length ?? 0).toBe(0);
    expect(putsToLevels()).toEqual([]);
  });

  it('15. malformed number text (validity.badInput) is invalid, not a blank that clears the level', async () => {
    const user = userEvent.setup();
    renderPage();
    await fillSunglass(user);
    const input = shopReorderInput();
    fill(input, '3'); // typed something, then the text turns malformed
    Object.defineProperty(input, 'validity', { value: { badInput: true }, configurable: true });
    fill(input, '');
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(productSpies.createProduct?.mock.calls.length ?? 0).toBe(0);
  });

  it('13. with no active shop no level is sent (never store_id "")', async () => {
    auth.store = undefined;
    const user = userEvent.setup();
    renderPage('/catalog/add?edit=P-SRC');
    await waitFor(() => expect(productSpies.getProduct).toHaveBeenCalled());
    await user.click(await screen.findByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalled());
    expect(putsToLevels()).toEqual([]);
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it('R5-2. an edit that leaves the level untouched sends no level PUT', async () => {
    const user = userEvent.setup();
    renderPage('/catalog/add?edit=P-SRC');
    await waitFor(() => expect(shopReorderInput().value).toBe('2'));
    await user.click(await screen.findByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalled());
    expect(putsToLevels()).toEqual([]);
  });

  it('R5-2. a changed level in edit mode is sent', async () => {
    const user = userEvent.setup();
    renderPage('/catalog/add?edit=P-SRC');
    await waitFor(() => expect(shopReorderInput().value).toBe('2'));
    fill(shopReorderInput(), '6');
    await user.click(await screen.findByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(putsToLevels().length).toBe(1));
  });
});
