// ============================================================================
// Add / edit product - the reorder level is THIS shop's (audit F73, owner D12)
// ============================================================================
// The owner (2026-09-29): reorder points are PER SHOP. The form today saves
// one chain-wide `reorder_point` (useQuickAddForm: a follow-up
// updateProduct(newId, { reorder_point }) after create, the same key inside
// the edit PUT), and edit mode pre-fills that chain value -- so a level typed
// for Dhanbad lands on Bokaro too, and a product with no level at this shop
// shows somebody else's number.
//
// it.fails until the fix lands (vitest's strict xfail). The network is the
// seam: productApi is a spy for whatever the fix calls, and the axios client is
// a spy, so the tests ask only WHAT was written and shown.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const SHOP = 'BV-DHN-02';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Avinash', roles: ['ADMIN'], activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
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

type Call = { url: string; body: unknown };
const allWrites = (): Call[] => [
  ...(['put', 'post', 'patch'] as const).flatMap((m) =>
    http[m].mock.calls.map((c) => ({ url: String(c[0]), body: c[1] as unknown }))),
  ...Object.entries(productSpies)
    .filter(([k]) => !k.startsWith('get') && k !== 'previewSku')
    .flatMap(([k, fn]) => fn.mock.calls.map((c) => ({ url: `productApi.${k}`, body: c }))),
];
const shopWrite = (level: number) =>
  allWrites().find((w) => {
    const s = JSON.stringify(w);
    return s.includes(SHOP) && new RegExp(`[:,[]${level}(?![0-9.])`).test(s);
  });
const chainWrite = () => allWrites().find((w) => JSON.stringify(w.body ?? {}).includes('reorder_point'));

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  for (const fn of [...Object.values(http), ...Object.values(productSpies)]) fn.mockClear();
});

describe('F73/D12 - add product', () => {
  it.fails('a typed level is saved for this shop only, never as one chain-wide reorder_point', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText('Sunglass'));
    fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
    fill(screen.getByLabelText(/^Model No/), 'RB4165');
    fill(screen.getByLabelText(/^Colour Code/), '601');
    fill(screen.getByLabelText(/^MRP/), '7890');
    fill(shopReorderInput(), '2');
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(productSpies.createProduct).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(shopWrite(2)).toBeTruthy());
    expect(chainWrite()).toBeUndefined();
  });
});

describe('F73/D12 - edit product', () => {
  it.fails("shows THIS shop's level, never the chain-wide value", async () => {
    renderPage('/catalog/add?edit=P-SRC');
    await waitFor(() => expect(productSpies.getProduct).toHaveBeenCalled());
    await waitFor(() => expect(shopReorderInput().value).toBe('2'));
    const shown = screen.getAllByLabelText(/reorder level/i).map((el) => (el as HTMLInputElement).value);
    expect(shown).not.toContain('5');
  });

  it.fails("saving an edit writes this shop's level, never reorder_point inside the product PUT", async () => {
    const user = userEvent.setup();
    renderPage('/catalog/add?edit=P-SRC');
    await waitFor(() => expect(productSpies.getProduct).toHaveBeenCalled());
    await screen.findByRole('button', { name: /Save changes/ });
    fill(shopReorderInput(), '3');
    await user.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalled());
    await waitFor(() => expect(shopWrite(3)).toBeTruthy());
    expect(chainWrite()).toBeUndefined();
  });
});
