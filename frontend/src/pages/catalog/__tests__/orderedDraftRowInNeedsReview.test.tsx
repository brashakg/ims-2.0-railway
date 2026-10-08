// ============================================================================
// Needs review: a manager's ordered-but-unfinished item is shown as what it is
// (panel round 4, audit C1)
// ============================================================================
// Its catalogue copy has needs_review + spine_product_id and NO is_active, so a
// chip read off is_active alone said "POS-ready" for an inactive, priceless
// draft whose receipt is still holding the units. The row and the drawer read
// the one rule (isOrderedDraft): badge "Ordered — finish it", no bulk-approve
// checkbox, it opens as its product (Edit -> ?edit=<spine>), never the import
// approve, and it offers no Clone or Order stock of an unfinished draft --
// whether the drawer holds the catalogue copy or the product itself.

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', name: 'Cat', roles: ['CATALOG_MANAGER'] },
    hasRole: () => false,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../components/shell/NavBadge', () => ({ refreshNavCounts: vi.fn() }));
// The same draft as its product: Needs review -> Edit -> a save that leaves a
// gap returns to /catalog?focus=<spine>, which opens THIS doc.
const SPINE = {
  product_id: 'P-SPINE',
  sku: 'FRBOSSBOSS1700C2',
  category: 'FR',
  brand: 'Boss',
  model: 'BOSS 1700',
  provisional: true,
  is_active: false,
  catalog_status: 'DRAFT',
  mrp: 2990,
};
vi.mock('../../../services/api/products', () => ({
  productApi: {
    getProducts: vi.fn(async () => ({ products: [], total: 0 })),
    getBrandOptions: vi.fn(async () => ({ brands: [] })),
    getProduct: vi.fn(async () => SPINE),
  },
  catalogApi: { getOnlineSummary: vi.fn(async () => ({ catalog: null })) },
}));

const ORDERED = {
  id: 'CAT-ORDERED',
  sku: 'FRBOSSBOSS1700C2',
  category: 'FRAME',
  needs_review: true,
  spine_product_id: 'P-SPINE',
  mrp: 2990,
  attributes: { brand_name: 'Boss', model_no: 'BOSS 1700', colour_code: 'C2', lens_size: '52' },
};
const IMPORT = {
  id: 'CAT-IMPORT',
  sku: 'BVI-1',
  category: 'FRAME',
  needs_review: true,
  attributes: { brand_name: 'Vogue', model_no: 'VO5286', colour_code: 'W44' },
};
vi.mock('../../../services/api/catalog', () => ({
  catalogProductsApi: {
    list: vi.fn(async () => ({ products: [ORDERED, IMPORT], total: 2 })),
    get: vi.fn(async () => IMPORT),
    promoteDryRun: vi.fn(async () => ({ ok: false, gaps: [], duplicate_warnings: [] })),
  },
}));

import { CatalogManagerPage } from '../CatalogManagerPage';

let where = '';
function Where() {
  where = useLocation().search;
  return null;
}

beforeEach(() => {
  where = '';
});

const renderReview = () =>
  render(
    <MemoryRouter initialEntries={['/catalog/review']}>
      <CatalogManagerPage segment="review" />
      <Where />
    </MemoryRouter>,
  );

describe('an ordered draft in Needs review', () => {
  it('its row says "Ordered — finish it" and cannot be bulk-approved', async () => {
    renderReview();
    const name = await screen.findByText('Boss BOSS 1700');
    const row = name.closest('tr') as HTMLElement;
    expect(within(row).getByText(/Ordered — finish it/)).toBeInTheDocument();
    expect(within(row).queryByText(/POS-ready/)).toBeNull();
    expect(within(row).queryByRole('checkbox')).toBeNull();
    // The import beside it is still a bulk-approvable review row.
    expect(screen.getByRole('checkbox', { name: 'Select Vogue VO5286' })).toBeInTheDocument();
  });

  it('opens as its product: not POS-ready, no Clone or Order stock, Edit goes to the spine', async () => {
    const user = userEvent.setup();
    renderReview();
    await user.click(await screen.findByText('Boss BOSS 1700'));
    const drawer = await screen.findByRole('dialog', { name: 'Boss BOSS 1700' });
    expect(within(drawer).getByText(/Ordered — finish it/)).toBeInTheDocument();
    expect(within(drawer).queryByText(/POS-ready/)).toBeNull();
    expect(within(drawer).queryByText(/Needs review/)).toBeNull();
    expect(within(drawer).queryByRole('button', { name: /Clone/ })).toBeNull();
    expect(within(drawer).queryByRole('button', { name: /Order stock/ })).toBeNull();
    await user.click(within(drawer).getByRole('button', { name: /^Edit$/ }));
    await waitFor(() => expect(where).toContain('edit=P-SPINE'));
  });

  // R1-99: the queue holds a manager's ordered drafts too, not only imports.
  it('the Needs-review tab is not labelled as imports only', async () => {
    renderReview();
    await screen.findByText('Boss BOSS 1700');
    const tab = screen.getByRole('link', { name: /^Needs review/ });
    expect(tab).not.toHaveTextContent(/imported/i);
  });

  it('opened as the product doc itself, it is still the unfinished ordered draft', async () => {
    render(
      <MemoryRouter initialEntries={['/catalog?focus=P-SPINE']}>
        <CatalogManagerPage segment="catalog" />
      </MemoryRouter>,
    );
    const drawer = await screen.findByRole('dialog', { name: 'Boss BOSS 1700' });
    expect(within(drawer).getByText(/Ordered — finish it/)).toBeInTheDocument();
    expect(within(drawer).queryByText(/Inactive/)).toBeNull();
    expect(within(drawer).queryByRole('button', { name: /Clone/ })).toBeNull();
    expect(within(drawer).queryByRole('button', { name: /Order stock/ })).toBeNull();
  });

  it('in the catalogue list it is the ordered draft, never a dimmed inactive row', async () => {
    const { productApi } = await import('../../../services/api/products');
    (productApi.getProducts as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      products: [SPINE],
      total: 1,
    });
    render(
      <MemoryRouter initialEntries={['/catalog']}>
        <CatalogManagerPage segment="catalog" />
      </MemoryRouter>,
    );
    const row = (await screen.findByText('Boss BOSS 1700')).closest('tr') as HTMLElement;
    expect(within(row).getByText(/Ordered — finish it/)).toBeInTheDocument();
    expect(within(row).queryByText(/Inactive/)).toBeNull();
    expect(row.className).not.toMatch(/opacity-60/);
  });
});
