// ============================================================================
// UI-03 (audit): the four catalogue figure cards sat as dashes with no word of
// why. The page now says when the figures could not be read and offers Retry;
// a successful read shows the figures and no notice.
// ============================================================================
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
import { MemoryRouter, Route, Routes } from 'react-router-dom';
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

import { catalogApi, productApi } from '../../../services/api/products';
import { CatalogManagerPage } from '../CatalogManagerPage';

const getOnlineSummary = catalogApi.getOnlineSummary as unknown as ReturnType<typeof vi.fn>;
const COUNTS = { in_catalog: 514, smartglasses: 3, own: 0, no_photo: 41, live: 60, pending: 2, needs_review: 4 };

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/catalog']}>
      <Routes><Route path="/catalog" element={<CatalogManagerPage />} /></Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  auth.roles = ['ADMIN'];
  (productApi.getProducts as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ products: [], total: 0, total_count: 0 });
  (productApi.getFacets as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ total: 0 });
  getOnlineSummary.mockReset();
});

describe('catalogue figure cards say when they could not be read (UI-03)', () => {
  it('the summary request fails: a notice with Retry, and Retry fills the cards', async () => {
    getOnlineSummary.mockRejectedValueOnce(new Error('Server error')).mockResolvedValue({ configured: true, reachable: true, catalog: COUNTS });
    renderPage();
    const notice = await screen.findByTestId('catalog-counts-unavailable');
    expect(notice).toHaveTextContent('could not be loaded');
    fireEvent.click(within(notice).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByTestId('catalog-counts-unavailable')).toBeNull());
    expect(within(screen.getByTestId('catalog-counts')).getByText('514')).toBeTruthy();
  });

  it('the server answers without the figures (its read failed): the same notice', async () => {
    getOnlineSummary.mockResolvedValue({ configured: false, reachable: false });
    renderPage();
    expect(await screen.findByTestId('catalog-counts-unavailable')).toHaveTextContent('could not be loaded');
  });

  it('positive control: figures read fine -> no notice', async () => {
    getOnlineSummary.mockResolvedValue({ configured: true, reachable: true, catalog: COUNTS });
    renderPage();
    await waitFor(() => expect(within(screen.getByTestId('catalog-counts')).getByText('514')).toBeTruthy());
    expect(screen.queryByTestId('catalog-counts-unavailable')).toBeNull();
  });
});
