// ============================================================================
// Add product - owner rulings 2026-09-28 (second set) + 2026-09-29
// ============================================================================
// Audit rows F12, F13, F68, F69, F73, F92: each test pins one owner rule on
// the rendered screen.
//
// F12/D6 the brand default ALWAYS decides the website: no Sync to Shopify
//        switch on Add / Edit / Quick add, a read-only line names the brand
//        default, and the create payload carries no sync choice.
// F13/D5 the new product's readable SKU is PREVIEWED before saving, and the
//        preview comes from the server (the minting function), never a copy.
// F68    after Save + New the cursor is in Model No and the reorder level stays.
// F69    the same-model chip keeps the typed colour, copies weight + reorder
//        level, and is at least the app's 36px control height.
// F73    reorder level -1 = not set: a new form starts blank and says 'not
//        set', a blank level is never saved as a number, and editing a product
//        whose level is -1 shows 'not set' instead of inventing 5.
// F92    menu labels say what each buying door is for; Buy Desk does not claim
//        '0 products' while it is still loading.

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Avinash', roles: ['ADMIN'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const SOURCE_PRODUCT = {
  product_id: 'P-SRC',
  sku: 'SGRAYBANRB4165001',
  category: 'SG',
  brand: 'Ray-Ban',
  model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '001', lens_size: '54' },
  mrp: 7890,
  offer_price: 7890,
  hsn_code: '900410',
  gst_rate: 18,
  weight: 25,
  reorder_point: 2,
  images: [],
};
const BRANDS = [
  { name: 'Ray-Ban', subbrands: [], tier: 'PREMIUM', sync_to_shopify_default: true },
  // Oakley, not Carrera: the brand select's offline fallback list has no Carrera.
  { name: 'Oakley', subbrands: [], tier: 'PREMIUM', sync_to_shopify_default: false },
];

const createProduct = vi.fn(async () => ({ product_id: 'P-NEW', sku: 'SG-RAYBAN-RB4165-601' }));
const updateProduct = vi.fn(async () => ({}));
const getProduct = vi.fn(async () => SOURCE_PRODUCT as Record<string, unknown>);
const previewSku = vi.fn(async () => ({ category: 'SUNGLASS', sku: 'SG-RAYBAN-RB4165-601' }));
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: {
    getCategoryRegistry: vi.fn(async () => { throw new Error('offline'); }),
    getBrandOptions: vi.fn(async () => ({ brands: BRANDS })),
    getProduct: (...a: unknown[]) => getProduct(...a),
    uploadProductImage: vi.fn(async () => ({ url: '/api/v1/products/image/f1' })),
    createProduct: (...a: unknown[]) => createProduct(...a),
    updateProduct: (...a: unknown[]) => updateProduct(...a),
    previewSku: (...a: unknown[]) => previewSku(...a),
  },
}));
vi.mock('../../../services/api/productTemplates', () => ({
  productTemplatesApi: { list: vi.fn(async () => ({ templates: [] })) },
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error { status = 0; },
  catalogProductsApi: {},
}));
// The page's strip is a stand-in chip that takes the page's own pick path; the
// real strip (for the chip's size) is imported unmocked further down.
vi.mock('../SimilarProductsHint', () => ({
  SimilarProductsHint: (p: { onPickSibling: (id: string) => void }) => (
    <button type="button" onClick={() => p.onPickSibling('P-SRC')}>same model chip</button>
  ),
}));
vi.mock('../useSimilarProducts', () => ({ useSimilarProducts: vi.fn() }));
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [{ value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 }],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : ''),
  resolveGstRate: () => 18,
}));
const getRows = vi.fn();
vi.mock('../../../services/api/buyDesk', () => ({
  buyDeskApi: { getRows: (...a: unknown[]) => getRows(...a) },
}));

import { QuickAddPage } from '../QuickAddPage';
import { DuplicateProductError } from '../../../services/api/products';
import BuyDeskPage from '../BuyDeskPage';
import { useSimilarProducts } from '../useSimilarProducts';
import { NAV_GROUPS } from '../../../components/shell/navConfig';

const renderPage = (url = '/catalog/add') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <QuickAddPage />
    </MemoryRouter>,
  );

// One change event per field, as the sibling suite does (the page re-validates
// on every change).
const fill = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });
const reorderInput = () => screen.getByLabelText('Reorder Level') as HTMLInputElement;

async function sunglass(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByText('Sunglass'));
  fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
  fill(screen.getByLabelText(/^Model No/), 'RB4165');
  fill(screen.getByLabelText(/^Colour Code/), '601');
  fill(screen.getByLabelText(/^MRP/), '7890');
}

// A number saved as the level. -1 (not set) or nothing at all are both fine.
const savedLevels = () =>
  [
    ...createProduct.mock.calls.map((c) => (c[0] as Record<string, unknown>)?.reorder_point),
    ...updateProduct.mock.calls.map((c) => (c[1] as Record<string, unknown>)?.reorder_point),
  ].filter((v) => typeof v === 'number' && v >= 0);

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  createProduct.mockClear();
  updateProduct.mockClear();
  getProduct.mockClear();
  previewSku.mockClear();
});

describe('F12 / D6 - the brand default decides the website', () => {
  it('Add product has no Sync to Shopify switch', async () => {
    renderPage();
    await screen.findByText('Sunglass');
    expect(screen.queryByLabelText('Sync to Shopify')).toBeNull();
  });

  it('Edit product has no Sync to Shopify switch either', async () => {
    renderPage('/catalog/add?edit=P-SRC');
    await screen.findByRole('button', { name: /Save changes/ });
    expect(screen.queryByLabelText('Sync to Shopify')).toBeNull();
  });

  it('a read-only line names the brand default, and follows the brand', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    const rayBan = (await screen.findByText(/brand default/i)).textContent || '';
    expect(rayBan).toMatch(/Ray-Ban/);

    fill(screen.getByLabelText(/^Brand Name/), 'Oakley');
    await waitFor(() => expect(screen.getByText(/brand default/i).textContent).toMatch(/Oakley/));
    // Same sentence with only the name swapped would mean the verdict never moved.
    expect(screen.getByText(/brand default/i).textContent).not.toBe(rayBan.replace('Ray-Ban', 'Oakley'));
  });

  it('the create payload carries no sync choice for the server to honour', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
    const payload = createProduct.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.sync_to_shopify).toBeUndefined();
    expect((payload.shopify as Record<string, unknown> | undefined)?.sync_to_shopify).toBeUndefined();
  });
});

describe('F13 / D5 - the SKU is previewed before saving', () => {
  it('shows the server-minted readable SKU once brand, model and colour are in', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    expect(await screen.findByText('SG-RAYBAN-RB4165-601')).toBeInTheDocument();
    // From the server's minting function, not a second copy of the rule here.
    const [category, attrs] = previewSku.mock.calls.at(-1) as [string, Record<string, string>];
    expect(category).toBe('SG');
    expect(attrs).toMatchObject({ brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '601' });
    expect(createProduct).not.toHaveBeenCalled();
  });
});

describe('F68 - Save + New keeps you typing', () => {
  it('lands the cursor in Model No and keeps the reorder level', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    fill(reorderInput(), '2');
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByLabelText(/^Model No/)).toHaveFocus());
    expect(reorderInput().value).toBe('2');
  });
});

describe('F69 - the same-model chip', () => {
  it('keeps the typed colour and copies weight and reorder level', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    await user.click(screen.getByRole('button', { name: 'same model chip' }));
    await waitFor(() => expect(getProduct).toHaveBeenCalledWith('P-SRC'));
    await waitFor(() => expect((screen.getByTitle('Weight (g)') as HTMLInputElement).value).toBe('25'));
    expect((screen.getByLabelText(/^Colour Code/) as HTMLInputElement).value).toBe('601');
    expect(reorderInput().value).toBe('2');
  });

  it('the chip is at least the 36px control height', async () => {
    const { SimilarProductsHint: RealHint } =
      await vi.importActual<typeof import('../SimilarProductsHint')>('../SimilarProductsHint');
    (useSimilarProducts as unknown as Mock).mockReturnValue({
      armed: true,
      data: {
        exact_match: null,
        siblings: [{ product_id: 'P-SRC', sku: 'SGRAYBANRB4165001', name: 'Ray-Ban RB4165', colour_code: '001' }],
        model_colour_count: 1,
      },
    });
    render(
      <RealHint category="SG" brand="Ray-Ban" model="RB4165" colour="" size=""
        onPickSibling={vi.fn()} onOpenExisting={vi.fn()} />,
    );
    const chip = screen.getByRole('button', { name: /001/ });
    expect(chip.className).toMatch(/(^|\s)min-h-(9|10|11|12|\[(3[6-9]|4\d)px\])(\s|$)/);
  });
});

describe('F73 - reorder level -1 = not set', () => {
  it('a new product starts with the level blank and says not set', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText('Sunglass'));
    expect(reorderInput().value).toBe('');
    expect(reorderInput().placeholder).toMatch(/not set/i);
    const card = screen.getByRole('heading', { name: 'Review' }).closest('.card') as HTMLElement;
    const row = within(card).getByText('Reorder level').closest('dl') as HTMLElement;
    expect(row).toHaveTextContent(/not set/i);
  });

  it('a level left blank is never saved as a number', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    fill(reorderInput(), '');
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 50)); // the follow-up write, if any
    expect(savedLevels()).toEqual([]);
  });

  it('editing a product whose level is -1 shows not set, and saving keeps it unset', async () => {
    getProduct.mockResolvedValueOnce({ ...SOURCE_PRODUCT, reorder_point: -1 });
    const user = userEvent.setup();
    renderPage('/catalog/add?edit=P-SRC');
    await screen.findByRole('button', { name: /Save changes/ });
    await waitFor(() => expect(screen.getByLabelText(/^Model No/)).toHaveValue('RB4165'));
    expect(reorderInput().value).toBe('');
    await user.click(screen.getByRole('button', { name: /Save changes/ }));
    await waitFor(() => expect(updateProduct).toHaveBeenCalledTimes(1));
    expect(savedLevels()).toEqual([]);
  });
});

describe('F73 - a typed level is never silently lost', () => {
  it.each(['2.5', '-3'])('editing: %s is refused, not saved as not set', async (typed) => {
    const user = userEvent.setup();
    renderPage('/catalog/add?edit=P-SRC');
    await screen.findByRole('button', { name: /Save changes/ });
    await waitFor(() => expect(reorderInput().value).toBe('2'));
    fill(reorderInput(), typed);
    await user.click(screen.getByRole('button', { name: /Save changes/ }));
    await new Promise((r) => setTimeout(r, 50));
    expect(updateProduct).not.toHaveBeenCalled();
    expect(reorderInput().value).toBe(typed); // still there to correct
  });

  it('creating: 2.5 is refused, not saved without a level', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    fill(reorderInput(), '2.5');
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await new Promise((r) => setTimeout(r, 50));
    expect(createProduct).not.toHaveBeenCalled();
  });

  it('the duplicate rescue keeps the typed level', async () => {
    createProduct.mockRejectedValueOnce(
      Object.assign(new DuplicateProductError('duplicate'), {
        existing: { product_id: 'P-SRC', sku: SOURCE_PRODUCT.sku },
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    fill(reorderInput(), '4');
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await user.click(await screen.findByRole('button', { name: /Add a new colour\/size of this model/ }));
    await waitFor(() => expect(getProduct).toHaveBeenCalledWith('P-SRC'));
    // The rescued product's own level is 2; the operator typed 4.
    await waitFor(() => expect(screen.getByLabelText(/^Model No/)).toHaveValue('RB4165'));
    expect(reorderInput().value).toBe('4');
  });
});

describe('F92 - two buying doors, each says what it is for', () => {
  const item = (id: string) => NAV_GROUPS.flatMap((g) => g.items).find((i) => i.id === id)!;

  it('menu labels', () => {
    expect(item('buy-desk').label).toMatch(/^Buy Desk\s*[-–—]\s*what to reorder$/);
    expect(item('purchase').label).toMatch(/^Purchase\s*[-–—]\s*orders, receiving, bills$/);
  });

  it('both doors stay (guard)', () => {
    expect(item('buy-desk').to).toBe('/catalog/buy-desk');
    expect(item('purchase').to).toBe('/purchase');
  });

  it('Buy Desk does not say 0 products while it is still loading', async () => {
    getRows.mockReturnValue(new Promise(() => {}));
    render(
      <MemoryRouter initialEntries={['/catalog/buy-desk']}>
        <BuyDeskPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Loading/)).toBeInTheDocument();
    expect(screen.queryByText(/\b0 products\b/)).toBeNull();
  });
});
