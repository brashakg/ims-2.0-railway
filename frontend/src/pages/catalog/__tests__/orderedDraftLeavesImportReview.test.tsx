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
const getProduct = vi.fn(async (): Promise<Record<string, unknown>> => SPINE);
const updateProduct = vi.fn(async (..._args: unknown[]) => ({}));
vi.mock('../../../services/api/products', () => ({
  DuplicateProductError: class DuplicateProductError extends Error {},
  productApi: {
    getCategoryRegistry: vi.fn(async () => {
      throw new Error('offline');
    }),
    getBrandOptions: vi.fn(async () => ({ brands: [] })),
    getProduct: (...a: unknown[]) => getProduct(...(a as [])),
    updateProduct: (...a: unknown[]) => updateProduct(...a),
    previewSku: vi.fn(async () => ({ category: 'FRAME', sku: 'FR-BOSS-BOSS1700-C2-52' })),
    getWebsiteVerdict: vi.fn(async (brand: string) => ({ brand, online: true, reason: null })),
  },
}));

const DOCS: Record<string, Record<string, unknown>> = {
  'CAT-ORDERED': {
    id: 'CAT-ORDERED',
    sku: 'FRBOSSBOSS1700C252',
    category: 'FRAME',
    needs_review: true,
    spine_product_id: 'P-SPINE',
    ordered_draft: true,
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
  SimilarProductsHint: (p: { attributes?: Record<string, string> }) => {
    hintSizes.push(p.attributes?.lens_size ?? '');
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
  updateProduct.mockClear();
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

  it("the similar-products hint is sent the frame's eye size as typed", async () => {
    // `size` left the frame registry; a legacy "52-18-140" can still sit
    // beside the eye size. The hint sends the attributes as typed and the
    // server keys the frame by lens_size first (identity_parts, merge of
    // main #1174), so the eye size must reach it.
    getProduct.mockImplementation(async () => ({
      ...SPINE,
      attributes: { ...SPINE.attributes, size: '52-18-140' },
    }));
    try {
      renderAt('/catalog/add?edit=P-SPINE');
      await waitFor(() => expect(hintSizes).toContain('52'));
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

// Owner decision (round 11): an ordered draft -- typed on a PO, nothing of it
// shelved or sold -- keeps its category picker in the editor (a sunglass
// typed under the form's default FR is put right here, the server re-derives
// its HSN, rate and identity); only a finished product is locked, with the
// Clone advice.
// A brand both the frame and the sunglass pickers offer, so the save validates.
const VOGUE = {
  ...SPINE,
  brand: 'Vogue',
  model: 'VO 5286',
  attributes: { brand_name: 'Vogue', model_no: 'VO 5286', colour_code: 'W44', lens_size: '52' },
};

describe("the editor's category picker", () => {
  it('stays open on an ordered draft, and saving sends the category put right', async () => {
    getProduct.mockImplementation(async () => ({ ...VOGUE, ordered_draft: true }));
    try {
      const user = userEvent.setup();
      renderAt('/catalog/add?edit=P-SPINE');
      await waitFor(() => expect(getProduct).toHaveBeenCalledWith('P-SPINE'));
      const sunglass = await screen.findByRole('button', { name: /Sunglass/ });
      await waitFor(() => expect(sunglass).toBeEnabled());
      expect(screen.queryByText('locked while editing')).toBeNull();
      expect(screen.queryByText(/Clone as new SKU/)).toBeNull();
      await user.click(sunglass);
      await user.click(screen.getByRole('button', { name: /Save changes/ }));
      await waitFor(() => expect(updateProduct).toHaveBeenCalled());
      expect(updateProduct).toHaveBeenCalledWith(
        'P-SPINE',
        expect.objectContaining({ category: 'SG' }),
      );
    } finally {
      getProduct.mockImplementation(async () => SPINE);
    }
  });

  it('stays locked on a finished product, with the Clone advice, and sends no category', async () => {
    getProduct.mockImplementation(async () => VOGUE);
    try {
      const user = userEvent.setup();
      renderAt('/catalog/add?edit=P-SPINE');
      await screen.findByText('locked while editing');
      expect(screen.getByText(/Clone as new SKU/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Sunglass/ })).toBeDisabled();
      await user.click(screen.getByRole('button', { name: /Save changes/ }));
      await waitFor(() => expect(updateProduct).toHaveBeenCalled());
      expect(updateProduct.mock.calls[0][1]).not.toHaveProperty('category');
    } finally {
      getProduct.mockImplementation(async () => SPINE);
    }
  });
});
