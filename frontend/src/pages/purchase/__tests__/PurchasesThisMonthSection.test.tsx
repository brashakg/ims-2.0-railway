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

  it('rounds a figure ending in 50 paise one way on screen, in the CSV and on the Total line', async () => {
    // Round 3, problem 9: an advance of 1500.50 read "Paid Rs 1,501 / Rs 1,501
    // advance" on screen but "Advance Frames,0,0,0,1501,-1500" in the CSV,
    // because Math.round(-1500.5) is -1500. One rule now: the size rounds half
    // away from zero and the sign goes back on.
    roles = ['ADMIN'];
    exportToCSV.mockClear();
    const half = {
      month: '2026-09',
      vendors: [
        { vendor_id: 'v4', vendor_name: 'Advance Frames', ordered: 0, received: 0, billed: 0, paid: 1500.5, owed: -1500.5, next_due_date: null },
        { vendor_id: 'v5', vendor_name: 'Half Rupee Lens', ordered: 2500.5, received: 2500.5, billed: 2500.5, paid: 1500, owed: 1000.5, next_due_date: '2026-10-05', next_due_overdue: false },
        { vendor_id: 'v6', vendor_name: 'Small Change Co', ordered: 0, received: 0, billed: 0, paid: 0.5, owed: -0.5, next_due_date: null },
      ],
      totals: { ordered: 2500.5, received: 2500.5, billed: 2500.5, paid: 3001, owed: -500.5 },
    };
    get.mockImplementation((url: string) =>
      Promise.resolve({
        data: url === '/stores'
          ? { stores: [{ store_id: 'BV-DHN-01', store_name: 'Dhanbad' }] }
          : url === '/vendors/purchases-this-month' ? half : {},
      }),
    );
    open();

    const advance = (await screen.findByText('Advance Frames')).closest('tr')!;
    expect(within(advance).getByText('₹1,501')).toBeInTheDocument();
    expect(within(advance).getByText('₹1,501 advance')).toBeInTheDocument();
    const positive = screen.getByText('Half Rupee Lens').closest('tr')!;
    expect(within(positive).getAllByText('₹2,501')).toHaveLength(3);
    expect(within(positive).getByText('₹1,001')).toBeInTheDocument();
    const small = screen.getByText('Small Change Co').closest('tr')!;
    expect(within(small).getByText('₹1 advance')).toBeInTheDocument();
    const total = screen.getByText('Total').closest('tr')!;
    expect(within(total).getByText('₹501 advance')).toBeInTheDocument();
    // Paid rows read 1,501 + 1,500 + 1: the Total line adds them as shown
    // (3,002). This pin used to say 3,001 -- the server's exact 3001.00 --
    // which is the review's #7/#33: a Total that is not the sum of its rows.
    expect(within(total).getByText('₹3,002')).toBeInTheDocument();

    // The real CSV text, from the real writer, for the rows the screen exported.
    fireEvent.click(screen.getByRole('button', { name: /export csv/i }));
    const [rows, , columns] = exportToCSV.mock.calls[0];
    const { toCSV } = await vi.importActual<typeof import('../../../utils/exportUtils')>(
      '../../../utils/exportUtils',
    );
    const csv = toCSV(rows, columns).split('\n');
    expect(csv.slice(1)).toEqual([
      '"Advance Frames",0,0,0,1501,-1501,"",""',
      '"Half Rupee Lens",2501,2501,2501,1500,1001,"2026-10-05",""',
      '"Small Change Co",0,0,0,1,-1,"",""',
      '"Total",2501,2501,2501,3002,-501,"",""',
    ]);

    // Every figure cell on screen, Total line included, says the CSV's number
    // ("Rs 1,501 advance" is -1501), and the columns come in the screen's order
    // under the screen's names.
    const figure = (text: string) => {
      const m = /^₹([\d,-]+)( advance)?$/.exec(text.trim());
      if (!m) return `unreadable: ${text}`;
      const digits = m[1].replace(/,/g, '');
      return m[2] ? `-${digits}` : digits;
    };
    const table = screen.getByRole('table');
    const onScreen = within(table).getAllByRole('row').slice(1).map((tr) => {
      const cells = within(tr).getAllByRole('cell').map((td) => td.textContent ?? '');
      return [cells[0], ...cells.slice(1, 6).map(figure)].join(',');
    });
    const inCsv = csv.slice(1).map((l) => l.split(',').slice(0, 6).join(',').replace(/"/g, ''));
    expect(onScreen).toEqual(inCsv);
    const heads = within(table).getAllByRole('columnheader').map((th) => th.textContent ?? '');
    const labels = columns.map((c: { label: string }) => c.label);
    expect(heads.map((h, i) => labels[i].startsWith(h))).toEqual(heads.map(() => true));
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

// ============================================================================
// Review r1 (#7/#33, #8/#32, #9/#36, #4, #35): what the page says about its own
// figures comes from the body -- the Total adds the rows as shown, owed is as
// at the body's day, the shop is the body's, and the note names only views
// this login can open.
// ============================================================================

const STORES = [
  { store_id: 'BV-DHN-01', store_name: 'Dhanbad' },
  { store_id: 'BV-PUN-01', store_name: 'WizOpt Pune' },
];

function serve(report: Record<string, unknown>) {
  get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url === '/stores' ? { stores: STORES } : url === '/vendors/purchases-this-month' ? report : {},
    }),
  );
}

const footnote = () => screen.getByText(/Billed, paid and owed are the supplier ledger/).textContent ?? '';
const caption = () => screen.getByText(/Owed as at/).textContent ?? '';

describe('Purchases this month says what its figures are', () => {
  it('#7/#33: the Total line is the sum of the rows as shown, on screen and in the CSV', async () => {
    roles = ['ADMIN'];
    exportToCSV.mockClear();
    serve({
      month: '2026-09',
      store_id: null,
      as_of: '2026-09-30',
      vendors: [
        { vendor_id: 'a', vendor_name: 'Paise One', ordered: 0, received: 0, billed: 1000.6, paid: 0, owed: 1000.6, next_due_date: null },
        { vendor_id: 'b', vendor_name: 'Paise Two', ordered: 0, received: 0, billed: 2000.6, paid: 0, owed: 2000.6, next_due_date: null },
      ],
      // The exact sums: 3001.20 each, which rounds to 3,001 -- a rupee short
      // of the 1,001 + 2,001 a reader adds up.
      totals: { ordered: 0, received: 0, billed: 3001.2, paid: 0, owed: 3001.2 },
      unassigned_owed: 0,
    });
    open();
    await screen.findByText('Paise One');
    const total = screen.getByText('Total').closest('tr')!;
    expect(within(total).getAllByText('₹3,002')).toHaveLength(2);
    expect(within(total).queryByText('₹3,001')).toBeNull();
    // ...and says what the exact sum is, so it can be met on another screen.
    expect(footnote()).toContain('each Total adds the rows as shown');
    expect(footnote()).toContain('to the paise the totals are Billed ₹3,001.20, Owed ₹3,001.20');

    fireEvent.click(screen.getByRole('button', { name: /export csv/i }));
    const [rows] = exportToCSV.mock.calls[0];
    const sum = (k: string) =>
      rows.filter((r: { vendor_name: string }) => r.vendor_name !== 'Total').reduce((s: number, r: Record<string, number>) => s + r[k], 0);
    const totalLine = rows.find((r: { vendor_name: string }) => r.vendor_name === 'Total');
    expect([totalLine.billed, totalLine.owed]).toEqual([3002, 3002]);
    expect([sum('billed'), sum('owed')]).toEqual([3002, 3002]);
  });

  it('#8/#32: owed is as at the body\'s day -- today for the month we are in, never "the end of the month"', async () => {
    roles = ['ADMIN'];
    serve({ ...REPORT, month: '2026-10', store_id: null, as_of: '2026-10-01', unassigned_owed: 0 });
    open();
    await screen.findByText('Jharkhand Optical');
    expect(caption()).toContain('Owed as at 01 Oct 2026');
    expect(footnote()).toContain('owed is the balance as at 01 Oct 2026 (today: the month is not over)');
    expect(footnote()).not.toContain('end of the month');
  });

  it('#8: a month that is over is as at its last day, and says so', async () => {
    roles = ['ADMIN'];
    serve({ ...REPORT, store_id: null, as_of: '2026-09-30', unassigned_owed: 0 });
    open();
    await screen.findByText('Jharkhand Optical');
    // en-IN spells September "Sept" in some ICU builds.
    expect(caption()).toMatch(/Owed as at 30 Sept? 2026/);
    expect(footnote()).toMatch(/as at 30 Sept? 2026 \(the end of the month\)/);
  });

  it('#32: the month box stops at this month, and a later month typed in reads this month', async () => {
    roles = ['ADMIN'];
    serve({ ...REPORT, store_id: null, as_of: '2026-09-30' });
    open();
    await screen.findByText('Jharkhand Optical');
    const box = screen.getByLabelText('Month') as HTMLInputElement;
    const now = box.value;
    expect(now).toMatch(/^\d{4}-\d{2}$/);
    expect(box.max).toBe(now);
    const later = `${Number(now.slice(0, 4)) + 1}${now.slice(4)}`;
    fireEvent.change(box, { target: { value: later } });
    await waitFor(() => expect(box.value).toBe(now));
    expect(reportParams().some((p) => p.month === later)).toBe(false);
  });

  it('#9/#36: the note says how Received is valued and counts the receipt lines with no price', async () => {
    roles = ['ADMIN'];
    serve({ ...REPORT, store_id: null, as_of: '2026-09-30', unpriced_receipt_lines: 2 });
    open();
    await screen.findByText('Jharkhand Optical');
    expect(footnote()).toContain("or at the receipt's own price for goods on no order");
    expect(footnote()).toContain('an order line with no GST rate counts without GST, as its bill draft does');
    expect(footnote()).toContain('receipts with no price count 0 (2 receipt lines in this report)');
  });

  it('#4: All stores names the money that is in no shop', async () => {
    roles = ['ADMIN'];
    serve({ ...REPORT, store_id: null, as_of: '2026-09-30', unassigned_owed: -1000 });
    open();
    await screen.findByText('Jharkhand Optical');
    expect(caption()).toMatch(/^All stores/);
    expect(footnote()).toContain('A bill not yet placed in a shop (and the money paid against it)');
    expect(footnote()).toContain('count here under All stores only; in no shop: ₹1,000 advance.');
  });

  it('#35: an accountant is told the shop the figures cover, and never of an All stores view', async () => {
    roles = ['ACCOUNTANT'];
    serve({ ...REPORT, store_id: 'BV-PUN-01', as_of: '2026-09-30', unassigned_owed: null });
    open();
    await screen.findByText('Jharkhand Optical');
    await waitFor(() => expect(caption()).toContain('Shop: WizOpt Pune'));
    expect(screen.queryByLabelText('Purchase shop')).toBeNull();
    expect(footnote()).not.toMatch(/All stores/i);
    expect(footnote()).toContain('counts at the shop it was recorded for');
  });

  it('#35: an admin narrowed to one shop is told which, and where the rest is counted', async () => {
    roles = ['ADMIN'];
    serve({ ...REPORT, store_id: 'BV-DHN-01', as_of: '2026-09-30', unassigned_owed: null });
    open();
    await screen.findByText('Jharkhand Optical');
    await waitFor(() => expect(caption()).toContain('Shop: Dhanbad'));
    expect(footnote()).toContain('count under All stores only.');
  });
});

describe('Purchases this month says what Received and owed leave out (review r2 #3, #5)', () => {
  it('#3: Received is the goods put into stock, a held line in the month it is added', async () => {
    roles = ['ADMIN'];
    serve({ ...REPORT, store_id: null, as_of: '2026-09-30', unassigned_owed: 0 });
    open();
    await screen.findByText('Jharkhand Optical');
    expect(footnote()).toContain('Received is the goods put into stock, each in the month it went in');
    expect(footnote()).toContain(
      'a line held back at receiving until its product is catalogued counts in the month it is added to stock',
    );
  });

  it('#5: a return lowers owed only once it is recorded as a debit note on the supplier', async () => {
    roles = ['ACCOUNTANT'];
    serve({ ...REPORT, store_id: 'BV-PUN-01', as_of: '2026-09-30', unassigned_owed: null });
    open();
    await screen.findByText('Jharkhand Optical');
    expect(footnote()).toContain(
      "A return's credit (a vendor-return credit note, an RMA credit, an RTV debit note) lowers owed only once " +
        'it is recorded as a debit note on the supplier (Cash Flow & Payables, Debit note).',
    );
  });
});
