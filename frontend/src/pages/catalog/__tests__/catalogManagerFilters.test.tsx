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
import { catalogBulkApi, catalogProductsApi } from '../../../services/api/catalog';
import { CatalogManagerPage } from '../CatalogManagerPage';
import { EDIT_QUEUE_KEY, backToQueueAddress, editListAddress, readReviewQueue } from '../reviewQueue';

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
      '38 models of 481 products, recently edited first'
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
    expect(within(bar).getByText(/Flag 1 item/)).toBeTruthy();
    expect(within(bar).getByText('Delete (archive)')).toBeTruthy();
  });

  it('a store manager can flag but not archive', async () => {
    auth.roles = ['STORE_MANAGER'];
    renderAt('');
    const bar = await tickFirstRow();
    expect(within(bar).getByText(/Flag 1 item/)).toBeTruthy();
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
    expect(within(bar).queryByText(/Flag 1 item/)).toBeNull();
  });

  // Review BLOCK 1 (#1273): hasRole answers true for every ADMIN, so the
  // page reads the SUPERADMIN role itself -- an ADMIN never sees the button.
  it.each([
    [['ADMIN'], 0],
    [['SUPERADMIN'], 2],
  ])('Delete permanently on an archived row and the bar: %s sees %i', async (roles, n) => {
    auth.roles = roles;
    getProducts.mockResolvedValue({
      products: [{ ...ROW, is_active: false, deleted_at: '2026-10-20T06:00:00' }],
      total: 1,
      total_count: 1,
    });
    renderAt('?flag=archived');
    const bar = await tickFirstRow();
    expect(within(bar).getByText('Restore')).toBeTruthy();
    expect(screen.queryAllByRole('button', { name: /Delete permanently/ })).toHaveLength(n);
    // The row keeps its Edit button beside it (review nit 10).
    expect(screen.getByRole('button', { name: /^Edit$/ })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Panel round: orders and paths the first tests did not walk
// ---------------------------------------------------------------------------

const flagApi = catalogBulkApi.flag as unknown as ReturnType<typeof vi.fn>;
const reviewList = catalogProductsApi.list as unknown as ReturnType<typeof vi.fn>;

describe('the date range in its natural order', () => {
  it('Last edited chosen BEFORE the days stays Last edited', async () => {
    renderAt('?gender=Men'); // a More-filters choice opens the panel
    await screen.findByTestId('catalog-table');
    const range = screen.getByTestId('date-range');
    fireEvent.change(within(range).getByLabelText('Date range'), { target: { value: 'edited' } });
    await waitFor(() => expect(where().get('date_on')).toBe('edited'));
    fireEvent.change(within(range).getByLabelText('From'), { target: { value: '2026-10-01' } });
    await waitFor(() => expect(where().get('from')).toBe('2026-10-01'));
    expect((within(range).getByLabelText('Date range') as HTMLSelectElement).value).toBe('edited');
    await waitFor(() =>
      expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({
        date_on: 'edited',
        date_from: '2026-10-01',
      })
    );
    expect(within(screen.getByTestId('filter-chips')).getByText('Edited from 1 Oct')).toBeTruthy();
  });
});

describe('rows per page back to the default', () => {
  it('a device that remembered 100 can choose 50 again', async () => {
    window.localStorage.setItem('ims.catalog.rowsPerPage', '100');
    renderAt('');
    await waitFor(() => expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ limit: 100 }));
    fireEvent.change(screen.getByLabelText('Rows per page'), { target: { value: '50' } });
    await waitFor(() =>
      expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ limit: 50, skip: 0 })
    );
    expect((screen.getByLabelText('Rows per page') as HTMLSelectElement).value).toBe('50');
    expect(window.localStorage.getItem('ims.catalog.rowsPerPage')).toBe('50');
    expect(where().get('rows')).toBe('50'); // the address always carries rows
  });
});

describe('the Needs review page shows only the filters it applies', () => {
  it('no chip for a filter that list never applied', async () => {
    reviewList.mockReset();
    reviewList.mockResolvedValue({ products: [], total: 0 });
    render(
      <MemoryRouter initialEntries={['/catalog/review?frame_color=Black&flag=any&brand=Oakley&sort=name_asc']}>
        <Routes>
          <Route path="/catalog/review" element={<CatalogManagerPage segment="review" />} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(reviewList).toHaveBeenCalled());
    expect(reviewList.mock.calls.at(-1)?.[0]).toMatchObject({ brand: 'Oakley', sort: 'name_asc' });
    const chips = await screen.findByTestId('filter-chips');
    expect(within(chips).getAllByText(/:/).map((c) => c.textContent)).toEqual(['Brand: Oakley']);
  });

  it('the review queue remembers the whole address for Back to queue', async () => {
    reviewList.mockReset();
    reviewList.mockResolvedValue({
      products: [{ id: 'IMP-1', sku: 'IMP-1', title: 'Imported thing', needs_review: true }],
      total: 120,
    });
    window.sessionStorage.clear();
    render(
      <MemoryRouter initialEntries={['/catalog/review?sort=name_asc&rows=25&page=3']}>
        <Routes>
          <Route path="/catalog/review" element={<CatalogManagerPage segment="review" />} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(readReviewQueue()?.ids).toEqual(['IMP-1']));
    const back = new URLSearchParams(
      backToQueueAddress(readReviewQueue(), 'IMP-1').split('?')[1]
    );
    expect([back.get('sort'), back.get('rows'), back.get('page'), back.get('focus')]).toEqual([
      'name_asc', '25', '3', 'IMP-1',
    ]);
  });
});

describe('the Flag dialog names who gets the tasks from the server', () => {
  it("the server's answer, not the row's adder", async () => {
    flagApi.mockReset();
    // The adder has left: the server's own rule sends it to a catalogue manager.
    flagApi.mockResolvedValue({
      results: [{ product_id: 'P1', status: 'done' }],
      tasks: [{ task_id: null, assigned_to: 'u-ak', assigned_to_name: 'Akshay Ovhal', product_count: 1 }],
    });
    renderAt('');
    await screen.findByTestId('catalog-table');
    fireEvent.click(screen.getByLabelText('Select Carrera 8035 Black'));
    fireEvent.click(within(await screen.findByTestId('selection-bar')).getByText(/Flag 1 item/));
    await waitFor(() =>
      expect(screen.getByTestId('tasks-go-to').textContent).toBe('Tasks go to: Akshay Ovhal (1 product)')
    );
    // The preview is a dry run of the same door: nothing written.
    expect(flagApi.mock.calls[0]).toEqual([['P1'], 'needs_revision', '', false]);
  });
});

// ---------------------------------------------------------------------------
// Panel round: typing a day, a message that outlives its value, Archived for
// non-admins, the review queue's own order
// ---------------------------------------------------------------------------

describe('typing a To date with the keyboard', () => {
  it('a half-typed year is kept in the box, never refused, applied once whole', async () => {
    renderAt('?gender=Men&from=2026-10-01');
    await screen.findByTestId('catalog-table');
    const to = within(screen.getByTestId('date-range')).getByLabelText('To') as HTMLInputElement;
    // What Chrome reports while 12/10/2026 is typed into the box, digit by digit.
    for (const partial of ['0002-10-12', '0020-10-12', '0202-10-12']) {
      fireEvent.change(to, { target: { value: partial } });
      expect(to.value).toBe(partial);
      expect(screen.queryByRole('alert')).toBeNull();
      expect(where().get('to')).toBeNull();
    }
    fireEvent.change(to, { target: { value: '2026-10-12' } });
    await waitFor(() => expect(where().get('to')).toBe('2026-10-12'));
    expect(where().get('from')).toBe('2026-10-01');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it("a refused range's message goes when its value goes (the chip's X)", async () => {
    renderAt('?gender=Men&from=2026-10-02&to=2026-10-11');
    await screen.findByTestId('catalog-table');
    const range = screen.getByTestId('date-range');
    fireEvent.change(within(range).getByLabelText('From'), { target: { value: '2026-10-20' } });
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(where().get('from')).toBe('2026-10-02');
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'mrp_desc' } });
    await waitFor(() => expect(where().get('sort')).toBe('mrp_desc'));
    fireEvent.click(screen.getByLabelText('Remove Added 2 Oct - 11 Oct filter'));
    await waitFor(() => expect(where().get('from')).toBeNull());
    expect(screen.queryByRole('alert')).toBeNull();
    expect((within(range).getByLabelText('From') as HTMLInputElement).value).toBe('');
  });
});

describe('Flags: Archived is the admins’ option only', () => {
  it.each([['CATALOG_MANAGER'], ['STORE_MANAGER']])(
    'a shared ?flag=archived falls back to not chosen for %s',
    async (role) => {
      auth.roles = [role];
      renderAt('?flag=archived');
      await screen.findByTestId('catalog-table');
      expect(getProducts.mock.calls.at(-1)?.[0].flag).toBeUndefined();
      expect(screen.queryByText(/Archived \(deleted\)/)).toBeNull();
      expect(screen.queryByText('Could not load the catalog.')).toBeNull();
    }
  );
});

describe('the Needs review queue keeps its newest-first order', () => {
  it('no Sort by in the address asks for newest first and says so', async () => {
    reviewList.mockReset();
    reviewList.mockResolvedValue({
      products: [{ id: 'IMP-1', sku: 'IMP-1', title: 'Imported thing', needs_review: true }],
      total: 1,
    });
    render(
      <MemoryRouter initialEntries={['/catalog/review']}>
        <Routes>
          <Route path="/catalog/review" element={<CatalogManagerPage segment="review" />} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(reviewList).toHaveBeenCalled());
    expect(reviewList.mock.calls.at(-1)?.[0]).toMatchObject({ sort: 'newest' });
    expect((await screen.findByTestId('result-line')).textContent).toContain('newest first');
    expect((screen.getByLabelText('Sort by') as HTMLSelectElement).value).toBe('newest');
  });
});

// ---------------------------------------------------------------------------
// Round 4: rows in every address, no page past the end, the review queue's
// positions follow rows per page
// ---------------------------------------------------------------------------

describe('a page number means the same rows on every device', () => {
  it('a plain address gets the rows this page shows', async () => {
    renderAt('?page=3');
    await waitFor(() => expect(where().get('rows')).toBe('50'));
    expect(where().get('page')).toBe('3');
    expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ skip: 100, limit: 50 });
  });

  it("a device's remembered size is written into the address", async () => {
    window.localStorage.setItem('ims.catalog.rowsPerPage', '200');
    renderAt('');
    await waitFor(() => expect(where().get('rows')).toBe('200'));
  });

  it("a shared link's rows win over this device's remembered size", async () => {
    window.localStorage.setItem('ims.catalog.rowsPerPage', '200');
    renderAt('?page=3&rows=50');
    await screen.findByTestId('catalog-table');
    expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ skip: 100, limit: 50 });
    expect(where().get('rows')).toBe('50');
  });
});

describe('a page past the end is never a dead end', () => {
  const sixty = async ({ skip }: { skip: number }) =>
    skip < 60 ? { products: [ROW], total: 1, total_count: 60 } : { products: [], total: 0, total_count: 60 };

  it('goes to the last page that has rows', async () => {
    getProducts.mockImplementation(sixty);
    renderAt('?page=5&rows=50');
    await waitFor(() => expect(where().get('page')).toBe('2'));
    await screen.findByTestId('catalog-table');
    expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ skip: 50, limit: 50 });
    expect(screen.queryByText('No products found')).toBeNull();
    expect(screen.getByLabelText('Rows per page')).toBeTruthy();
    expect(screen.getByTestId('result-line').textContent).toBe(
      '60 models of 481 products, recently edited first'
    );
  });

  it('after a bulk archive empties the last page, goes to the new last page', async () => {
    let total = 26;
    getProducts.mockImplementation(async ({ skip }: { skip: number }) =>
      skip < total
        ? { products: [ROW], total: 1, total_count: total }
        : { products: [], total: 0, total_count: total }
    );
    const archive = catalogBulkApi.archive as unknown as ReturnType<typeof vi.fn>;
    archive.mockReset();
    archive.mockImplementation(async (ids: string[], apply: boolean) => {
      if (apply) total = 25;
      return {
        applied: apply,
        results: ids.map((id) => ({ product_id: id, status: apply ? 'done' : 'ready' })),
      };
    });
    renderAt('?page=2&rows=25');
    await screen.findByTestId('catalog-table');
    fireEvent.click(screen.getByLabelText('Select Carrera 8035 Black'));
    fireEvent.click(within(await screen.findByTestId('selection-bar')).getByText('Delete (archive)'));
    fireEvent.click(await screen.findByRole('button', { name: /Delete \(archive\) 1 item/ }));
    await waitFor(() => expect(where().get('page')).toBeNull());
    await waitFor(() =>
      expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({ skip: 0, limit: 25 })
    );
    expect(await screen.findByTestId('catalog-table')).toBeTruthy();
  });

  it("a late answer to an older request never moves the page a newer choice set", async () => {
    let lateAnswer: (v: unknown) => void = () => {};
    getProducts.mockImplementation(({ skip }: { skip: number }) =>
      skip >= 60
        ? new Promise((resolve) => {
            lateAnswer = resolve;
          })
        : Promise.resolve({ products: [ROW], total: 1, total_count: 60 })
    );
    renderAt('?page=5&rows=50');
    await waitFor(() => expect(getProducts).toHaveBeenCalledWith(expect.objectContaining({ skip: 200 })));
    // Sort by changes before the page-5 answer arrives: page 1 of the new sort.
    fireEvent.change(screen.getByLabelText('Sort by'), { target: { value: 'name_asc' } });
    await screen.findByTestId('catalog-table');
    expect(where().get('page')).toBeNull();
    lateAnswer({ products: [], total: 0, total_count: 60 });
    await new Promise((r) => setTimeout(r, 0));
    await waitFor(() => expect(where().get('sort')).toBe('name_asc'));
    expect(where().get('page')).toBeNull();
    expect(screen.getByTestId('catalog-table')).toBeTruthy();
  });

  it('a late unfiltered answer never shows under the filter chips', async () => {
    const late: Array<(v: unknown) => void> = [];
    const BLACK = { ...ROW, product_id: 'P2', sku: 'SG-RB-2', name: 'Ray-Ban 3025 Black' };
    const OLD = { ...ROW, product_id: 'P9', sku: 'SG-OLD-9', name: 'Unfiltered old row' };
    getProducts.mockImplementation((p: { frame_color?: string }) =>
      p.frame_color === 'Black'
        ? Promise.resolve({ products: [BLACK], total: 1, total_count: 179 })
        : new Promise((resolve) => {
            late.push(resolve);
          })
    );
    renderAt('');
    await screen.findByText('Black (179)');
    fireEvent.click(screen.getByLabelText('Frame colour'));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Black (179)' }));
    await screen.findByText('Ray-Ban 3025 Black');
    // The older, unfiltered requests answer last.
    expect(late.length).toBeGreaterThan(0);
    late.forEach((resolve) => resolve({ products: [OLD], total: 1, total_count: 481 }));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText('Unfiltered old row')).toBeNull();
    expect(screen.getByText('Ray-Ban 3025 Black')).toBeTruthy();
    expect(within(screen.getByTestId('filter-chips')).getByText('Frame colour: Black')).toBeTruthy();
    expect(screen.getByTestId('result-line').textContent).toMatch(/^179 models of /);
  });

  it('the pages and Rows per page stay whenever the list has rows to page through', async () => {
    getProducts.mockResolvedValue({ products: [], total: 0, total_count: 5 });
    renderAt('');
    expect(await screen.findByLabelText('Rows per page')).toBeTruthy();
  });
});

describe('an archived row on Needs review is never approved', () => {
  it('gets no tick box, so the bulk Approve never sends it', async () => {
    reviewList.mockReset();
    reviewList.mockResolvedValue({
      products: [
        { id: 'IMP-1', sku: 'IMP-1', title: 'Imported one', needs_review: true },
        { id: 'IMP-2', sku: 'IMP-2', title: 'Archived two', needs_review: true, is_active: false,
          deleted_at: '2026-10-12T05:00:00+00:00' },
      ],
      total: 2,
    });
    const promote = catalogProductsApi.promote as unknown as ReturnType<typeof vi.fn>;
    promote.mockReset();
    promote.mockResolvedValue({ sku: 'IMP-1' });
    render(
      <MemoryRouter initialEntries={['/catalog/review']}>
        <Routes>
          <Route path="/catalog/review" element={<CatalogManagerPage segment="review" />} />
        </Routes>
      </MemoryRouter>
    );
    expect(await screen.findByLabelText('Select Imported one')).toBeTruthy();
    expect(screen.queryByLabelText('Select Archived two')).toBeNull();
    fireEvent.click(screen.getByLabelText('Select all on this page'));
    expect((screen.getByLabelText('Select all on this page') as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Approve for POS/ }));
    await waitFor(() => expect(promote).toHaveBeenCalledTimes(1));
    expect(promote).toHaveBeenCalledWith('IMP-1');
  });
});

describe('the Needs review queue follows rows per page', () => {
  it('asks for that many rows and keeps their place for the editor', async () => {
    reviewList.mockReset();
    reviewList.mockResolvedValue({
      products: [
        { id: 'IMP-1', sku: 'IMP-1', title: 'Imported one', needs_review: true },
        { id: 'IMP-2', sku: 'IMP-2', title: 'Imported two', needs_review: true },
      ],
      total: 120,
    });
    window.sessionStorage.clear();
    render(
      <MemoryRouter initialEntries={['/catalog/review?rows=25&page=2']}>
        <Routes>
          <Route path="/catalog/review" element={<CatalogManagerPage segment="review" />} />
          <Route path="/catalog/add" element={<output data-testid="editor" />} />
        </Routes>
      </MemoryRouter>
    );
    await waitFor(() => expect(readReviewQueue()?.ids).toEqual(['IMP-1', 'IMP-2']));
    expect(reviewList.mock.calls.at(-1)?.[0]).toMatchObject({ page: 2, limit: 25 });
    expect(readReviewQueue()?.offset).toBe(25);
    // The row's Edit opens the editor with the queue at that row: item 27 of 120.
    fireEvent.click(screen.getAllByRole('button', { name: /Edit/ })[1]);
    await screen.findByTestId('editor');
    expect([readReviewQueue()?.index, readReviewQueue()?.offset]).toEqual([1, 25]);
  });
});

describe('G1 - one row per model (owner 2026-10-18)', () => {
  const MODEL = {
    ...ROW,
    flags: [],
    family: {
      lead_id: 'P1',
      count: 3,
      members: [
        { product_id: 'P1', sku: 'SG-CAR-1', color: 'Black (807)', size: '55', mrp: 12990, is_active: true, is_lead: true },
        { product_id: 'P2', sku: 'SG-CAR-2', color: 'Gold (J5G)', size: '55', mrp: 13990, is_active: true, is_lead: false },
        { product_id: 'P3', sku: 'SG-CAR-3', color: 'Gold (J5G)', size: '58', mrp: 13990, is_active: false, is_lead: false },
      ],
    },
  };

  it('asks for models, shows the colour/size chips, and expands to the variants', async () => {
    getProducts.mockResolvedValue({ products: [MODEL], total: 1, total_count: 1 });
    renderAt('?brand=Carrera');
    await screen.findByText('Carrera 8035 Black');
    // every filter still travels; the list is grouped by model
    expect(getProducts).toHaveBeenCalledWith(expect.objectContaining({ group: 'model', brand: 'Carrera' }));
    const chips = screen.getByTestId('family-chips');
    expect(chips).toHaveTextContent('3 variants');
    expect(chips).toHaveTextContent('Gold (J5G) · 58');
    expect(screen.queryAllByTestId('family-member-row')).toHaveLength(0);
    fireEvent.click(screen.getByTestId('family-toggle'));
    const rows = screen.getAllByTestId('family-member-row');
    expect(rows).toHaveLength(3);
    expect(rows[2]).toHaveTextContent('SG-CAR-3');
    expect(rows[2]).toHaveTextContent('(off)');
    expect(within(rows[0]).getByText('model')).toBeTruthy();
    expect(screen.getByTestId('result-line').textContent).toMatch(/^1 model of 481 products/);
  });

  it('ticking a model selects all its colours/sizes; each variant has its own tick', async () => {
    getProducts.mockResolvedValue({ products: [MODEL], total: 1, total_count: 1 });
    renderAt();
    await screen.findByText('Carrera 8035 Black');
    fireEvent.click(screen.getByLabelText('Select all 3 items of Carrera 8035 Black'));
    const bar = await screen.findByTestId('selection-bar');
    expect(bar).toHaveTextContent('3 items selected');
    expect(within(bar).getByText(/Flag 3 items/)).toBeTruthy();
    fireEvent.click(screen.getByTestId('family-toggle'));
    const rows = screen.getAllByTestId('family-member-row');
    fireEvent.click(within(rows[1]).getByRole('checkbox'));
    expect(screen.getByTestId('selection-bar')).toHaveTextContent('2 items selected');
    const modelBox = screen.getByLabelText('Select all 3 items of Carrera 8035 Black') as HTMLInputElement;
    expect(modelBox.checked).toBe(false);
    expect(modelBox.indeterminate).toBe(true);
    // the page box counts items too
    fireEvent.click(screen.getByLabelText('Select all on this page'));
    expect(screen.getByTestId('selection-bar')).toHaveTextContent('3 items selected');
  });

  it('shows each variant own editor and never borrows the model edit', async () => {
    getProducts.mockResolvedValue({ products: [{
      ...MODEL, updated_by: 'lead-editor', updated_by_name: 'Lead Editor', last_edited_at: '2026-10-23T10:00:00Z',
      family: { ...MODEL.family, members: MODEL.family.members.map((m, i) => ({
        ...m, created_at: '2026-10-01T10:00:00Z',
        ...(i === 1 ? { updated_by: 'child-editor', updated_by_name: 'Child Editor', last_edited_at: '2026-10-22T10:00:00Z' } : {}),
      })) },
    }], total: 1, total_count: 1 });
    renderAt();
    fireEvent.click(await screen.findByTestId('family-toggle'));
    const rows = screen.getAllByTestId('family-member-row');
    expect(rows[1]).toHaveTextContent('Edited by Child Editor');
    expect(rows[1]).not.toHaveTextContent('Lead Editor');
    expect(rows[2]).toHaveTextContent('Not edited since');
    expect(rows[2]).not.toHaveTextContent('Edited by');
  });
});

describe('Save & next: the Catalog list hands the edit page its products', () => {
  it('stashes the rows in the order shown, with the list query that made them', async () => {
    getProducts.mockResolvedValue({
      products: [
        { ...ROW, product_id: 'P-B', name: 'B' },
        { ...ROW, product_id: 'P-A', name: 'A' },
        { ...ROW, product_id: 'P-C', name: 'C' },
      ],
      total: 3,
      total_count: 90,
    });
    renderAt('?sort=name_asc&brand=Carrera&rows=25&page=2');
    await screen.findByTestId('catalog-table');
    await waitFor(() => expect(readReviewQueue(EDIT_QUEUE_KEY)?.ids).toEqual(['P-B', 'P-A', 'P-C']));
    const stash = readReviewQueue(EDIT_QUEUE_KEY)!;
    expect(stash).toMatchObject({ offset: 25, total: 90 });
    // The very params the list asked with (no skip/limit), for the next page.
    const asked = { ...getProducts.mock.calls.at(-1)?.[0] };
    delete asked.skip;
    delete asked.limit;
    expect(stash.list).toEqual({ path: '/catalog', params: asked, pageSize: 25 });
    expect(stash.list?.params).toMatchObject({ sort: 'name_asc', brand: 'Carrera' });
    // Plain Save comes back to this list as it was, the product in view.
    const back = new URLSearchParams(editListAddress(stash, 'P-A').split('?')[1]);
    expect(back.get('sort')).toBe('name_asc');
    expect(back.get('page')).toBe('2');
    expect(back.get('focus')).toBe('P-A');
  });
});

describe('Added by and Last edited by: tick boxes on the main row (owner 2026-11-01)', () => {
  const PEOPLE_FACETS = {
    ...FACETS,
    created_by: [
      { value: 'u-mansi', label: 'Mansi Kumari', count: 319 },
      { value: 'u-durga', label: 'Durga Devi', count: 41 },
    ],
    updated_by: [
      { value: 'u-ak', label: 'Akshay Ovhal', count: 2 },
      { value: 'none', label: 'Never edited', count: 356 },
    ],
  };

  it('ticks people in both, asks the server for exactly them, and names them in one chip each', async () => {
    getFacets.mockResolvedValue(PEOPLE_FACETS);
    renderAt('?page=3');
    await screen.findByTestId('catalog-table');
    // Both visible without opening More filters.
    expect(screen.queryByTestId('more-filters')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Added by' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Mansi Kumari (319)' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Durga Devi (41)' }));
    fireEvent.click(screen.getByRole('button', { name: 'Last edited by' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Akshay Ovhal (2)' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Never edited (356)' }));
    await waitFor(() => expect(where().get('edited_by')).toBe('u-ak,none'));
    expect(where().get('added_by')).toBe('u-mansi,u-durga');
    expect(where().get('page')).toBeNull();
    await waitFor(() =>
      expect(getProducts.mock.calls.at(-1)?.[0]).toMatchObject({
        created_by: 'u-mansi,u-durga',
        updated_by: 'u-ak,none',
        skip: 0,
      })
    );
    const chips = screen.getByTestId('filter-chips');
    expect(within(chips).getByText('Added by: Mansi Kumari, Durga Devi')).toBeTruthy();
    expect(within(chips).getByText('Last edited by: Akshay Ovhal, Never edited')).toBeTruthy();
  });

  it('an old single-value link still ticks its one person', async () => {
    getFacets.mockResolvedValue(PEOPLE_FACETS);
    renderAt('?added_by=u-durga');
    await screen.findByTestId('catalog-table');
    expect(getProducts.mock.calls.at(-1)?.[0].created_by).toBe('u-durga');
    fireEvent.click(screen.getByRole('button', { name: 'Added by' }));
    const durga = (await screen.findByRole('checkbox', { name: 'Durga Devi (41)' })) as HTMLInputElement;
    expect(durga.checked).toBe(true);
    expect(within(screen.getByTestId('filter-chips')).getByText('Added by: Durga Devi')).toBeTruthy();
  });

  it('category chips tick several at once', async () => {
    renderAt('');
    await screen.findByTestId('catalog-table');
    const chipRow = screen.getByRole('button', { name: 'All' }).parentElement as HTMLElement;
    const [first, second] = within(chipRow).getAllByRole('button').slice(1);
    fireEvent.click(first);
    fireEvent.click(second);
    await waitFor(() => expect(where().get('category')?.split(',')).toHaveLength(2));
    expect(first.getAttribute('aria-pressed')).toBe('true');
    expect(second.getAttribute('aria-pressed')).toBe('true');
  });
});
