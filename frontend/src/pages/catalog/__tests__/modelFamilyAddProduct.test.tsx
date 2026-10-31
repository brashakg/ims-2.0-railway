// ============================================================================
// G1 (owner 2026-10-18) - Add Product joins an existing model
// ============================================================================
// Same model number in another colour/size = a variant of that model:
//  * brand + model typed match a model -> the banner "This model already
//    exists: <Brand Model> - N variants"; nothing locks while typing and the
//    server decides the link on Save; after Save + New the next colour is
//    entered in variant mode on that model, its model fields locked;
//  * the same-model chip on a VARIANT of the model links to its lead;
//  * "This is a different model" keeps what was typed and the save sends
//    family_opt_out (never variant_of);
//  * editing a variant locks the registry's model-level fields.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Avinash', roles: ['ADMIN'], activeStoreId: 'S1' },
    hasRole: () => true,
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
const updateProduct = vi.hoisted(() => vi.fn(async (..._a: unknown[]) => ({}) as Record<string, unknown>));

const LEAD = {
  product_id: 'P-LEAD',
  sku: 'SG-RAYBAN-RB4165-601',
  category: 'SG',
  brand: 'Ray-Ban',
  model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '601', shape: 'Square', gender: 'Men' },
  mrp: 7890,
  offer_price: 7890,
  hsn_code: '900410',
  gst_rate: 18,
  images: [],
};
const CHILD = { ...LEAD, product_id: 'P-CHILD', sku: 'SG-RAYBAN-RB4165-710', variant_of: 'P-LEAD' };
const FAMILY = { lead_id: 'P-LEAD', lead_sku: LEAD.sku, label: 'Ray-Ban RB4165', count: 3, chips: ['601', '710', '902'] };

// The registry as the server sends it: shape + gender are MODEL-level.
const REGISTRY = {
  categories: [
    {
      code: 'SUNGLASS',
      sku_prefix: 'SG',
      name: 'Sunglass',
      required_fields: ['brand_name', 'model_no', 'colour_code'],
      optional_fields: ['model_name', 'shape', 'gender', 'lens_colour'],
      fields: [],
      forced_discount_category: null,
      has_families: true,
      model_fields: ['brand_name', 'model_no', 'model_name', 'shape', 'gender'],
    },
  ],
};

const createProduct = vi.fn(async () => ({ product_id: 'P-NEW', sku: 'SG-RAYBAN-RB4165-902' }));
const getProduct = vi.fn(async (id: string) => (id === 'P-CHILD' ? CHILD : LEAD) as Record<string, unknown>);
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: {
    getCategoryRegistry: vi.fn(async () => REGISTRY),
    getBrandOptions: vi.fn(async () => ({ brands: [{ name: 'Ray-Ban', subbrands: [], tier: 'PREMIUM' }] })),
    getProduct: (id: string) => getProduct(id),
    createProduct: (...a: unknown[]) => createProduct(...(a as [])),
    updateProduct: (...a: unknown[]) => updateProduct(...a),
    previewSku: vi.fn(async () => ({ category: 'SUNGLASS', sku: 'SG-RAYBAN-RB4165-902' })),
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
// The strip's two outputs, as buttons: a model answer and a sibling chip.
vi.mock('../SimilarProductsHint', () => ({
  SimilarProductsHint: (p: { onPickSibling: (id: string) => void; onFamily?: (f: unknown) => void }) => (
    <>
      <button type="button" onClick={() => p.onFamily?.(FAMILY)}>model answer</button>
      <button type="button" onClick={() => p.onPickSibling('P-CHILD')}>variant chip</button>
    </>
  ),
}));
vi.mock('../useSimilarProducts', () => ({ useSimilarProducts: vi.fn() }));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));

import { QuickAddPage } from '../QuickAddPage';
import { loadCategoryRegistry } from '../../../domain/catalog/productAdd';

const fill = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });

async function typeNewColour(user: ReturnType<typeof userEvent.setup>) {
  render(
    <MemoryRouter initialEntries={['/catalog/add']}>
      <QuickAddPage />
    </MemoryRouter>,
  );
  await loadCategoryRegistry();
  await user.click(await screen.findByText('Sunglasses'));
  fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
  fill(screen.getByLabelText(/^Model No/), 'RB4165');
  fill(screen.getByLabelText(/^Colour Code/), '902');
  fill(screen.getByLabelText(/^MRP/), '8100');
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  createProduct.mockClear();
  getProduct.mockClear();
  toast.warning.mockClear();
});

describe('G1 - a new colour/size joins its model', () => {
  it('a matching model shows the banner; nothing locks while typing; Save + New locks onto the model', async () => {
    createProduct.mockResolvedValueOnce({ product_id: 'P-NEW', sku: 'SG-RAYBAN-RB4165-902', variant_of: 'P-LEAD' } as never);
    const user = userEvent.setup();
    await typeNewColour(user);
    await user.click(screen.getByRole('button', { name: 'model answer' }));
    const banner = await screen.findByTestId('model-family-hint');
    expect(banner).toHaveTextContent('This model already exists: Ray-Ban RB4165 — 3 variants');
    expect(banner).toHaveTextContent('This will be added as a new colour/size of it');
    // typing goes on: the model number can still become another one
    expect(screen.getByLabelText(/^Model No/)).not.toBeDisabled();
    fill(screen.getByLabelText(/^Model No/), 'RB4165F');
    expect(screen.getByLabelText(/^Model No/)).toHaveValue('RB4165F');
    fill(screen.getByLabelText(/^Model No/), 'RB4165');
    expect(screen.getByLabelText(/^Shape/)).not.toBeDisabled();
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
    // the server decides the link from what is typed at Save
    const payload = createProduct.mock.calls[0][0] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('variant_of');
    expect(payload).not.toHaveProperty('family_opt_out');
    // ... and the next colour is entered on that model, its fields locked
    await screen.findByTestId('model-family-banner');
    expect(getProduct).toHaveBeenCalledWith('P-LEAD');
    expect(screen.getByLabelText(/^Shape/)).toBeDisabled();
    expect(screen.getByLabelText(/^Shape/)).toHaveValue('Square');
    expect(screen.getByLabelText(/^Colour Code/)).not.toBeDisabled();
  });

  it("the same-model chip on a variant links to the variant's lead", async () => {
    const user = userEvent.setup();
    await typeNewColour(user);
    await user.click(screen.getByRole('button', { name: 'variant chip' }));
    await user.click(await screen.findByRole('button', { name: /Save variant/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
    expect(createProduct.mock.calls[0][0]).toMatchObject({ variant_of: 'P-LEAD' });
  });

  it('"This is a different model" keeps the typing and saves it as its own model', async () => {
    const user = userEvent.setup();
    await typeNewColour(user);
    await user.click(screen.getByRole('button', { name: 'model answer' }));
    await user.click(await screen.findByRole('button', { name: 'This is a different model' }));
    expect(screen.queryByTestId('model-family-hint')).toBeNull();
    expect(screen.getByLabelText(/^Colour Code/)).toHaveValue('902');
    // the same answer again does not bring the banner back
    await user.click(screen.getByRole('button', { name: 'model answer' }));
    expect(screen.queryByTestId('model-family-hint')).toBeNull();
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
    const payload = createProduct.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.family_opt_out).toBe(true);
    expect(payload).not.toHaveProperty('variant_of');
  });

  it("editing a colour/size of a model: the model's fields are locked, its own are not", async () => {
    render(
      <MemoryRouter initialEntries={['/catalog/add?edit=P-CHILD']}>
        <QuickAddPage />
      </MemoryRouter>,
    );
    await loadCategoryRegistry();
    await screen.findByRole('button', { name: /Save changes/ });
    await waitFor(() => expect(screen.getByLabelText(/^Shape/)).toBeDisabled());
    expect(screen.getByLabelText(/^Model No/)).toBeDisabled();
    expect(screen.getAllByText('edit on the model').length).toBeGreaterThan(0);
    expect(screen.getByLabelText(/^Colour Code/)).not.toBeDisabled();
    expect(screen.getByLabelText(/^MRP/)).not.toBeDisabled();
  });

  it('a model edit that some colours/sizes refused names them in a warning', async () => {
    updateProduct.mockResolvedValueOnce({ message: 'Product updated', family_not_updated: ['SG-RAYBAN-RB4165-710'] });
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/catalog/add?edit=P-LEAD']}>
        <QuickAddPage />
      </MemoryRouter>,
    );
    await loadCategoryRegistry();
    await user.click(await screen.findByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(updateProduct).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(toast.warning).toHaveBeenCalled());
    expect(String(toast.warning.mock.calls[0][0])).toContain('SG-RAYBAN-RB4165-710');
  });
});
