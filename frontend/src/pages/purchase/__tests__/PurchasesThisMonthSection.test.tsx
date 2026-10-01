// ============================================================================
// Audit F56 + F63: the Purchases this month screen
// ============================================================================
// The figures are the server's (GET /vendors/purchases-this-month, pinned in
// backend/tests/test_purchases_this_month.py). This pins the screen: it shows
// them with totals, asks for the picked month and the admin's picked shop (the
// one Purchase scope), and exports the same rows as CSV.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

let roles: string[] = ['ADMIN'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Owner', roles, activeStoreId: 'BV-PUN-01', storeIds: [] },
    hasRole: () => true,
  }),
}));

const exportToCSV = vi.hoisted(() => vi.fn());
vi.mock('../../../utils/exportUtils', () => ({ exportToCSV }));

const get = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/client')>();
  const fake = { get, post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };
  return { ...actual, default: fake, api: fake };
});

const REPORT = {
  month: '2026-09',
  vendors: [
    { vendor_id: 'v1', vendor_name: 'Jharkhand Optical', ordered: 10000, received: 8400, billed: 8400, paid: 2860, owed: 5540.4, next_due_date: '2026-09-09', next_due_overdue: true },
    { vendor_id: 'v2', vendor_name: 'Pune Lens Co', ordered: 0, received: 0, billed: 2240, paid: 0, owed: 2240, next_due_date: '2026-10-05', next_due_overdue: false },
    { vendor_id: 'v3', vendor_name: 'New Frames Co', ordered: 0, received: 0, billed: 0, paid: 1500, owed: -1500, next_due_date: null },
  ],
  totals: { ordered: 10000, received: 8400, billed: 10640, paid: 4360, owed: 6280.4 },
};

beforeEach(() => {
  get.mockReset();
  get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url === '/stores'
        ? { stores: [{ store_id: 'BV-DHN-01', store_name: 'Dhanbad' }] }
        : url === '/vendors/purchases-this-month' ? REPORT : {},
    }),
  );
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PurchasesThisMonthSection } from '../PurchasesThisMonthSection';
import { PurchaseShopPicker } from '../purchaseShop';

function open() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PurchaseShopPicker />
      <PurchasesThisMonthSection />
    </QueryClientProvider>,
  );
}

const reportParams = () =>
  get.mock.calls
    .filter(([url]) => url === '/vendors/purchases-this-month')
    .map(([, cfg]) => (cfg as { params: Record<string, string> }).params);

describe('Purchases this month', () => {
  it('shows each vendor and the totals exactly as the server sent them', async () => {
    roles = ['ADMIN'];
    open();
    const row = (await screen.findByText('Jharkhand Optical')).closest('tr')!;
    expect(within(row).getByText('₹5,540')).toBeInTheDocument();
    const total = screen.getByText('Total').closest('tr')!;
    expect(within(total).getByText('₹6,280')).toBeInTheDocument();
    expect(within(total).getByText('₹10,640')).toBeInTheDocument();
    // An admin opens on all stores: no store_id is sent.
    expect(reportParams()[0].store_id).toBeUndefined();
  });

  it('asks for the picked month and the picked shop', async () => {
    roles = ['ADMIN'];
    open();
    await screen.findByText('Jharkhand Optical');
    fireEvent.change(screen.getByLabelText('Month'), { target: { value: '2026-08' } });
    await waitFor(() => expect(reportParams().some((p) => p.month === '2026-08')).toBe(true));
    await screen.findByRole('option', { name: 'Dhanbad' });
    fireEvent.change(screen.getByLabelText('Purchase shop'), { target: { value: 'BV-DHN-01' } });
    await waitFor(() =>
      expect(reportParams().some((p) => p.month === '2026-08' && p.store_id === 'BV-DHN-01')).toBe(true),
    );
  });

  it('exports what the screen shows: the rows, the Total line, whole rupees, an advance as a number', async () => {
    roles = ['ADMIN'];
    exportToCSV.mockClear();
    open();
    await screen.findByText('Jharkhand Optical');
    fireEvent.click(screen.getByRole('button', { name: /export csv/i }));
    const [rows, , columns] = exportToCSV.mock.calls[0];
    expect(rows.map((r: { vendor_name: string; owed: number }) => [r.vendor_name, r.owed])).toEqual([
      ['Jharkhand Optical', 5540],
      ['Pune Lens Co', 2240],
      ['New Frames Co', -1500],
      ['Total', 6280],
    ]);
    expect(rows[0]).toMatchObject({ next_due_date: '2026-09-09', overdue: 'overdue' });
    expect(columns.map((c: { key: string }) => c.key)).toEqual(
      ['vendor_name', 'ordered', 'received', 'billed', 'paid', 'owed', 'next_due_date', 'overdue'],
    );
  });

  it('says an advance is an advance and marks an overdue next due', async () => {
    roles = ['ADMIN'];
    open();
    const advance = (await screen.findByText('New Frames Co')).closest('tr')!;
    expect(within(advance).getByText('₹1,500 advance')).toBeInTheDocument();
    const owed = screen.getByText('Jharkhand Optical').closest('tr')!;
    expect(within(owed).getByText('overdue')).toBeInTheDocument();
    const notYet = screen.getByText('Pune Lens Co').closest('tr')!;
    expect(within(notYet).queryByText('overdue')).toBeNull();
  });

  it('a cleared month box reads this month again instead of spinning', async () => {
    roles = ['ADMIN'];
    open();
    await screen.findByText('Jharkhand Optical');
    const box = screen.getByLabelText('Month') as HTMLInputElement;
    const current = box.value;
    fireEvent.change(box, { target: { value: '2026-08' } });
    await waitFor(() => expect(reportParams().some((p) => p.month === '2026-08')).toBe(true));
    fireEvent.change(box, { target: { value: '' } });
    await waitFor(() => expect(box.value).toBe(current));
    expect(await screen.findByText('Jharkhand Optical')).toBeInTheDocument();
  });

  it('an accountant gets no shop picker and reads his own shop', async () => {
    roles = ['ACCOUNTANT'];
    open();
    await screen.findByText('Jharkhand Optical');
    expect(screen.queryByLabelText('Purchase shop')).toBeNull();
    expect(reportParams()[0].store_id).toBe('BV-PUN-01');
  });
});
