// ============================================================================
// IMS 2.0 - Add Product's save asks about the same model written differently
// ============================================================================
// Owner 2026-11-01: the SERVER decides (409 NEAR_DUPLICATE) and only this form
// asks it (check_near_duplicates). "It's a different product" (reason
// required) sends the same save again with distinct_from; the typed form is
// never left behind.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Priya', roles: ['CATALOG_MANAGER'], activeStoreId: 'S1' },
    hasRole: () => true,
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const REGISTRY = {
  categories: [
    {
      code: 'SUNGLASS',
      sku_prefix: 'SG',
      name: 'Sunglass',
      required_fields: ['brand_name', 'model_no', 'colour_code'],
      optional_fields: ['model_name', 'lens_size'],
      fields: [],
      forced_discount_category: null,
      has_families: true,
      model_fields: ['brand_name', 'model_no', 'model_name'],
    },
  ],
};

const TWIN = {
  product_id: 'P-OLD',
  sku: 'SG-RAYBAN-RB4340-601/M3',
  brand: 'Ray-Ban',
  model: 'RB4340',
  colour_code: '601/M3',
  created_by_name: 'admin',
};

const api = vi.hoisted(() => {
  class DuplicateProductError extends Error {
    existing: Record<string, unknown>;
    code: string;
    constructor(message: string, existing: Record<string, unknown>, code = 'DUPLICATE_PRODUCT') {
      super(message);
      this.existing = existing;
      this.code = code;
    }
  }
  return { DuplicateProductError, createProduct: vi.fn() };
});

vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: api.DuplicateProductError,
  productApi: {
    getCategoryRegistry: vi.fn(async () => REGISTRY),
    getBrandOptions: vi.fn(async () => ({ brands: [{ name: 'Ray-Ban', subbrands: [], tier: 'PREMIUM' }] })),
    getProduct: vi.fn(async () => ({})),
    createProduct: (...a: unknown[]) => api.createProduct(...a),
    updateProduct: vi.fn(async () => ({})),
    previewSku: vi.fn(async () => ({ category: 'SUNGLASS', sku: 'SG-RAYBAN-4340-601/M3' })),
    getWebsiteVerdict: vi.fn(async (brand: string) => ({ brand, online: true, reason: null })),
  },
}));
vi.mock('../../../services/api/inventory', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/inventory')>()),
  reorderApi: { setShopLevel: vi.fn(async () => ({})) },
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error { status = 0; },
  catalogProductsApi: { get: vi.fn(), list: vi.fn(async () => ({ products: [] })), promoteDryRun: vi.fn() },
}));
vi.mock('../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));
vi.mock('../useSimilarProducts', () => ({ useSimilarProducts: vi.fn() }));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));

import { QuickAddPage } from '../QuickAddPage';
import { loadCategoryRegistry } from '../../../domain/catalog/productAdd';

const fill = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  api.createProduct.mockReset();
});

describe('Save: the same model written differently', () => {
  it('asks "Is this the same product?", then saves again with the reason', async () => {
    api.createProduct
      .mockRejectedValueOnce(new api.DuplicateProductError('same?', TWIN, 'NEAR_DUPLICATE'))
      .mockResolvedValueOnce({ product_id: 'P-NEW', sku: 'SG-RAYBAN-4340-601/M3' });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/catalog/add']}>
        <QuickAddPage />
      </MemoryRouter>,
    );
    await loadCategoryRegistry();
    await user.click(await screen.findByText('Sunglasses'));
    fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
    fill(screen.getByLabelText(/^Model No/), '4340');
    fill(screen.getByLabelText(/^Colour Code/), '601/M3');
    fill(screen.getByLabelText(/^MRP/), '8100');
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));

    await screen.findByRole('heading', { name: 'Is this the same product?' });
    expect(api.createProduct.mock.calls[0][0]).toMatchObject({ check_near_duplicates: true });
    expect(api.createProduct.mock.calls[0][0]).not.toHaveProperty('distinct_from');
    // The typed form is still there behind the popup.
    expect(screen.getByLabelText(/^Model No/)).toHaveValue('4340');

    await user.click(screen.getByRole('button', { name: "It's a different product" }));
    await user.click(screen.getByRole('button', { name: 'Different line or series' }));
    await user.click(screen.getByRole('button', { name: 'Save as a different product' }));

    await waitFor(() => expect(api.createProduct).toHaveBeenCalledTimes(2));
    expect(api.createProduct.mock.calls[1][0]).toMatchObject({
      check_near_duplicates: true,
      distinct_from: [{ product_id: 'P-OLD', reason: 'Different line or series' }],
    });
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Is this the same product?' })).toBeNull());
  });
});
