// ============================================================================
// Catalog page: sort, filters, rows per page, flags, selection (2026-10-12)
// ============================================================================
// The page reads every choice from its ADDRESS and asks the SERVER (GET
// /products) for exactly that; a change writes the address and goes back to
// page 1; rows per page is remembered on this device; the ADDED column and
// the red flag chips come from the row; the red banner's "Show them" sets the
// Flags filter; who sees which bulk button follows the owner's role rulings.

// This Node/jsdom runner ships a partial localStorage: the repo's Map stand-in.
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => {
      m.set(k, String(v));
    },
    removeItem: (k: string) => {
      m.delete(k);
    },
    clear: () => {
      m.clear();
    },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() {
      return m.size;
    },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
  Object.defineProperty(window, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { roles: ['ADMIN'] as string[] };

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-me', roles: auth.roles },
    hasRole: (r: string | string[]) => {
      if (auth.roles.includes('SUPERADMIN') || auth.roles.includes('ADMIN')) return true;
      return (Array.isArray(r) ? r : [r]).some((x) => auth.roles.includes(x));
    },
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../components/shell/NavBadge', () => ({ refreshNavCounts: vi.fn() }));
vi.mock('../../../services/api/products', () => ({
  productApi: {
    getProducts: vi.fn(),
    getFacets: vi.fn(),
    getBrandOptions: vi.fn(async () => ({ brands: [] })),
    getProduct: vi.fn(),
  },
  catalogApi: { getOnlineSummary: vi.fn(async () => ({ catalog: null })) },
}));
vi.mock('../../../services/api/catalog', () => ({
  catalogProductsApi: { list: vi.fn(), get: vi.fn(), promote: vi.fn() },
  catalogBulkApi: { archive: vi.fn(), restore: vi.fn(), flag: vi.fn(), fixFlags: vi.fn() },
}));

import { productApi } from '../../../services/api/products';
import { CatalogManagerPage } from '../CatalogManagerPage';

const getProducts = productApi.getProducts as unknown as ReturnType<typeof vi.fn>;
const getFacets = productApi.getFacets as unknown as ReturnType<typeof vi.fn>;

const ROW = {
  product_id: 'P1',
  sku: 'SG-CAR-1',
  name: 'Carrera 8035 Black',
  brand: 'Carrera',
  category: 'SUNGLASS',
  mrp: 12990,
  is_active: true,
  has_photo: true,
  online: 'OFF',
  created_by: 'u-mansi',
  created_by_name: 'Mansi Kumari',
  created_at: '2026-10-10T10:35:00',
  flags: [
    {
      flag_id: 'f1', type: 'photo_redo', status: 'open', note: 'Front photo is 3rd',
      by_name: 'Avinash', at: '2026-10-11T05:00:00',
    },
    { flag_id: 'f2', type: 'price_check', status: 'fixed' },
  ],
};

const FACETS = {
  total: 481,
  brand: [{ value: 'Carrera', count: 120 }],
  frame_color: [{ value: 'Black', count: 179 }],
  created_by: [{ value: 'u-mansi', label: 'Mansi Kumari', count: 319 }],
  updated_by: [{ value: 'none', label: 'Never edited', count: 356 }],
  flags: { open: 14, mine: 8, by_type: { photo_redo: 9, price_check: 5 }, archived: 3 },
};

function Where() {
  return <output data-testid="where">{useLocation().search}</output>;
}

function renderAt(search = '') {
  return render(
    <MemoryRouter initialEntries={[`/catalog${search}`]}>
      <Routes>
        <Route
          path="/catalog"
          element={
            <>
              <CatalogManagerPage />
              <Where />
            </>
          }
        />
      </Routes>
    </MemoryRouter>
  );
}

const where = () => new URLSearchParams(screen.getByTestId('where').textContent || '');

beforeEach(() => {
  auth.roles = ['ADMIN'];
  getProducts.mockReset();
  getProducts.mockResolvedValue({ products: [ROW], total: 1, total_count: 38 });
  getFacets.mockReset();
  getFacets.mockResolvedValue(FACETS);
  try {
    window.localStorage.clear();
  } catch {
    /* jsdom always has it */
  }
});

describe('the address is the state', () => {
  it('asks the server for exactly what the address says', async () => {
    renderAt('?sort=name_asc&frame_color=Black&added_by=u-mansi&from=2026-10-01&to=2026-10-12&rows=25&page=2');
    await waitFor(() => expect(getProducts).toHaveBeenCalled());
    expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({
      sort: 'name_asc',
      frame_color: 'Black',
      created_by: 'u-mansi',
      date_on: 'added',
      date_from: '2026-10-01',
      date_to: '2026-10-12',
      is_active: 'true',
      skip: 25,
      limit: 25,
    });
  });

  it('a new sort is written to the address and goes back to page 1', async () => {
    renderAt('?page=3');
    await screen.findByTestId('catalog-table');
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'mrp_desc' } });
    await waitFor(() => expect(where().get('sort')).toBe('mrp_desc'));
    expect(where().get('page')).toBeNull();
    await waitFor(() =>
      expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ sort: 'mrp_desc', skip: 0 })
    );
  });

  it('rows per page: in the address, remembered on this device, back to page 1', async () => {
    renderAt('?page=2');
    await screen.findByTestId('catalog-table');
    fireEvent.change(screen.getByLabelText('Rows per page'), { target: { value: '100' } });
    await waitFor(() => expect(where().get('rows')).toBe('100'));
    expect(where().get('page')).toBeNull();
    expect(window.localStorage.getItem('ims.catalog.rowsPerPage')).toBe('100');
    await waitFor(() =>
      expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ limit: 100, skip: 0 })
    );
  });

  it('a remembered rows-per-page is used when the address has none', async () => {
    window.localStorage.setItem('ims.catalog.rowsPerPage', '200');
    renderAt('');
    await waitFor(() => expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ limit: 200 }));
  });

  it('options come from the server with counts; the result line reads plainly', async () => {
    renderAt('?frame_color=Black');
    await screen.findByText('Black (179)');
    expect(screen.getByText('Mansi Kumari (319)')).toBeTruthy();
    expect(await screen.findByTestId('result-line')).toHaveProperty(
      'textContent',
      '38 of 481 products, recently edited first'
    );
    const chips = screen.getByTestId('filter-chips');
    expect(within(chips).getByText('Frame colour: Black')).toBeTruthy();
    fireEvent.click(within(chips).getByText('Clear all'));
    await waitFor(() => expect(where().get('frame_color')).toBeNull());
  });

  it('a From after To is refused in plain words and not applied', async () => {
    renderAt('?gender=Men');
    await screen.findByTestId('catalog-table');
    // A More-filters choice in the address opens the panel by itself.
    const range = screen.getByTestId('date-range');
    fireEvent.change(within(range).getByLabelText('To'), { target: { value: '2026-10-05' } });
    await waitFor(() => expect(where().get('to')).toBe('2026-10-05'));
    fireEvent.change(within(range).getByLabelText('From'), { target: { value: '2026-10-09' } });
    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent',
      expect.stringContaining('The From date is after the To date')
    );
    expect(where().get('from')).toBeNull();
  });
});

describe('rows: the ADDED column and the flags', () => {
  it('shows who added it and when in IST, the open flag and its note', async () => {
    renderAt('');
    const added = await screen.findByTestId('added-cell');
    expect(added.textContent).toContain('Mansi Kumari');
    expect(added.textContent).toContain('4:05 pm');
    expect(added.textContent).toContain('Not edited since');
    expect(screen.getAllByTestId('flag-chip').map((c) => c.textContent?.trim())).toEqual([
      'Needs photo redo',
    ]);
    expect(screen.getByText('Note from Avinash: Front photo is 3rd')).toBeTruthy();
  });

  it("the banner counts from the server; a cataloguer's Show them picks their own", async () => {
    auth.roles = ['CATALOG_MANAGER'];
    renderAt('');
    const banner = await screen.findByTestId('flags-banner');
    expect(banner.textContent?.replace(/\s+/g, ' ')).toContain(
      '14 products are flagged as needing work - 8 of them are yours'
    );
    fireEvent.click(banner);
    await waitFor(() => expect(where().get('flag')).toBe('mine'));
  });

  it("anyone else's Show them picks every open flag", async () => {
    renderAt('');
    fireEvent.click(await screen.findByTestId('flags-banner'));
    await waitFor(() => expect(where().get('flag')).toBe('any'));
  });
});

describe('who sees which bulk button', () => {
  async function tickFirstRow() {
    await screen.findByTestId('catalog-table');
    fireEvent.click(screen.getByLabelText('Select Carrera 8035 Black'));
    return screen.findByTestId('selection-bar');
  }

  it('an admin can flag and archive', async () => {
    renderAt('');
    const bar = await tickFirstRow();
    expect(within(bar).getByText(/Flag 1 product/)).toBeTruthy();
    expect(within(bar).getByText('Delete (archive)')).toBeTruthy();
  });

  it('a store manager can flag but not archive', async () => {
    auth.roles = ['STORE_MANAGER'];
    renderAt('');
    const bar = await tickFirstRow();
    expect(within(bar).getByText(/Flag 1 product/)).toBeTruthy();
    expect(within(bar).queryByText('Delete (archive)')).toBeNull();
    expect(screen.queryByText('Add product')).toBeNull();
  });

  it('a catalogue manager gets no tick boxes (they clear flags, not raise them)', async () => {
    auth.roles = ['CATALOG_MANAGER'];
    renderAt('');
    await screen.findByTestId('catalog-table');
    expect(screen.queryByLabelText('Select all on this page')).toBeNull();
  });

  it('only admins are offered Archived; there the bar offers Restore', async () => {
    auth.roles = ['STORE_MANAGER'];
    const { unmount } = renderAt('');
    await screen.findByTestId('catalog-table');
    expect(screen.queryByText(/Archived \(deleted\)/)).toBeNull();
    unmount();
    auth.roles = ['ADMIN'];
    renderAt('?flag=archived');
    expect(await screen.findByText('Archived (deleted) (3)')).toBeTruthy();
    const bar = await tickFirstRow();
    expect(within(bar).getByText('Restore')).toBeTruthy();
    expect(within(bar).queryByText(/Flag 1 product/)).toBeNull();
  });
});
