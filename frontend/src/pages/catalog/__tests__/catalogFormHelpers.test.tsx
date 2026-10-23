// ============================================================================
// Add/Edit product - form fill helpers (owner-approved UX list, PR 3, 2026-10-23)
// ============================================================================
// 1. "Website still needs" = the push gate's own list (GET /products/website-
//    verdict?product_id=), shown for the saved product; never blocks a save.
// 2. "Copy specs from last product": blanks only, amber until confirmed; never
//    model, colour, barcode/GTIN.
// 3. AI description drafts ONCE in the background (empty box, colour typed),
//    never overwrites typed text; a spec change after drafting asks to
//    regenerate, never rewrites.
// 4. Soft warnings never block a save; lens 99 is valid, bridge 01 is not.

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
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const SOURCE_PRODUCT = {
  product_id: 'P-SRC', sku: 'SG-RAYBAN-RB4165-001', category: 'SG', brand: 'Ray-Ban', model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '001', lens_size: '54' },
  mrp: 7890, offer_price: 7890, hsn_code: '900410', gst_rate: 18, weight: 25, images: [],
  description: 'Stored paragraph.',
};
const getProduct = vi.fn(async (): Promise<Record<string, unknown>> => SOURCE_PRODUCT);
const createProduct = vi.fn(async () => ({ product_id: 'P-NEW', sku: 'SG-RAYBAN-RB4165-601' }));
const generateDescription = vi.fn(async () => ({ status: 'GENERATED', description: 'AI paragraph.' }));
const getWebsiteNeeds = vi.fn(async () => ({ needs: ['a photo'] as string[] | null, gaps: ['hsn_code'] }));
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: {
    getCategoryRegistry: vi.fn(async () => { throw new Error('offline'); }),
    getBrandOptions: vi.fn(async () => ({ brands: [{ name: 'Ray-Ban', subbrands: [], tier: 'PREMIUM' }] })),
    getProduct: (...a: unknown[]) => getProduct(...(a as [])),
    createProduct: (...a: unknown[]) => createProduct(...(a as [])),
    updateProduct: vi.fn(async () => ({})),
    previewSku: vi.fn(async () => ({ category: 'SUNGLASS', sku: 'SG-RAYBAN-RB4165-601' })),
    getWebsiteVerdict: vi.fn(async (brand: string) => ({ brand, online: true, reason: null })),
    getWebsiteNeeds: (...a: unknown[]) => getWebsiteNeeds(...(a as [])),
    generateDescription: (...a: unknown[]) => generateDescription(...(a as [])),
  },
}));
vi.mock('../../../services/api/inventory', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/inventory')>()),
  reorderApi: { setShopLevel: vi.fn(async () => ({})) },
}));
vi.mock('../SimilarProductsHint', () => ({ SimilarProductsHint: () => null }));
vi.mock('../useSimilarProducts', () => ({ useSimilarProducts: vi.fn() }));
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
const amber = (el: HTMLElement) => /(^|\s)ring-amber-400(\s|$)/.test(el.className);
const desc = () => screen.getByPlaceholderText('Optional product description…') as HTMLTextAreaElement;

async function sunglass(user: ReturnType<typeof userEvent.setup>, colour = '601') {
  await user.click(screen.getByText('Sunglass'));
  fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
  fill(screen.getByLabelText(/^Model No/), 'RB4165');
  if (colour) fill(screen.getByLabelText(/^Colour Code/), colour);
  fill(screen.getByLabelText(/^MRP/), '7890');
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  createProduct.mockClear();
  generateDescription.mockClear();
  getWebsiteNeeds.mockClear();
});

describe('Website still needs', () => {
  it('edit: lists the gate reasons and the fields, each field a jump chip', async () => {
    renderPage('/catalog/add?edit=P-SRC');
    const line = await screen.findByTestId('qa-website-needs');
    expect(getWebsiteNeeds).toHaveBeenCalledWith('P-SRC');
    expect(line).toHaveTextContent('As saved: website still needs');
    expect(line).toHaveTextContent('a photo');
    expect(screen.getByRole('button', { name: 'HSN Code' })).toBeInTheDocument();
  });

  it('edit: nothing missing says so; an unreadable answer is not shown as ready', async () => {
    getWebsiteNeeds.mockResolvedValueOnce({ needs: [], gaps: [] });
    const { unmount } = renderPage('/catalog/add?edit=P-SRC');
    expect(await screen.findByTestId('qa-website-needs')).toHaveTextContent('nothing more is needed');
    unmount();
    getWebsiteNeeds.mockResolvedValueOnce({ needs: null, gaps: [] });
    renderPage('/catalog/add?edit=P-SRC');
    expect(await screen.findByTestId('qa-website-needs')).toHaveTextContent('could not be checked');
  });
});

describe('Copy specs from last product', () => {
  it('after Save + New fills the blank repeating specs amber; never colour or model', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    fill(screen.getByLabelText(/^Warranty/), '1 Year');
    fill(screen.getByLabelText(/^Lens Size/), '54');
    fill(screen.getByLabelText(/^UPC/), '8056597000000');
    expect(screen.queryByTestId('qa-copy-last')).toBeNull();
    await user.click(screen.getByRole('button', { name: /Save \+ New/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
    // The just-saved product's website line (Add mode names its SKU).
    expect(await screen.findByTestId('qa-website-needs')).toHaveTextContent('Last saved (SG-RAYBAN-RB4165-601)');

    const chip = await screen.findByTestId('qa-copy-last');
    expect(chip).toHaveTextContent('2 fields');
    await user.click(chip);
    const warranty = screen.getByLabelText(/^Warranty/) as HTMLInputElement;
    expect(warranty.value).toBe('1 Year');
    expect(amber(warranty)).toBe(true);
    expect((screen.getByLabelText(/^Lens Size/) as HTMLInputElement).value).toBe('54');
    expect((screen.getByLabelText(/^Colour Code/) as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText(/^Model No/) as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText(/^UPC/) as HTMLInputElement).value).toBe('');
    expect(screen.queryByTestId('qa-copy-last')).toBeNull();
  });
});

describe('AI description drafts itself once', () => {
  it('drafts in the background when the box is empty and the colour is typed', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    await waitFor(() => expect(desc().value).toBe('AI paragraph.'), { timeout: 4000 });
    expect(generateDescription).toHaveBeenCalledTimes(1);
    // A spec change after drafting asks; it never rewrites.
    fill(screen.getByLabelText(/^Lens Size/), '56');
    expect(await screen.findByTestId('qa-desc-stale')).toHaveTextContent(
      'Specs changed since this was drafted - regenerate?',
    );
    expect(desc().value).toBe('AI paragraph.');
    expect(generateDescription).toHaveBeenCalledTimes(1);
  });

  it('never overwrites text typed while the draft was on its way', async () => {
    let answer: (v: { status: string; description: string }) => void = () => {};
    generateDescription.mockImplementationOnce(() => new Promise((r) => { answer = r; }));
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    await waitFor(() => expect(generateDescription).toHaveBeenCalledTimes(1), { timeout: 4000 });
    fill(desc(), 'My own words.');
    answer({ status: 'GENERATED', description: 'AI paragraph.' });
    await new Promise((r) => setTimeout(r, 50));
    expect(desc().value).toBe('My own words.');
  });

  it('does not draft without a colour, or in edit', async () => {
    const user = userEvent.setup();
    const { unmount } = renderPage();
    await sunglass(user, '');
    await new Promise((r) => setTimeout(r, 2000));
    expect(generateDescription).not.toHaveBeenCalled();
    unmount();
    // A stored product with an empty description and a colour: still no draft.
    getProduct.mockResolvedValueOnce({ ...SOURCE_PRODUCT, description: '' });
    renderPage('/catalog/add?edit=P-SRC');
    await screen.findByTestId('qa-website-needs');
    expect(desc().value).toBe('');
    await new Promise((r) => setTimeout(r, 2000));
    expect(generateDescription).not.toHaveBeenCalled();
  });
});

describe('Soft warnings never block', () => {
  it('bridge 01 warns, lens 99 does not, and the save still goes', async () => {
    const user = userEvent.setup();
    renderPage();
    await sunglass(user);
    fill(screen.getByLabelText(/^Lens Size/), '99');
    fill(screen.getByLabelText(/^Bridge Size/), '01');
    const box = await screen.findByTestId('qa-soft-warnings');
    expect(box).toHaveTextContent('Bridge 01 looks wrong');
    expect(box).not.toHaveTextContent('Lens size');
    expect(box).toHaveTextContent('No photos yet');
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await waitFor(() => expect(createProduct).toHaveBeenCalledTimes(1));
  });
});
