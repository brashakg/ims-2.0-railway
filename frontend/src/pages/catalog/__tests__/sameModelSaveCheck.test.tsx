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
import { MemoryRouter, useNavigate } from 'react-router-dom';

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
  return { DuplicateProductError, createProduct: vi.fn(), updateProduct: vi.fn(), getProduct: vi.fn() };
});

vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: api.DuplicateProductError,
  productApi: {
    getCategoryRegistry: vi.fn(async () => REGISTRY),
    getBrandOptions: vi.fn(async () => ({ brands: [{ name: 'Ray-Ban', subbrands: [], tier: 'PREMIUM' }] })),
    getProduct: (...a: unknown[]) => api.getProduct(...a),
    createProduct: (...a: unknown[]) => api.createProduct(...a),
    updateProduct: (...a: unknown[]) => api.updateProduct(...a),
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
  catalogProductsApi: {
    get: vi.fn(async () => ({
      id: 'CAT-1', sku: 'BVI-1', category: 'SUNGLASS', needs_review: true,
      attributes: { brand_name: 'Ray-Ban', model_no: '4340', colour_code: '601/M3', lens_size: '50' },
    })),
    list: vi.fn(async () => ({ products: [], total: 0 })),
    promoteDryRun: vi.fn(async () => ({ ok: true, gaps: [], duplicate_warnings: [] })),
  },
}));
// The strip is the real one only where a test turns it on (hint.real), fed a
// red RB4340 twin by the mocked hook.
const hint = vi.hoisted(() => ({ real: false }));
vi.mock('../SimilarProductsHint', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../SimilarProductsHint')>();
  const { createElement } = await import('react');
  return {
    SimilarProductsHint: (p: Parameters<typeof actual.SimilarProductsHint>[0]) =>
      hint.real ? createElement(actual.SimilarProductsHint, p) : null,
  };
});
vi.mock('../useSimilarProducts', () => ({
  useSimilarProducts: () => ({
    armed: true,
    data: {
      exact_match: null,
      siblings: [],
      model_colour_count: 0,
      similar_models: [{ model: 'RB4340', brand: 'Ray-Ban', same_colour_size: true, products: [TWIN] }],
    },
  }),
}));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));

import { QuickAddPage } from '../QuickAddPage';
import { ApiError } from '../../../services/api/client';
import { loadCategoryRegistry } from '../../../domain/catalog/productAdd';
import { EDIT_QUEUE_KEY, writeReviewQueue } from '../reviewQueue';

const fill = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  api.createProduct.mockReset();
  api.updateProduct.mockReset();
  api.getProduct.mockReset();
  hint.real = false;
});

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <QuickAddPage />
    </MemoryRouter>,
  );

async function answerDifferent(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: "It's a different product" }));
  await user.click(screen.getByRole('button', { name: 'Different line or series' }));
  await user.click(screen.getByRole('button', { name: 'Save as a different product' }));
}

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

describe('the edit form asks the same question on its PUT', () => {
  it('a 409 NEAR_DUPLICATE on Save changes opens the popup, then resends with the reason', async () => {
    api.getProduct.mockResolvedValue({
      product_id: 'P-ME', sku: 'SG-RAYBAN-4340-710/51', category: 'SG', brand: 'Ray-Ban', model: '4340',
      attributes: { brand_name: 'Ray-Ban', model_no: '4340', colour_code: '601/M3', lens_size: '50' },
      mrp: 8100, offer_price: 8100, hsn_code: '900410', gst_rate: 18, images: [],
    });
    api.updateProduct
      .mockRejectedValueOnce(
        new ApiError('same?', { status: 409, code: 'NEAR_DUPLICATE', detail: { code: 'NEAR_DUPLICATE', existing: TWIN } }),
      )
      .mockResolvedValueOnce({});
    const user = userEvent.setup();
    renderAt('/catalog/add?edit=P-ME');
    await user.click(await screen.findByRole('button', { name: /Save changes/ }));

    await screen.findByRole('heading', { name: 'Is this the same product?' });
    expect(api.updateProduct.mock.calls[0][0]).toBe('P-ME');
    expect(api.updateProduct.mock.calls[0][1]).toMatchObject({ check_near_duplicates: true });
    expect(api.updateProduct.mock.calls[0][1]).not.toHaveProperty('distinct_from');

    await answerDifferent(user);
    await waitFor(() => expect(api.updateProduct).toHaveBeenCalledTimes(2));
    expect(api.updateProduct.mock.calls[1][1]).toMatchObject({
      check_near_duplicates: true,
      distinct_from: [{ product_id: 'P-OLD', reason: 'Different line or series' }],
    });
  });

  it('Save & next: the answer for this product is not sent on the next one', async () => {
    window.sessionStorage.clear();
    writeReviewQueue(
      { ids: ['P-ME', 'P-NEXT'], index: 0, offset: 0, total: 2, filters: {},
        list: { path: '/catalog', params: {}, pageSize: 50 } },
      EDIT_QUEUE_KEY,
    );
    api.getProduct.mockImplementation(async (id: string) => ({
      product_id: id, sku: `SG-RAYBAN-4340-${id}`, category: 'SG', brand: 'Ray-Ban', model: '4340',
      attributes: { brand_name: 'Ray-Ban', model_no: '4340', colour_code: '601/M3', lens_size: '50' },
      mrp: 8100, offer_price: 8100, hsn_code: '900410', gst_rate: 18, images: [],
    }));
    api.updateProduct
      .mockRejectedValueOnce(
        new ApiError('same?', { status: 409, code: 'NEAR_DUPLICATE', detail: { code: 'NEAR_DUPLICATE', existing: TWIN } }),
      )
      .mockResolvedValue({});
    const user = userEvent.setup();
    renderAt('/catalog/add?edit=P-ME&from=list');
    await user.click(await screen.findByRole('button', { name: /Save & next/ }));
    await screen.findByRole('heading', { name: 'Is this the same product?' });
    await answerDifferent(user);
    await waitFor(() => expect(api.updateProduct).toHaveBeenCalledTimes(2));
    expect(api.updateProduct.mock.calls[1][1]).toHaveProperty('distinct_from');

    await waitFor(() => expect(api.getProduct).toHaveBeenCalledWith('P-NEXT'));
    await user.click(await screen.findByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(api.updateProduct).toHaveBeenCalledTimes(3));
    expect(api.updateProduct.mock.calls[2][0]).toBe('P-NEXT');
    expect(api.updateProduct.mock.calls[2][1]).not.toHaveProperty('distinct_from');
  });
});

describe('the strip answer reaches the next save', () => {
  it('the strip "different product" answer is sent as distinct_from on Save', async () => {
    hint.real = true;
    api.createProduct.mockResolvedValueOnce({ product_id: 'P-NEW', sku: 'SG-RAYBAN-4340-601/M3' });
    const user = userEvent.setup();
    renderAt('/catalog/add');
    await loadCategoryRegistry();
    await user.click(await screen.findByText('Sunglasses'));
    fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
    fill(screen.getByLabelText(/^Model No/), '4340');
    fill(screen.getByLabelText(/^Colour Code/), '601/M3');
    fill(screen.getByLabelText(/^MRP/), '8100');

    await answerDifferent(user);
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));
    await waitFor(() => expect(api.createProduct).toHaveBeenCalledTimes(1));
    expect(api.createProduct.mock.calls[0][0]).toMatchObject({
      check_near_duplicates: true,
      distinct_from: [{ product_id: 'P-OLD', reason: 'Different line or series' }],
    });
  });

  it('a saved answer is cleared: the next product asks again and sends no distinct_from', async () => {
    hint.real = true;
    api.createProduct
      .mockResolvedValueOnce({ product_id: 'P-NEW', sku: 'SG-RAYBAN-4340-601/M3' })
      .mockResolvedValueOnce({ product_id: 'P-NEXT', sku: 'SG-RAYBAN-4340-710/51' });
    const user = userEvent.setup();
    renderAt('/catalog/add');
    await loadCategoryRegistry();
    await user.click(await screen.findByText('Sunglasses'));
    const typeIn = () => {
      fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
      fill(screen.getByLabelText(/^Model No/), '4340');
      fill(screen.getByLabelText(/^Colour Code/), '601/M3');
      fill(screen.getByLabelText(/^MRP/), '8100');
    };
    typeIn();
    await answerDifferent(user);
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));
    await waitFor(() => expect(api.createProduct).toHaveBeenCalledTimes(1));
    expect(api.createProduct.mock.calls[0][0]).toHaveProperty('distinct_from');

    // Next product: the near-twin is asked about again, not hidden by the old answer.
    await waitFor(() => expect(screen.getByLabelText(/^Model No/)).toHaveValue(''));
    typeIn();
    await screen.findByRole('button', { name: "It's a different product" });
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));
    await waitFor(() => expect(api.createProduct).toHaveBeenCalledTimes(2));
    expect(api.createProduct.mock.calls[1][0]).not.toHaveProperty('distinct_from');
  });

  it('Skip: an answer given on this product is not sent with the next one (a new ?edit= id)', async () => {
    hint.real = true;
    window.sessionStorage.clear();
    writeReviewQueue(
      { ids: ['P-ME', 'P-NEXT'], index: 0, offset: 0, total: 2, filters: {},
        list: { path: '/catalog', params: {}, pageSize: 50 } },
      EDIT_QUEUE_KEY,
    );
    api.getProduct.mockImplementation(async (id: string) => ({
      product_id: id, sku: `SG-RAYBAN-4340-${id}`, category: 'SG', brand: 'Ray-Ban', model: '4340',
      attributes: { brand_name: 'Ray-Ban', model_no: '4340', colour_code: '601/M3', lens_size: '50' },
      mrp: 8100, offer_price: 8100, hsn_code: '900410', gst_rate: 18, images: [],
    }));
    api.updateProduct.mockResolvedValue({});
    const user = userEvent.setup();
    renderAt('/catalog/add?edit=P-ME&from=list');
    await screen.findByRole('button', { name: "It's a different product" }, { timeout: 5000 });
    await answerDifferent(user);
    await user.click(await screen.findByRole('button', { name: /^Skip/ }));
    await waitFor(() => expect(api.getProduct).toHaveBeenCalledWith('P-NEXT'));
    // P-NEXT asks again: the old answer does not hide its strip.
    await screen.findByRole('button', { name: "It's a different product" });
    await user.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(api.updateProduct).toHaveBeenCalledTimes(1));
    expect(api.updateProduct.mock.calls[0][0]).toBe('P-NEXT');
    expect(api.updateProduct.mock.calls[0][1]).not.toHaveProperty('distinct_from');
  });

  it("an answer on the blank form is dropped when a variant starts and 'New model' leaves it", async () => {
    // Variant mode offers no answer itself (the strip hides same-model rows
    // there), so the leak is an answer given BEFORE it: + Variant, then New model.
    hint.real = true;
    api.getProduct.mockResolvedValue({
      product_id: 'P-SRC', sku: 'SG-RAYBAN-4340-710/51', category: 'SG', brand: 'Ray-Ban', model: '4340',
      attributes: { brand_name: 'Ray-Ban', model_no: '4340', colour_code: '710/51', lens_size: '50' },
      mrp: 8100, offer_price: 8100, hsn_code: '900410', gst_rate: 18, images: [],
    });
    api.createProduct.mockResolvedValue({ product_id: 'P-NEW', sku: 'SG-RAYBAN-4340-601/M3' });
    const GoVariant = () => {
      const nav = useNavigate();
      return <button type="button" onClick={() => nav('/catalog/add?variant=P-SRC')}>go-variant</button>;
    };
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/catalog/add']}>
        <GoVariant />
        <QuickAddPage />
      </MemoryRouter>,
    );
    await loadCategoryRegistry();
    const typeIn = async () => {
      await user.click(await screen.findByText('Sunglasses'));
      fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
      fill(screen.getByLabelText(/^Model No/), '4340');
      fill(screen.getByLabelText(/^Colour Code/), '601/M3');
      fill(screen.getByLabelText(/^MRP/), '8100');
    };
    await typeIn();
    await answerDifferent(user);
    await user.click(screen.getByRole('button', { name: 'go-variant' }));
    await user.click(await screen.findByRole('button', { name: /New model/ }));
    await typeIn();
    // Asked again: the old answer does not hide the strip.
    await screen.findByRole('button', { name: "It's a different product" });
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));
    await waitFor(() => expect(api.createProduct).toHaveBeenCalledTimes(1));
    expect(api.createProduct.mock.calls[0][0]).not.toHaveProperty('distinct_from');
  });

  it('review mode (?review=) offers no "different product" answer: its save never sends one', async () => {
    hint.real = true;
    renderAt('/catalog/add?review=CAT-1');
    // The strip is not drawn in review (IdentitySection: no anchor field), so
    // its "different product" answer, which the catalog save never sends, is
    // never offered there.
    await screen.findByText(/Reviewing BVI-1/, {}, { timeout: 5000 });
    expect(screen.queryByTestId('similar-products-hint')).toBeNull();
    expect(screen.queryByRole('button', { name: "It's a different product" })).toBeNull();
  });
});
