// ============================================================================
// A product's weight, on every mode of the "Edit everything" page (owner
// 2026-10-22: "she filled weight in, now she can't see it")
// ============================================================================
// Weight (grams) lives on the billing spine. The edit page (?edit=) reads the
// spine as stored: category 'SUNGLASS' / 'FRAME', never the picker's 'SG' /
// 'FR' -- left canonical, the page lost every rule keyed on the code and a
// sunglass's Weight box left its place after Warranty. The review editor
// (?review=) shows the weight the catalogue door serves (the spine's); the
// duplicate popup's "new colour" keeps a weight the operator typed.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Cat', roles: ['ADMIN'], activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const SPINE = {
  product_id: 'P-SG',
  sku: 'SGCARRERA1234',
  category: 'SUNGLASS',
  brand: 'Carrera',
  model: '1234',
  // Stored before today's lists: brand, shape, gender and UV are NOT in the
  // form's select options -- they must stay valid on the edit page.
  attributes: {
    brand_name: 'Carrera', model_no: '1234', colour_code: '807', warranty: '1 year',
    shape: 'Butterfly', gender: 'Male', uv_protection: 'UV 400',
  },
  mrp: 9000,
  offer_price: 9000,
  hsn_code: '900410',
  gst_rate: 18,
  weight: 40,
  images: ['/api/v1/products/image/a'],
};

// A sibling of the same model, offered by the similar-products chip.
const SIBLING_B = {
  ...SPINE,
  product_id: 'P-B',
  sku: 'SGCARRERA1234-901',
  brand: 'Ray-Ban',
  attributes: { brand_name: 'Ray-Ban', model_no: '1234', colour_code: '901', warranty: '1 year' },
  weight: 38,
};

const current = vi.hoisted(() => ({ product: {} as Record<string, unknown> }));
const productSpies = vi.hoisted(() => ({}) as Record<string, ReturnType<typeof vi.fn>>);
const known: Record<string, (...a: unknown[]) => unknown> = {
  getCategoryRegistry: async () => { throw new Error('offline'); },
  getBrandOptions: async () => ({ brands: [] }),
  getProduct: async (id: unknown) => (id === 'P-B' ? SIBLING_B : current.product),
  updateProduct: async () => ({}),
};
vi.mock('../../../services/api/products', () => {
  class DuplicateProductError extends Error {
    existing: Record<string, unknown>;
    constructor(message: string, existing: Record<string, unknown>) {
      super(message);
      this.existing = existing;
    }
  }
  return {
    DuplicateProductError,
    productApi: new Proxy({}, {
      get: (_t, key: string) => (productSpies[key] ??= vi.fn(known[key] ?? (async () => ({})))),
    }),
    catalogApi: { getOnlineSummary: vi.fn(async () => ({ catalog: null })) },
  };
});
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: {
    get: vi.fn(async () => ({ data: {} })),
    put: vi.fn(async () => ({ data: {} })),
    post: vi.fn(async () => ({ data: {} })),
    patch: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
}));
const catalogSpies = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  promoteDryRun: vi.fn(),
  list: vi.fn(),
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error { status = 0; },
  catalogProductsApi: catalogSpies,
}));
vi.mock('../SimilarProductsHint', () => ({
  SimilarProductsHint: ({ onPickSibling }: { onPickSibling: (id: string) => void }) => (
    <button type="button" onClick={() => onPickSibling('P-B')}>pick sibling B</button>
  ),
}));

import { QuickAddPage } from '../QuickAddPage';
import { DuplicateProductError } from '../../../services/api/products';
import { productToFormValues } from '../../../domain/catalog/productAdd/cloneMapping';
import { getCategoryFields } from '../../../domain/catalog/productAdd/categoryFields';
import { validateProductForm } from '../../../domain/catalog/productAdd/formModel';
import { formValuesToCatalogUpdate, validateReviewForm } from '../../../domain/catalog/productAdd/reviewMapping';

const weightBox = () => screen.getByTitle('Weight (g)') as HTMLInputElement;
const open = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <QuickAddPage />
    </MemoryRouter>,
  );

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  for (const fn of Object.values(productSpies)) fn.mockClear();
  current.product = SPINE;
  catalogSpies.get.mockReset();
  catalogSpies.update.mockReset().mockResolvedValue({ product: {} });
  catalogSpies.promoteDryRun.mockReset().mockResolvedValue({ ok: true, gaps: [], duplicate_warnings: [] });
  catalogSpies.list.mockReset().mockResolvedValue({ products: [], total: 0 });
  window.sessionStorage.clear();
});

describe('a stored category opens as the form category', () => {
  it.each([
    ['SUNGLASS', 'SG'],
    ['FRAME', 'FR'],
    ['OPTICAL_LENS', 'LS'],
  ])('%s opens as %s', (stored, code) => {
    expect(productToFormValues({ ...SPINE, category: stored }).category).toBe(code);
  });
});

describe('the edit page (?edit=) on a stored sunglass / frame', () => {
  it.each(['SUNGLASS', 'FRAME'])('%s: the stored weight shows, inline as eyewear, and a save sends it', async (cat) => {
    current.product = { ...SPINE, category: cat };
    open('/catalog/add?edit=P-SG');
    await waitFor(() => expect(weightBox().value).toBe('40'));
    // Eyewear: Weight sits in the attribute grid; Advanced is HSN & GST only.
    expect(screen.getByText('Advanced — HSN & GST')).toBeInTheDocument();
    expect(screen.queryByText('Advanced — HSN, GST & weight')).toBeNull();

    fireEvent.change(weightBox(), { target: { value: '42' } });
    await userEvent.setup().click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(productSpies.updateProduct).toHaveBeenCalledTimes(1));
    const [id, body] = productSpies.updateProduct.mock.calls[0] as [
      string, { weight?: number; attributes: Record<string, string> },
    ];
    expect(id).toBe('P-SG');
    expect(body.weight).toBe(42);
    // The stored off-list values went back unchanged.
    expect(body.attributes).toMatchObject({ shape: 'Butterfly', gender: 'Male', uv_protection: 'UV 400' });
  });

  it('shows a stored off-list value as "(current)"', async () => {
    open('/catalog/add?edit=P-SG');
    await waitFor(() => expect(weightBox().value).toBe('40'));
    expect(screen.getByRole('option', { name: 'Butterfly (current)' })).toBeInTheDocument();
    expect((screen.getByTitle('Shape') as HTMLSelectElement).value).toBe('Butterfly');
  });
});

describe('the off-list rule', () => {
  const shapeField = () => getCategoryFields('SG').find((f) => f.name === 'shape')!;
  const values = (shape: string) => ({
    ...productToFormValues(SPINE),
    attributes: { ...productToFormValues(SPINE).attributes, shape },
  });

  it('keeps a stored value valid, and still refuses a NEW off-list value', () => {
    expect(shapeField().options).not.toContain('Butterfly');
    const stored = productToFormValues(SPINE).attributes;
    expect(validateProductForm(values('Butterfly'), stored).shape).toBeUndefined();
    expect(validateProductForm(values('Heart'), stored).shape).toMatch(/not in the allowed list/);
    expect(validateProductForm(values('Butterfly')).shape).toMatch(/not in the allowed list/);
  });

  it('does not judge the brand before the Brand Master list has arrived', () => {
    // The registry is offline in this file: the local list is not the owner's.
    expect(validateProductForm(values('Butterfly')).brand_name).toBeUndefined();
  });

  it('the review editor follows the same rule', () => {
    const baseline = values('Butterfly');
    const touched = { ...baseline, attributes: { ...baseline.attributes, colour_code: '808' } };
    expect(validateReviewForm(touched, baseline).shape).toBeUndefined();
    const fresh = { ...touched, attributes: { ...touched.attributes, shape: 'Heart' } };
    expect(validateReviewForm(fresh, baseline).shape).toMatch(/not in the allowed list/);
  });
});

describe('the description generator', () => {
  it('is given the weight, so the spec table can show it', async () => {
    open('/catalog/add?edit=P-SG');
    await waitFor(() => expect(weightBox().value).toBe('40'));
    await userEvent.setup().click(screen.getByRole('button', { name: /Auto-fill with AI/ }));
    await waitFor(() => expect(productSpies.generateDescription).toHaveBeenCalledTimes(1));
    const [body] = productSpies.generateDescription.mock.calls[0] as [{ attributes: Record<string, string> }];
    expect(body.attributes.weight).toBe('40 g');
  });
});

describe('picking a sibling while editing', () => {
  it('on ?edit=A, sibling B starts a NEW colour: Save creates it and A is never written', async () => {
    // A with values from today's lists (its own values ride into the new
    // colour as typed, and a NEW product has nothing stored to exempt).
    current.product = { ...SPINE, brand: 'Ray-Ban', attributes: SIBLING_B.attributes };
    open('/catalog/add?edit=P-SG');
    await waitFor(() => expect(weightBox().value).toBe('40'));
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'pick sibling B' }));
    const save = await screen.findByRole('button', { name: /Save variant/ });
    fireEvent.change(screen.getByLabelText(/^Colour Code/), { target: { value: '777' } });
    await user.click(save);
    await waitFor(() => expect(productSpies.createProduct).toHaveBeenCalledTimes(1));
    expect(productSpies.createProduct.mock.calls[0][0]).toMatchObject({ variant_of: 'P-B' });
    expect(productSpies.updateProduct?.mock.calls.length ?? 0).toBe(0);
  });
});

describe('the review editor (?review=)', () => {
  it('shows the weight the catalogue door serves and sends a changed one', async () => {
    catalogSpies.get.mockResolvedValue({
      id: 'CAT-1',
      sku: 'SGCARRERA1234',
      category: 'SUNGLASS',
      attributes: SPINE.attributes,
      pricing: { mrp: 9000, offer_price: 9000 },
      hsn_code: '900410',
      gst_rate: 18,
      weight: 40,
      needs_review: true,
      updated_at: '2026-10-22T10:00:00+05:30',
    });
    open('/catalog/add?review=CAT-1');
    await waitFor(() => expect(weightBox().value).toBe('40'));
    fireEvent.change(weightBox(), { target: { value: '41' } });
    await userEvent.setup().click(screen.getByRole('button', { name: /Save fixes/ }));
    await waitFor(() => expect(catalogSpies.update).toHaveBeenCalledTimes(1));
    expect(catalogSpies.update.mock.calls[0][1]).toMatchObject({ weight: 41 });
  });
});

describe('clearing a weight in the review editor', () => {
  it('an emptied stored weight is sent as null (a clear), never as a missing key', () => {
    const snap = { ...productToFormValues(SPINE), weight: '40' };
    expect(formValuesToCatalogUpdate({ ...snap, weight: '' }, snap).weight).toBeNull();
    expect(formValuesToCatalogUpdate({ ...snap, weight: '0' }, snap).weight).toBeNull();
    expect(formValuesToCatalogUpdate({ ...snap, weight: '41' }, snap).weight).toBe(41);
  });
});

describe('the duplicate popup\'s "new colour"', () => {
  it('keeps the weight the operator typed', async () => {
    const existing = { ...SPINE, product_id: 'P-LIVE', weight: undefined, is_active: true, catalog_status: 'ACTIVE' };
    current.product = existing;
    productSpies.createProduct = vi.fn(async () => {
      throw new DuplicateProductError('exists', existing);
    });
    open('/catalog/add');
    const user = userEvent.setup();
    await user.click(screen.getByText('Sunglasses'));
    fireEvent.change(screen.getByLabelText(/^Brand Name/), { target: { value: 'Ray-Ban' } });
    fireEvent.change(screen.getByLabelText(/^Model No/), { target: { value: '1234' } });
    fireEvent.change(screen.getByLabelText(/^Colour Code/), { target: { value: '807' } });
    fireEvent.change(screen.getByLabelText(/^MRP/), { target: { value: '9000' } });
    fireEvent.change(weightBox(), { target: { value: '40' } });
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await user.click(await screen.findByRole('button', { name: /Add a new colour\/size of this model/ }));
    await screen.findByRole('button', { name: /Save variant/ });
    expect(weightBox().value).toBe('40');
  });
});
