// ============================================================================
// One online-stock page: the Online Store "Stock tally" retired into
// Inventory > Online stock (/inventory/online-sync).
// ============================================================================
// Fails if the old address stops forwarding (bookmarks 404), drops its query
// string, or if a bit the tally carried (SKU/name search, Held (POS) column,
// the honest "couldn't load" state) goes missing from the kept page.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { roles: ['SUPERADMIN'], activeRole: 'SUPERADMIN' }, isAuthenticated: true }),
}));
vi.mock('../../../services/api/onlineStock', () => ({
  onlineStockApi: { reconcile: vi.fn() },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: vi.fn().mockResolvedValue({ stores: [] }) },
}));
// Stable reference: the page's load() depends on the toast object.
const TOAST = { error: vi.fn(), success: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => TOAST }));

import { onlineStoreRoutes } from '../../../routes/onlineStoreRoutes';
import OnlineStockPage from '../OnlineStockPage';
import { onlineStockApi } from '../../../services/api/onlineStock';

function Where() {
  const { pathname, search } = useLocation();
  return <p>at {pathname}{search}</p>;
}

describe('the retired Stock tally address', () => {
  it('forwards to /inventory/online-sync with its query string', async () => {
    render(
      <MemoryRouter initialEntries={['/online-store/stock-tally?sku=ABC']}>
        <Routes>
          {onlineStoreRoutes}
          <Route path="/inventory/online-sync" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText('at /inventory/online-sync?sku=ABC')).toBeInTheDocument();
  });
});

const ROWS = {
  items: [
    { sku: 'SKU-A', name: 'Ray-Ban Aviator', in_store: 2, held: 1, online: 2, recommended: 2, delta: 0, status: 'OK' },
    { sku: 'SKU-B', name: 'Oakley Frog', in_store: 1, held: 0, online: 1, recommended: 1, delta: 0, status: 'OK' },
  ],
  summary: { ok: 2, safety_buffer: 0 },
  online_configured: true,
  listed_qty_live: true,
  live_listings_unknown: false,
};

describe('the kept online-stock page carries the tally bits', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows Held (POS) and searches by SKU or name', async () => {
    (onlineStockApi.reconcile as any).mockResolvedValue(ROWS);
    render(<OnlineStockPage />);
    fireEvent.click(await screen.findByLabelText(/show only at-risk/i)); // show every row
    expect(await screen.findByText('Held (POS)')).toBeInTheDocument();
    expect(screen.getByText('Oakley Frog')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search by SKU or name'), { target: { value: 'aviator' } });
    expect(await screen.findByText('Ray-Ban Aviator')).toBeInTheDocument();
    expect(screen.queryByText('Oakley Frog')).toBeNull();
    const row = screen.getByText('SKU-A').closest('tr')!;
    expect(row.querySelectorAll('td')[5].textContent).toBe('1'); // the held unit
  });

  it('a failed read says it could not load, never "No overselling risk"', async () => {
    (onlineStockApi.reconcile as any).mockRejectedValue(Object.assign(new Error('boom'), { response: { status: 500 } }));
    render(<OnlineStockPage />);
    expect(await screen.findByText(/Couldn't load online stock/)).toBeInTheDocument();
    expect(screen.queryByText(/No overselling risk/i)).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument());
  });
});
