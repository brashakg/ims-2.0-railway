// ============================================================================
// Finishing an ordered draft in the editor says what went on the shelf
// (audit C1)
// ============================================================================
// PUT /products/{id} releases the units receipts were holding for the item and
// answers released_units. The save's toast says so; a save that released
// nothing says nothing about stock.

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Cat', roles: ['CATALOG_MANAGER'], activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'] },
    hasRole: () => true,
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const DRAFT = {
  product_id: 'P-DRAFT',
  sku: 'SG-RAYB-4165-601',
  category: 'SG',
  brand: 'Ray-Ban',
  model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '601', lens_size: '54' },
  mrp: 7890,
  offer_price: 7490,
  hsn_code: '900410',
  gst_rate: 18,
  images: [],
  provisional: true,
  is_active: false,
  catalog_status: 'DRAFT',
};

const update = vi.hoisted(() => vi.fn());
const known: Record<string, (...a: unknown[]) => unknown> = {
  getCategoryRegistry: async () => {
    throw new Error('offline');
  },
  getBrandOptions: async () => ({ brands: [] }),
  getProduct: async () => DRAFT,
};
const spies = vi.hoisted(() => ({}) as Record<string, ReturnType<typeof vi.fn>>);
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: new Proxy(
    {},
    {
      get: (_t, key: string) =>
        key === 'updateProduct' ? update : (spies[key] ??= vi.fn(known[key] ?? (async () => ({})))),
    },
  ),
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
  CatalogRequestError: class CatalogRequestError extends Error {
    status = 0;
  },
  catalogProductsApi: {},
}));
vi.mock('../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));

import { QuickAddPage } from '../QuickAddPage';

const renderEditor = () =>
  render(
    <MemoryRouter initialEntries={['/catalog/add?edit=P-DRAFT']}>
      <QuickAddPage />
    </MemoryRouter>,
  );

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  for (const fn of Object.values(toast)) fn.mockClear();
  update.mockReset();
});

async function save() {
  const user = userEvent.setup();
  renderEditor();
  await user.click(await screen.findByRole('button', { name: /Save changes/ }));
  await waitFor(() => expect(update).toHaveBeenCalled());
}

describe('finishing an ordered draft', () => {
  it('the toast says how many held units went on the shelf', async () => {
    update.mockResolvedValue({ product_id: 'P-DRAFT', released_units: 2 });
    await save();
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/2 held unit\(s\) are now on the shelf/)),
    );
  });

  it('a save that released nothing says nothing about stock', async () => {
    update.mockResolvedValue({ product_id: 'P-DRAFT', released_units: 0 });
    await save();
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(String(toast.success.mock.calls[0][0])).not.toMatch(/on the shelf/);
  });
});

