// Decision D115 (PR #1410): when the B2B invoice list cannot be loaded (the
// door answers 503 since D106) the two B2B -> Tally screens clear their rows
// and say "Could not load B2B invoices" with Retry. Before, a toast was raised
// and the table kept what it had: "No pending B2B invoices. Backlog is clear."
// / "No B2B invoices for this period." on first open, or the PREVIOUS
// period's rows and summary under the new dates.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u-a', roles: ['ADMIN'], activeRole: 'ADMIN', activeStoreId: 'BV-DHN-01' } }),
}));
const fin = vi.hoisted(() => ({
  listB2BInvoices: vi.fn(), downloadB2BInvoiceXml: vi.fn(), exportB2BInvoicesToTally: vi.fn(),
  markB2BInvoicesExported: vi.fn(), markB2BInvoiceDone: vi.fn(), setB2BAttentionNote: vi.fn(),
}));
vi.mock('../../../services/api/finance', () => ({ financeApi: fin }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStores: vi.fn(async () => ({ stores: [] })) } }));

import { ToastProvider } from '../../../context/ToastContext';
import { ApiError } from '../../../services/api/client';
import B2BTallyWorklist from '../B2BTallyWorklist';
import B2BTallyExport from '../B2BTallyExport';

const FAILED = 'Could not load B2B invoices';
const EMPTY_LINES = ['No pending B2B invoices. Backlog is clear.', 'No B2B invoices for this period.'];
const unavailable = () =>
  new ApiError('The records could not be reached, so this report was not worked out. Nothing was changed. Try again.', { status: 503 });

const invoice = (n: string) => ({
  order_id: `ORD-${n}`, invoice_number: `INV-${n}`, has_invoice_number: true, date: '2026-11-03', store_id: 'BV-DHN-01',
  customer_id: 'C1', customer_name: 'Trade Buyer', customer_gstin: '20ABCDE1234F1Z5', place_of_supply: '20',
  interstate: false, taxable: 1000, cgst: 90, sgst: 90, igst: 0, tax: 180, total: 1180, needs_eway: false,
  tally_status: 'PENDING' as const, exported_to_tally: false, attention_note: '', age_days: 5, overdue: true,
});
const answer = (...numbers: string[]) => ({
  invoices: numbers.map(invoice),
  summary: {
    count: numbers.length, pending: numbers.length, in_tally: 0, done: 0, needs_eway: 0, overdue: numbers.length,
    exported: 0, total_taxable: 1000, total_tax: 180, total_value: 1180,
  },
  eway_threshold: 50000, pending_reminder_days: 3,
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const fromDate = () => document.querySelector('input[type="date"]') as HTMLInputElement;
const noEmptyLine = () => EMPTY_LINES.forEach((line) => expect(screen.queryByText(line)).toBeNull());

beforeEach(() => {
  for (const f of Object.values(fin)) f.mockReset();
});

describe.each([
  ['B2B Tally Worklist', B2BTallyWorklist],
  ['B2B Tally Export', B2BTallyExport],
])('%s when the invoice list cannot be loaded (D115)', (_name, Screen) => {
  const open = () => render(<ToastProvider><Screen /></ToastProvider>);

  it('says so with Retry on first open, never that there are no invoices', async () => {
    fin.listB2BInvoices.mockRejectedValueOnce(unavailable()).mockResolvedValue(answer('7'));
    open();
    const failed = await screen.findByRole('alert');
    expect(failed.textContent).toContain(FAILED);
    noEmptyLine();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('INV-7')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(fin.listB2BInvoices).toHaveBeenCalledTimes(2);
  });

  it('clears the previous period when new dates fail to load', async () => {
    fin.listB2BInvoices.mockResolvedValueOnce(answer('1')).mockRejectedValueOnce(unavailable());
    open();
    expect(await screen.findByText('INV-1')).toBeTruthy();
    expect(screen.getByText('Need e-way bill')).toBeTruthy(); // the summary cards
    fireEvent.change(fromDate(), { target: { value: '2026-10-01' } });
    expect((await screen.findByRole('alert')).textContent).toContain(FAILED);
    // Neither last period's rows nor its totals stand under the new dates.
    expect(screen.queryByText('INV-1')).toBeNull();
    expect(screen.queryByText('Need e-way bill')).toBeNull();
    noEmptyLine();
  });

  it('still says there are none when the list loads empty', async () => {
    fin.listB2BInvoices.mockResolvedValue(answer());
    open();
    await waitFor(() => expect(EMPTY_LINES.some((line) => screen.queryByText(line))).toBe(true));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows the dates asked for last, whichever answer arrives last', async () => {
    const older = deferred<ReturnType<typeof answer>>();
    const olderFailing = deferred<ReturnType<typeof answer>>();
    fin.listB2BInvoices
      .mockResolvedValueOnce(answer('1'))
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(olderFailing.promise)
      .mockResolvedValueOnce(answer('4'));
    open();
    expect(await screen.findByText('INV-1')).toBeTruthy();
    fireEvent.change(fromDate(), { target: { value: '2026-10-01' } }); // held
    fireEvent.change(fromDate(), { target: { value: '2026-09-01' } }); // held, will fail
    fireEvent.change(fromDate(), { target: { value: '2026-08-01' } }); // answers at once
    expect(await screen.findByText('INV-4')).toBeTruthy();
    older.resolve(answer('2'));
    olderFailing.reject(unavailable());
    await older.promise;
    await olderFailing.promise.catch(() => undefined);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText('INV-4')).toBeTruthy();
    expect(screen.queryByText('INV-2')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(fin.listB2BInvoices.mock.calls.map((c) => c[0].from_date)).toEqual([
      expect.any(String), '2026-10-01', '2026-09-01', '2026-08-01']);
  });
});

it('B2B Tally Export drops the selection with the rows it was made on', async () => {
  fin.listB2BInvoices.mockResolvedValueOnce(answer('1')).mockRejectedValueOnce(unavailable());
  render(<ToastProvider><B2BTallyExport /></ToastProvider>);
  fireEvent.click(await screen.findByLabelText('Select INV-1'));
  expect(screen.getByText('1 selected')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  expect((await screen.findByRole('alert')).textContent).toContain(FAILED);
  expect(screen.getByText('0 selected')).toBeTruthy();
  expect((screen.getByRole('button', { name: /Export to Tally/ }) as HTMLButtonElement).disabled).toBe(true);
  expect(fin.exportB2BInvoicesToTally).not.toHaveBeenCalled();
});
