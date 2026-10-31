// S7 Stock today (owner approved 2026-10-23): one shop's stock jobs on one
// read-only page, the first link of the Stock & supply menu. The backend half
// is backend/tests/test_stock_today.py (every figure, the gate, the cost rule).
// Here: the page draws what the server sends - the stock value only when it
// was sent - and links each job only for a role its screen admits.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const getStockToday = vi.fn();
vi.mock('../../../services/api', () => ({ inventoryApi: { getStockToday: (...a: unknown[]) => getStockToday(...a) } }));
let roles: string[] = ['STORE_MANAGER'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { activeStoreId: 'S1', roles },
    hasRole: (want: string[]) => roles.includes('SUPERADMIN') || want.some((r) => roles.includes(r)),
  }),
}));

import StockTodayPage from '../StockTodayPage';
import { STOCK_TODAY_ROLES } from '../inventoryRoles';
import { PURCHASE_ROLES } from '../../purchase/purchaseRoles';
import { NAV_GROUPS } from '../../../components/shell/navConfig';
import { istDayString } from '../../../utils/datetime';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel: string) => readFileSync(path.resolve(here, rel), 'utf8');

const today = istDayString(new Date())!;
const DATA = {
  store_id: 'S1',
  store_name: 'Dhanbad',
  incoming: [{ transfer_id: 't1', from_store_name: 'Bokaro', pieces: 6, shipped_at: '2026-10-30T10:00:00Z' }],
  requests: [{ transfer_id: 't2', to_store_name: 'Pune', status: 'approved', pieces: 2, lines: [{ product_name: 'Ray-Ban RB3025', quantity: 2 }] }],
  count: { in_progress: [], counted_today: 0 },
  quarantine: { pieces: 3, unlabeled: 2 },
  deliveries: [
    { po_id: 'po1', po_number: 'PO-0412', vendor_id: 'V1', vendor_name: 'Safilo India', pieces_due: 11, expected_date: '2026-01-01' },
    { po_id: 'po2', po_number: 'PO-0418', vendor_id: 'V2', vendor_name: 'Luxottica India', pieces_due: 10, expected_date: today },
  ],
  stock_value: null as null | { total: number; uncosted_pieces: number },
  running_low: [{ product_id: 'RB', name: 'Ray-Ban RB3025 Aviator', store_id: 'S1', here: 0, other_shops: 3, sold_30d: 7 }],
  running_low_total: 1,
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <StockTodayPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Stock today: reachable', () => {
  it('is the first link of Stock & supply, gated like its route', () => {
    const group = NAV_GROUPS.find((g) => g.title === 'Stock & supply')!;
    expect(group.items[0]).toMatchObject({ label: 'Stock today', to: '/inventory/today' });
    expect([...(group.items[0].requireRoles ?? [])].sort()).toEqual([...STOCK_TODAY_ROLES].sort());
    const src = read('../../../routes/inventoryRoutes.tsx');
    expect(src).toMatch(/path="inventory\/today"[\s\S]*?allowedRoles=\{STOCK_TODAY_ROLES\}/);
  });

  it('admits the purchase roles - the server gate on GET /inventory/today', () => {
    expect([...STOCK_TODAY_ROLES].sort()).toEqual([...PURCHASE_ROLES].sort());
    expect(STOCK_TODAY_ROLES).toContain('ACCOUNTANT');
    expect(STOCK_TODAY_ROLES).not.toContain('SALES_STAFF');
  });

  it('the layout gate probes /inventory/today', () => {
    const src = read('../../../../../e2e/fixtures/routes.ts');
    const routes = src.slice(src.indexOf('export const ROUTES'), src.indexOf('export const EXCLUSIONS'));
    expect(routes).toMatch(/path:\s*'\/inventory\/today'/);
  });
});

describe('Stock today: the page', () => {
  beforeEach(() => {
    roles = ['STORE_MANAGER'];
    getStockToday.mockReset();
    getStockToday.mockResolvedValue({ ...DATA });
  });

  it("asks for the top-bar shop and draws each job", async () => {
    renderPage();
    expect(await screen.findByText('What needs you today')).toBeInTheDocument();
    expect(getStockToday).toHaveBeenCalledWith('S1');
    expect(screen.getByText('Pieces coming from Bokaro')).toBeInTheDocument();
    expect(screen.getByText(/Pune wants 2 × Ray-Ban RB3025/)).toBeInTheDocument();
    expect(screen.getByText('Due tonight')).toBeInTheDocument();
    expect(screen.getByText('2 pieces waiting, print red labels')).toBeInTheDocument();
    const row = screen.getByText('Ray-Ban RB3025 Aviator').closest('tr')!;
    expect(within(row).getAllByRole('cell').map((c) => c.textContent)).toEqual(['Ray-Ban RB3025 Aviator', '0', '3', '7']);
  });

  it('a manager gets no stock value card and a Receive button per order', async () => {
    renderPage();
    await screen.findByText('What needs you today');
    expect(screen.queryByText(/Stock value/)).not.toBeInTheDocument();
    const btn = within(screen.getByText('PO-0412').closest('tr')!).getByRole('link', { name: 'Receive this box' });
    expect(btn).toHaveAttribute('href', '/purchase/receive?vendor_id=V1&po_id=po1');
    expect(screen.getByText(/late · was/)).toBeInTheDocument();
    expect(screen.getByText('due today')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Scan in' })).toHaveAttribute('href', '/inventory/transfers');
    expect(screen.getByRole('link', { name: 'Start counting' })).toHaveAttribute('href', '/inventory/audit');
  });

  it('the accountant sees the stock value the server sent, and no doors it cannot open', async () => {
    roles = ['ACCOUNTANT'];
    getStockToday.mockResolvedValue({ ...DATA, stock_value: { total: 2500, uncosted_pieces: 1 } });
    renderPage();
    expect(await screen.findByText('₹2,500')).toBeInTheDocument();
    expect(screen.getByText(/1 piece has no cost yet, so this figure is too low/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Receive this box' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Scan in' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Start counting' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Reorder these' })).not.toBeInTheDocument();
  });

  it('an open count is continued, not started again', async () => {
    getStockToday.mockResolvedValue({
      ...DATA,
      count: { in_progress: [{ count_id: 'c1', zone: 'Sunglasses wall' }], counted_today: 0 },
    });
    renderPage();
    expect(await screen.findByText('Day-end count: Sunglasses wall')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Continue counting' })).toBeInTheDocument();
    expect(screen.queryByText('Due tonight')).not.toBeInTheDocument();
  });

  it('a section the server could not read is said, never shown as nothing to do', async () => {
    getStockToday.mockResolvedValue({ ...DATA, deliveries: [], running_low: [], unavailable: ['deliveries', 'running_low', 'transfers'] });
    renderPage();
    await screen.findByText('What needs you today');
    expect(screen.getAllByText(/Could not be read just now/)).toHaveLength(4);
    expect(screen.queryByText(/No purchase order is waiting/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Nothing here is at or under/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No box is on its way/)).not.toBeInTheDocument();
  });
});
