// ============================================================================
// Add-a-Product: the design tweaks that have to survive (2026-08-30 artboard)
// ============================================================================
// Same screen, same owner-locked field order, same payload -- plus: the sticky
// bar names what the validator would still refuse (its OWN list, never a
// second one) and a tap lands on the field; each section header carries the
// count of errors inside it (a closed section is unmounted, so this is the only
// way an error in there is visible); the page-level rule that shrank every
// control to 32px is gone and the two photo controls are real tap targets; the
// review card names attributes by their registry label. Every assertion below
// is on the rendered DOM and fails if the behaviour is reverted.

import { fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
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

// No network. The registry fetch fails soft (the form falls back to the local
// required flags: SG needs brand, model no, colour code), brand options come
// back empty (free-form sub-brand), an upload returns one stable URL, a create
// returns a minted SKU. `getProduct` seeds the variant-mode test.
const createProduct = vi.fn(async () => ({ product_id: 'P-NEW', sku: 'SG-RAYB-4165-601' }));
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
};
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: {
    getCategoryRegistry: vi.fn(async () => { throw new Error('offline'); }),
    getBrandOptions: vi.fn(async () => ({ brands: [] })),
    getProduct: vi.fn(async () => SOURCE_PRODUCT),
    uploadProductImage: vi.fn(async () => ({ url: '/api/v1/products/image/f1' })),
    createProduct: (...a: unknown[]) => createProduct(...a),
    updateProduct: vi.fn(async () => ({})),
  },
}));
vi.mock('../../../services/api/productTemplates', () => ({
  productTemplatesApi: { list: vi.fn(async () => ({ templates: [] })) },
}));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error { status = 0; },
  catalogProductsApi: {},
}));
vi.mock('../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));
// The server-fed GST tables, as the shop sees them once loaded: sunglasses
// auto-fill 900410 @ 18%; 900311 (frames, 5%) is the "other" code a
// cataloguer can pick to prove the rate text follows the HSN.
vi.mock('../../../constants/gstRuntime', () => ({
  hsnOptions: () => [
    { value: '900410', label: '900410 - Sunglasses (18%)', gstRate: 18 },
    { value: '900311', label: '900311 - Frames (5%)', gstRate: 5 },
  ],
  resolveHsn: (c?: string | null) => (c === 'SG' ? '900410' : c === 'FR' ? '900311' : ''),
  resolveGstRate: (_c?: string | null, h?: string | null) => (h === '900311' ? 5 : 18),
}));

import { QuickAddPage } from '../QuickAddPage';
import {
  getCategoryFields,
  validateProductForm,
  type ProductFormValues,
} from '../productAddShared';

const renderPage = (url = '/catalog/add') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <QuickAddPage />
    </MemoryRouter>,
  );

// The form's own values after the Sunglass tile is tapped and nothing typed:
// the category autofill has already set the HSN, everything else is blank.
const blankSunglass = (over: Partial<ProductFormValues> = {}): ProductFormValues => ({
  category: 'SG',
  attributes: {},
  hsnCode: '900410',
  gstRate: '18',
  mrp: '',
  discountCategory: '',
  syncToShopify: false,
  shopifyTags: [],
  publishPOS: true,
  ...over,
});

// Labels read straight off the registry in the test, not through the page's
// own helper, so an assertion here cannot be true by construction.
const registryLabel = (key: string) =>
  key === 'mrp' ? 'MRP' : key === 'hsn_code' ? 'HSN Code'
    : getCategoryFields('SG').find((f) => f.name === key)!.label;

// One change event per field (not one per keystroke): the page re-runs the
// validator on every change, so userEvent.type against it blew the 15s
// per-test load bound when the suite ran alongside its siblings.
const fill = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });

const stillMissing = () =>
  within(screen.getByTestId('qa-still-missing')).getAllByRole('button').map((b) => b.textContent);

let scrollSpy: ReturnType<typeof vi.fn>;
beforeEach(() => {
  // jsdom has neither; the jump-to relies on the first.
  scrollSpy = vi.fn();
  Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  createProduct.mockClear();
});

describe('1 - "Still missing" in the save bar', () => {
  it('is the validator\'s own required set, by registry label, and shrinks as fields fill', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText('Sunglass'));

    const expected = Object.keys(validateProductForm(blankSunglass())).map(registryLabel);
    // Sanity: the validator really does name something (else the test is vacuous).
    expect(expected).toEqual(expect.arrayContaining(['Brand Name', 'Model No', 'Colour Code', 'MRP']));
    expect(stillMissing()).toEqual(expected);

    // Save stays enabled -- the list informs, it does not gate.
    expect(screen.getByRole('button', { name: /Save product/ })).toBeEnabled();

    fill(screen.getByLabelText(/^Model No/), 'RB4165');
    fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
    const after = Object.keys(
      validateProductForm(blankSunglass({ attributes: { model_no: 'RB4165', brand_name: 'Ray-Ban' } })),
    ).map(registryLabel);
    expect(after).not.toContain('Model No');
    expect(stillMissing()).toEqual(after);
  });

  it('tapping a chip opens the closed section and lands focus on that field', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText('Sunglass'));

    // Close Pricing: its MRP input is unmounted, yet its chip is still in the bar.
    await user.click(screen.getByRole('button', { name: /^Pricing/ }));
    expect(screen.queryByLabelText(/^MRP/)).toBeNull();

    await user.click(within(screen.getByTestId('qa-still-missing')).getByRole('button', { name: 'MRP' }));
    await waitFor(() => expect(screen.getByLabelText(/^MRP/)).toHaveFocus());
    expect(scrollSpy).toHaveBeenCalled();
  });

  it('a blanked HSN joins the same list, flags the Advanced row, and its chip focuses the HSN select', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText('Sunglass'));
    expect(stillMissing()).not.toContain('HSN Code');
    expect(screen.queryByText('HSN Code required')).toBeNull();

    fill(screen.getByLabelText(/^HSN Code/), '');
    expect(stillMissing()).toContain('HSN Code');
    expect(screen.getByText('HSN Code required')).toBeInTheDocument();

    await user.click(within(screen.getByTestId('qa-still-missing')).getByRole('button', { name: 'HSN Code' }));
    await waitFor(() => expect(screen.getByLabelText(/^HSN Code/)).toHaveFocus());
  });

  it('does not demand an HSN the server master cannot name yet', () => {
    // resolveHsn() is '' until GET /products/gst-rates answers, and there the
    // page never auto-filled a code and the SERVER supplies its own on save --
    // so the rule must not fire, or a cold session cannot save at all.
    expect(validateProductForm(blankSunglass({ category: 'AC', hsnCode: '' })).hsn_code)
      .toBeUndefined();
    // Where the master DOES name one, a blanked code is still refused.
    expect(validateProductForm(blankSunglass({ hsnCode: '' })).hsn_code).toBe('Pick an HSN code');
  });
});

describe('2 - a count on each section header', () => {
  it('splits the validator\'s errors by section, stays visible on a CLOSED header, and clears as fields fill', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText('Sunglass'));

    const errs = Object.keys(validateProductForm(blankSunglass()));
    const pricing = errs.filter((k) => k === 'mrp' || k === 'offer_price').length;
    const identity = errs.length - pricing;
    expect(screen.getByTestId('qa-section-issues-identity')).toHaveTextContent(`${identity} to fix`);
    expect(screen.getByTestId('qa-section-issues-pricing')).toHaveTextContent(`${pricing} to fix`);
    expect(screen.queryByTestId('qa-section-issues-inventory')).toBeNull();

    // Collapse Identity: its inputs are gone, the count is not.
    await user.click(screen.getByRole('button', { name: /^Identity/ }));
    expect(screen.queryByLabelText(/^Model No/)).toBeNull();
    expect(screen.getByTestId('qa-section-issues-identity')).toHaveTextContent(`${identity} to fix`);

    fill(screen.getByLabelText(/^MRP/), '7890');
    expect(screen.queryByTestId('qa-section-issues-pricing')).toBeNull();
  });
});

describe('4 - touch targets', () => {
  it('no page-level h-8 shrink remains, and the two photo controls are 40px targets', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await user.click(screen.getByText('Sunglass'));

    const root = container.querySelector('.inv-body')!;
    expect(root.className).not.toMatch(/input-field\]:h-8/);
    container.querySelectorAll('.input-field').forEach((el) => {
      expect(el.className).not.toMatch(/\bh-8\b/);
    });

    // Twelve tiles, exactly as today, each with a floor height.
    const tiles = within(container.querySelector('#qa-field-category')!).getAllByRole('button');
    expect(tiles).toHaveLength(12);
    tiles.forEach((t) => expect(t.className).toMatch(/\bmin-h-\[72px\]/));

    await user.upload(screen.getByTitle('Upload product images'), new File(['x'], 'a.png', { type: 'image/png' }));
    const del = await screen.findByRole('button', { name: 'Remove image' });
    expect(del.className).toMatch(/\bmin-h-10\b/);
    expect(del.className).toMatch(/\bmin-w-10\b/);
    expect(screen.getByRole('button', { name: 'Remove background' }).className).toMatch(/\bmin-h-10\b/);
  });
});

describe('5 - GST rate is text, and the value still posts', () => {
  it('shows the number as text (no read-only box), drops it when the HSN disagrees, and sends gst_rate', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await user.click(screen.getByText('Sunglass'));

    const gst = screen.getByTestId('qa-gst-rate');
    expect(gst.tagName).not.toBe('INPUT');
    expect(gst).toHaveTextContent('18%');
    // The old rendering was a read-only box holding the sentence; none remains.
    expect(container.querySelector('input[readonly]')).toBeNull();

    fill(screen.getByLabelText(/^HSN Code/), '900311');
    expect(screen.getByTestId('qa-gst-rate')).not.toHaveTextContent('%');
    expect(screen.getByTestId('qa-gst-rate')).toHaveTextContent('900311');
    fill(screen.getByLabelText(/^HSN Code/), '900410');

    fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
    fill(screen.getByLabelText(/^Model No/), 'RB4165');
    fill(screen.getByLabelText(/^Colour Code/), '601');
    fill(screen.getByLabelText(/^MRP/), '7890');
    expect(screen.queryByTestId('qa-still-missing')).toBeNull();

    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
    const payload = createProduct.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.gst_rate).toBe(18);
    expect(payload.hsn_code).toBe('900410');
    // The posted shape is the one buildProductPayload has always produced.
    expect(Object.keys(payload).sort()).toEqual([
      'attributes', 'brand', 'category', 'cost_price', 'description', 'gst_rate',
      'hsn_code', 'images', 'model', 'mrp', 'offer_price', 'shopify', 'weight',
    ]);
  });
});

describe('6 - lock and confirm chips carry words', () => {
  it('variant mode says in text what is locked and what was copied (no hover needed)', async () => {
    renderPage('/catalog/add?variant=P-SRC');
    // brand + model + category are locked; lens size + MRP were copied.
    await waitFor(() =>
      expect(screen.getAllByText('locked · same as the model').length).toBeGreaterThanOrEqual(3),
    );
    expect(screen.getAllByText('copied — confirm or edit').length).toBeGreaterThanOrEqual(2);
    screen.getAllByText(/locked · same as the model|copied — confirm or edit/).forEach((chip) => {
      expect(chip).not.toHaveAttribute('title');
    });
  });
});

describe('8 - the review card uses registry labels', () => {
  it('names a filled attribute by the label the form itself shows, never the raw key', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByText('Sunglass'));
    fill(screen.getByLabelText(/^Frame Front Material/), 'Acetate');

    const card = screen.getByRole('heading', { name: 'Review' }).closest('.card')!;
    expect(within(card).getByText('Frame Front Material')).toBeInTheDocument();
    expect(within(card).getByText('Acetate')).toBeInTheDocument();
    expect(within(card).queryByText(/^frame material$/i)).toBeNull();
  });
});

describe('9 + 12 - the Online strip', () => {
  it('says in words what the POS switch waits on, and the tag box has a visible label', async () => {
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByText(/turn on Sync to Shopify first/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Shopify tags')).toBeNull();

    await user.click(screen.getByLabelText('Sync to Shopify'));
    expect(screen.queryByText(/turn on Sync to Shopify first/)).toBeNull();
    expect(screen.getByLabelText('Shopify tags')).toBeInTheDocument();
  });
});
