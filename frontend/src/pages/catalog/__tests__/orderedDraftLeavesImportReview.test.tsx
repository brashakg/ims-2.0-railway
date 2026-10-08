// ============================================================================
// An item ordered on a PO before it was catalogued is finished in the PRODUCT
// editor, never the import review (panel 2026-09-29, audit C1)
// ============================================================================
// Its catalogue twin sits in Needs review (needs_review + spine_product_id)
// so the cataloguer finds it, but it already HAS its billing row: the import
// review's save wrote around the product door (no restamp, no release of the
// held stock) and its approve told the cataloguer to retire the real product.
//   1. ?review=<twin> hands over to ?edit=<spine> -- the editor whose save
//      puts the held units on the shelf.
//   2. The review queue's "next item" fallback asks the server for imports
//      only (ordered_draft=false); ordered drafts lead Needs review, so with
//      two of them Skip used to bounce between them forever.

import { render, screen, waitFor } from '@testing-library/react';
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

const SPINE = {
  product_id: 'P-SPINE',
  sku: 'FRBOSSBOSS1700C252',
  category: 'FR',
  brand: 'Boss',
  model: 'BOSS 1700',
  attributes: { brand_name: 'Boss', model_no: 'BOSS 1700', colour_code: 'C2', lens_size: '52' },
  mrp: 2990,
  images: [],
};
const getProduct = vi.fn(async () => SPINE);
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: {
    getCategoryRegistry: vi.fn(async () => {
      throw new Error('offline');
    }),
    getBrandOptions: vi.fn(async () => ({ brands: [] })),
    getProduct: (...a: unknown[]) => getProduct(...(a as [])),
    updateProduct: vi.fn(async () => ({})),
  },
}));
vi.mock('../../../services/api/productTemplates', () => ({
  productTemplatesApi: { list: vi.fn(async () => ({ templates: [] })) },
}));

const DOCS: Record<string, Record<string, unknown>> = {
  'CAT-ORDERED': {
    id: 'CAT-ORDERED',
    sku: 'FRBOSSBOSS1700C252',
    category: 'FRAME',
    needs_review: true,
    spine_product_id: 'P-SPINE',
    attributes: { brand_name: 'Boss', model_no: 'BOSS 1700', colour_code: 'C2' },
  },
  'CAT-IMPORT': {
    id: 'CAT-IMPORT',
    sku: 'BVI-1',
    category: 'FRAME',
    needs_review: true,
    attributes: { brand_name: 'Vogue', model_no: 'VO5286', colour_code: 'W44' },
  },
};
const list = vi.fn(async () => ({ products: [DOCS['CAT-IMPORT']], total: 1 }));
vi.mock('../../../services/api/catalog', () => ({
  CatalogRequestError: class CatalogRequestError extends Error {
    status = 0;
  },
  catalogProductsApi: {
    get: vi.fn(async (id: string) => DOCS[id]),
    list: (...a: unknown[]) => list(...(a as [])),
    promoteDryRun: vi.fn(async () => ({ ok: true, gaps: [], duplicate_warnings: [] })),
  },
}));
const hintSizes: string[] = [];
vi.mock('../SimilarProductsHint', () => ({
  SimilarProductsHint: (p: { size?: string }) => {
    hintSizes.push(p.size ?? '');
    return null;
  },
}));

import { QuickAddPage } from '../QuickAddPage';

let where = '';
function Where() {
  const loc = useLocation();
  where = loc.search;
  return null;
}

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <QuickAddPage />
      <Where />
    </MemoryRouter>,
  );

beforeEach(() => {
  where = '';
  getProduct.mockClear();
  hintSizes.length = 0;
  list.mockClear();
  window.sessionStorage.clear();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
});

describe('an ordered draft in Needs review', () => {
  it('opens in the product editor for its spine, not the import review', async () => {
    renderAt('/catalog/add?review=CAT-ORDERED');
    await waitFor(() => expect(where).toContain('edit=P-SPINE'));
    expect(where).not.toContain('review=');
    await waitFor(() => expect(getProduct).toHaveBeenCalledWith('P-SPINE'));
  });

  it('the similar-products hint reads a frame by its eye size, never an old stored size', async () => {
    // `size` left the frame registry; a legacy "52-18-140" can still sit
    // beside the eye size, and the server keys the frame by lens_size.
    getProduct.mockImplementation(async () => ({
      ...SPINE,
      attributes: { ...SPINE.attributes, size: '52-18-140' },
    }));
    try {
      renderAt('/catalog/add?edit=P-SPINE');
      await waitFor(() => expect(hintSizes).toContain('52'));
      expect(hintSizes).not.toContain('52-18-140');
    } finally {
      getProduct.mockImplementation(async () => SPINE);
    }
  });

  it("the review queue's next-item fallback asks for imports only", async () => {
    const user = userEvent.setup();
    renderAt('/catalog/add?review=CAT-IMPORT');
    const skip = await screen.findByRole('button', { name: /Skip/ });
    await user.click(skip);
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ ordered_draft: false }));
  });

  it('a review item that vanished falls forward to the next import, never an ordered draft', async () => {
    const api = await import('../../../services/api/catalog');
    (api.catalogProductsApi.get as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => {
      const gone = new api.CatalogRequestError('gone') as unknown as { status: number };
      gone.status = 404;
      throw gone;
    });
    renderAt('/catalog/add?review=CAT-GONE');
    await waitFor(() => expect(list).toHaveBeenCalled());
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ ordered_draft: false }));
  });
});
