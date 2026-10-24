// ============================================================================
// Add product meets an item a manager already ordered: it is FINISHED, in this
// editor (audit C3; panel R1-83, R1-85)
// ============================================================================
// The duplicate popup leads with "Finish the ordered item" for a provisional
// draft, and the exact-match hint says "Finish it": both open the draft in the
// product editor (/catalog/add?edit=<id>), whose save puts its held stock on
// the shelf -- one destination rule, existingProductPath. Any other existing
// product still opens in the stock ledger.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Cat', roles: ['CATALOG_MANAGER'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const DRAFT = {
  product_id: 'P-DRAFT',
  sku: 'SGRAYBRB4165601',
  name: 'Ray-Ban RB4165',
  category: 'SUNGLASS',
  is_active: false,
  catalog_status: 'DRAFT',
  provisional: true,
};
const LIVE = { ...DRAFT, product_id: 'P-LIVE', provisional: false, is_active: true, catalog_status: 'ACTIVE' };

const createProduct = vi.fn();
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
    productApi: {
      getCategoryRegistry: vi.fn(async () => {
        throw new Error('offline');
      }),
      getBrandOptions: vi.fn(async () => ({ brands: [] })),
      getProduct: vi.fn(async () => DRAFT),
      createProduct: (...a: unknown[]) => createProduct(...a),
      updateProduct: vi.fn(async () => ({})),
      getWebsiteVerdict: vi.fn(async (brand: string) => ({ brand, online: false, reason: null })),
      previewSku: vi.fn(async () => ({ category: 'SUNGLASS', sku: 'SG-RAYBAN-RB4165-601' })),
    },
  };
});
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error {
    status = 0;
  },
  catalogProductsApi: {},
}));
const similar = vi.fn(() => ({ data: null, armed: false }));
vi.mock('../useSimilarProducts', () => ({ useSimilarProducts: () => similar() }));

import { QuickAddPage } from '../QuickAddPage';
import { DuplicateProductError, productApi } from '../../../services/api/products';

// Every address the page went to: the editor consumes ?edit= once it has
// loaded the product, so the last one alone would not show it.
let seen: string[] = [];
function Where() {
  const loc = useLocation();
  seen.push(loc.pathname + loc.search);
  return null;
}

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/catalog/add']}>
      <QuickAddPage />
      <Where />
    </MemoryRouter>,
  );

const fill = (el: HTMLElement, value: string) => fireEvent.change(el, { target: { value } });

async function typeTheSunglass() {
  const user = userEvent.setup();
  await user.click(screen.getByText('Sunglasses'));
  fill(screen.getByLabelText(/^Brand Name/), 'Ray-Ban');
  fill(screen.getByLabelText(/^Model No/), 'RB4165');
  fill(screen.getByLabelText(/^Colour Code/), '601');
  fill(screen.getByLabelText(/^MRP/), '7890');
  return user;
}

beforeEach(() => {
  seen = [];
  createProduct.mockReset();
  similar.mockReset();
  similar.mockReturnValue({ data: null, armed: false });
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
});

describe('an item a manager already ordered', () => {
  it('R1-83: the 409 popup\'s "Finish the ordered item" opens that draft in the editor', async () => {
    createProduct.mockRejectedValue(new DuplicateProductError('exists', DRAFT));
    renderPage();
    const user = await typeTheSunglass();
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    const finish = await screen.findByRole('button', { name: /Finish the ordered item/ });
    await user.click(finish);
    await waitFor(() => expect(seen).toContain('/catalog/add?edit=P-DRAFT'));
  });

  it('any other existing product still opens in the stock ledger', async () => {
    createProduct.mockRejectedValue(new DuplicateProductError('exists', LIVE));
    renderPage();
    const user = await typeTheSunglass();
    await user.click(screen.getByRole('button', { name: /Save product/ }));
    await user.click(await screen.findByRole('button', { name: /Open the existing product/ }));
    await waitFor(() => expect(seen).toContain(`/inventory/stock?search=${LIVE.sku}`));
    expect(seen).not.toContain('/catalog/add?edit=P-LIVE');
  });

  it('R1-85: the exact-match hint names the ordered draft and "Finish it" opens it', async () => {
    similar.mockReturnValue({
      data: { exact_match: DRAFT, siblings: [], model_colour_count: 1 },
      armed: true,
    });
    renderPage();
    const user = await typeTheSunglass();
    expect(screen.getByRole('alert')).toHaveTextContent(/ordered before it was catalogued/);
    await user.click(screen.getByRole('button', { name: 'Finish it' }));
    await waitFor(() => expect(seen).toContain('/catalog/add?edit=P-DRAFT'));
  });
});

// One-rule review: the editor's own item is never "similar" to itself. An
// ordered draft opened to be finished was told "Finish it instead of adding
// it again", and the link reloaded the editor over the unsaved edits.
describe('finishing an ordered draft in the editor', () => {
  const renderEditor = () => {
    vi.mocked(productApi.getProduct).mockResolvedValueOnce({
      ...DRAFT,
      category: 'SG',
      brand: 'Ray-Ban',
      model: 'RB4165',
      attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '601' },
    } as never);
    return render(
      <MemoryRouter initialEntries={['/catalog/add?edit=P-DRAFT']}>
        <QuickAddPage />
        <Where />
      </MemoryRouter>,
    );
  };

  it('never tells the draft being finished to finish itself', async () => {
    similar.mockReturnValue({
      data: { exact_match: DRAFT, siblings: [], model_colour_count: 1 },
      armed: true,
    });
    renderEditor();
    await waitFor(() => expect(screen.getByLabelText(/^Model No/)).toHaveValue('RB4165'));
    expect(screen.queryByText(/ordered before it was catalogued/)).toBeNull();
  });

  it('still names another ordered item with the same identity', async () => {
    similar.mockReturnValue({
      data: { exact_match: { ...DRAFT, product_id: 'P-OTHER' }, siblings: [], model_colour_count: 1 },
      armed: true,
    });
    renderEditor();
    expect(await screen.findByText(/ordered before it was catalogued/)).toBeTruthy();
  });
});
