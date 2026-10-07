// ============================================================================
// IMS 2.0 - Purchases this month (/purchase/this-month) -- audit F56
// ============================================================================
// Per vendor: what we ordered, received, were billed, paid, still owe and the
// next due date, for one month. Every rupee is the server's
// (GET /vendors/purchases-this-month): billed / paid / owed are the supplier
// ledger's own rows, so this page can never disagree with the vendor ledger.
// The shop filter is the one Purchase scope (purchaseShop.tsx). What the page
// says ABOUT its figures comes from the body too: the shop it covers
// (store_id), the day owed is struck on (as_of), receipt lines with no price
// and, for All stores, what is owed and paid ahead in no shop.
//
// THE ONE 'WE OWE' RULE (F56): a supplier's ledger balance is either owed
// (above 0) or paid ahead of its bills (below 0). Owed and Paid ahead are two
// columns, each Total adds its own rows, and one supplier's advance is never
// taken off what we owe another (a netted Total read "Rs 5,000 advance" while
// Rs 5,000 was overdue to another supplier).

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Download, Loader2 } from 'lucide-react';
import api from '../../services/api/client';
import { exportToCSV } from '../../utils/exportUtils';
import { formatDateIST, istDayString } from '../../utils/datetime';
import { PurchaseShopName, usePurchaseShop } from './purchaseShop';

type Figures = { ordered: number; received: number; billed: number; paid: number; owed: number };
type VendorRow = Figures & {
  vendor_id: string;
  vendor_name: string;
  next_due_date: string | null;
  /** Past due on the day the report is as at (the month's end, or today). */
  next_due_overdue?: boolean;
};
/** The exact (to the paise) totals: owed adds only the suppliers we owe;
 *  advances adds what is paid ahead to the others, never taken off owed. */
type Totals = Figures & { advances?: number };
type Report = {
  month: string;
  /** The shop the figures cover; null = all stores (the server's scope rule). */
  store_id?: string | null;
  /** The day owed and next due are struck on: the month's end, or today. */
  as_of?: string;
  vendors: VendorRow[];
  /** Exact (to the paise) sums of the rows; the Total line adds the rows as shown. */
  totals: Totals;
  /** Accepted receipt lines with no price anywhere: Received counts them 0. */
  unpriced_receipt_lines?: number;
  /** All stores only: owed / paid ahead on rows the ledger puts in no shop. */
  unassigned_owed?: number | null;
  unassigned_advances?: number | null;
};

/** A row as the screen and the CSV show it: whole rupees, the supplier's
 *  balance split into owed (above 0) and paid ahead (below 0). */
type Shown = Figures & { advances: number };

const COLUMNS: { key: keyof Shown; label: string }[] = [
  { key: 'ordered', label: 'Ordered' },
  { key: 'received', label: 'Received' },
  { key: 'billed', label: 'Billed' },
  { key: 'paid', label: 'Paid' },
  { key: 'owed', label: 'Owed' },
  { key: 'advances', label: 'Paid ahead' },
];

// Whole rupees, on screen, in the export and on the Total line alike: ONE rule
// (the Suppliers card reads the same helpers). The size rounds half away from
// zero and the sign goes back on, so an advance of 1500.50 is 1501 everywhere
// (Math.round(-1500.5) is -1500, a rupee off the paid 1501 on the same row).
// A figure that rounds to nothing is 0, never -0.
const whole = (n: number) => {
  const size = Math.round(Math.abs(n));
  return n < 0 && size !== 0 ? -size : size;
};
export const rupees = (n: number) => `₹${whole(n).toLocaleString('en-IN')}`;
/** Owed below zero is money already with the supplier (an advance). */
export const owedText = (n: number) => {
  const w = whole(n);
  return w < 0 ? `${rupees(-w)} advance` : rupees(w);
};
/** To the paise, for the one place an exact figure is printed. */
const paiseText = (n: number) => {
  const text = `₹${Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return n < 0 ? `-${text}` : text;
};
const thisMonth = () => (istDayString(new Date()) ?? '').slice(0, 7);
/** 'YYYY-MM-DD' of a month's last day. */
const lastDay = (month: string) => {
  const [y, m] = month.split('-').map(Number);
  return `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`;
};
/** One row as shown. The balance is rounded ONCE (whole) and then split, so
 *  owed and paid ahead can never both be above 0 on one row. */
const shownRow = (r: VendorRow): Shown => {
  const balance = whole(r.owed);
  return {
    ordered: whole(r.ordered),
    received: whole(r.received),
    billed: whole(r.billed),
    paid: whole(r.paid),
    owed: balance > 0 ? balance : 0,
    advances: balance < 0 ? -balance : 0,
  };
};
/** The Total line: each column's rows AS SHOWN (whole rupees) added up, so a
 *  reader's -- or a spreadsheet's -- sum of the rows is the Total printed.
 *  Owed adds the suppliers we owe, Paid ahead the ones paid ahead: never
 *  netted against each other. */
const addRows = (rows: Shown[]) =>
  Object.fromEntries(COLUMNS.map((c) => [c.key, rows.reduce((sum, r) => sum + r[c.key], 0)])) as Shown;

export function PurchasesThisMonthSection() {
  const { storeId, canPick } = usePurchaseShop();
  const [month, setMonth] = useState(thisMonth);

  const q = useQuery<Report>({
    queryKey: ['purchase', 'this-month', month, storeId ?? 'all'],
    queryFn: async () =>
      (await api.get('/vendors/purchases-this-month', {
        params: { ...(month ? { month } : {}), ...(storeId ? { store_id: storeId } : {}) },
      })).data,
  });
  const rows = q.data?.vendors ?? [];
  const shownRows = rows.map(shownRow);
  const shown = addRows(shownRows);

  // The CSV opens with the screen's numbers: the same rows, the same columns
  // (owed and paid ahead apart, both 0 or more), and the Total line that adds
  // each column's rows -- so a spreadsheet's SUM of a column is its Total.
  const exportCsv = () => {
    const line = (name: string, f: Shown, due = '', overdue = false) => ({
      vendor_name: name,
      ...Object.fromEntries(COLUMNS.map((c) => [c.key, f[c.key]])),
      next_due_date: due,
      overdue: overdue ? 'overdue' : '',
    });
    exportToCSV(
      [
        ...rows.map((r, i) => line(r.vendor_name, shownRows[i], r.next_due_date ?? '', !!r.next_due_overdue)),
        line('Total', shown),
      ],
      `purchases-${month}${storeId ? `-${storeId}` : ''}`,
      [
        { key: 'vendor_name', label: 'Vendor' },
        ...COLUMNS.map((c) => ({ key: c.key, label: `${c.label} (Rs)` })),
        { key: 'next_due_date', label: 'Next due' },
        { key: 'overdue', label: 'Overdue' },
      ],
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-sm text-gray-600">
          Month
          <input
            type="month"
            value={month}
            // Nothing after this month: a month not yet begun has no figures,
            // only today's balance under a later month's name.
            max={thisMonth()}
            // A cleared box (or a later month typed past the cap) reads this
            // month again, never an endless spinner.
            onChange={(e) => {
              const picked = e.target.value;
              setMonth(picked && picked <= thisMonth() ? picked : thisMonth());
            }}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 bg-white"
          />
        </label>
        <button
          type="button"
          onClick={exportCsv}
          disabled={rows.length === 0}
          className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5 disabled:opacity-50"
        >
          <Download className="w-4 h-4" /> Export CSV
        </button>
      </div>

      {q.data && <Caption report={q.data} />}

      {q.isError ? (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2">
          <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <p className="flex-1 text-sm font-medium text-red-900">Failed to load purchases for {month}</p>
          <button type="button" onClick={() => q.refetch()} className="text-xs font-medium text-red-700 underline">Retry</button>
        </div>
      ) : q.isPending ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
        </div>
      ) : rows.length === 0 ? (
        <p className="text-center py-12 bg-white border border-gray-200 rounded-lg text-gray-500">
          No orders, receipts, bills, payments or money owed for {month}.
        </p>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs">
              <tr>
                <th className="text-left px-3 py-2">Vendor</th>
                {COLUMNS.map((c) => <th key={c.key} className="text-right px-3 py-2">{c.label}</th>)}
                <th className="text-left px-3 py-2">Next due</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r, i) => (
                <tr key={r.vendor_id}>
                  <td className="px-3 py-2 font-medium text-gray-900">{r.vendor_name}</td>
                  {COLUMNS.map((c) => (
                    <td key={c.key} className="px-3 py-2 text-right text-gray-700 whitespace-nowrap">
                      {rupees(shownRows[i][c.key])}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-gray-700 whitespace-nowrap">
                    {formatDateIST(r.next_due_date)}
                    {r.next_due_overdue && <span className="ml-1.5 text-xs font-medium text-red-600">overdue</span>}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot className="bg-gray-50 font-semibold text-gray-900">
              <tr>
                <td className="px-3 py-2">Total</td>
                {COLUMNS.map((c) => (
                  <td key={c.key} className="px-3 py-2 text-right whitespace-nowrap">
                    {rupees(shown[c.key])}
                  </td>
                ))}
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      {q.data && <Footnote report={q.data} shown={shown} canPick={canPick} />}
    </div>
  );
}

/** Which shop and which day the figures are for, from the body itself. */
function Caption({ report }: { report: Report }) {
  return (
    <p className="text-sm text-gray-600">
      {report.store_id ? (
        <>
          Shop: <span className="font-medium text-gray-900"><PurchaseShopName storeId={report.store_id} /></span>
        </>
      ) : (
        <span className="font-medium text-gray-900">All stores</span>
      )}
      {report.as_of && <> · Owed as at {formatDateIST(report.as_of)}</>}
    </p>
  );
}

/** What the figures are and what they leave out -- only what is true of THIS
 *  body: its as-of day, its shop scope (no word of an All stores view to a
 *  login that cannot open one), and the totals' rounding. Two rules always
 *  hold: Received is units put into stock, each in its own month (never a
 *  line held for cataloguing); and no return door writes the supplier ledger
 *  yet, so a return's credit lowers owed only once booked as a debit note. */
function Footnote({ report, shown, canPick }: { report: Report; shown: Shown; canPick: boolean }) {
  const asOf = report.as_of;
  const unpriced = report.unpriced_receipt_lines ?? 0;
  // In no shop, by the same rule: what is owed there and what is paid ahead
  // there, each its own figure.
  const noShop = [
    whole(report.unassigned_owed ?? 0) > 0 ? `${rupees(report.unassigned_owed ?? 0)} owed` : '',
    whole(report.unassigned_advances ?? 0) > 0 ? `${rupees(report.unassigned_advances ?? 0)} paid ahead` : '',
  ].filter(Boolean);
  // A Total adds the rows as shown; say so, and give the exact sum wherever
  // the two part (paise on many rows add up to rupees).
  const exact = (key: keyof Shown) => report.totals[key] ?? 0;
  const drift = COLUMNS.filter((c) => whole(exact(c.key)) !== shown[c.key]);
  return (
    <p className="text-xs text-gray-500">
      Billed, paid and owed are the supplier ledger's figures; owed is the balance as at{' '}
      {asOf ? formatDateIST(asOf) : 'the end of the month'}
      {asOf && (asOf === lastDay(report.month) ? ' (the end of the month)' : ' (today: the month is not over)')}, and
      next due the earliest due date still owed then. Each supplier's payments settle its own bills first; a
      supplier paid beyond its bills shows under Paid ahead, a figure of its own: Owed adds only the suppliers we
      owe and is never reduced by money paid ahead to another supplier. A return's credit (a vendor-return credit
      note, an RMA credit, an RTV debit note) lowers owed only once it is recorded as a debit note on the supplier
      (Cash Flow &amp; Payables, Debit note). Received is the goods put into stock, each in the month it went in: a line held back at
      receiving until its product is catalogued counts in the month it is added to stock. It is valued at the order's
      price incl. GST (an order line with no GST rate counts without GST, as its bill draft does), or at the
      receipt's own price for goods on no order; receipts with no price count 0
      {unpriced > 0 && ` (${unpriced} receipt line${unpriced === 1 ? '' : 's'} in this report)`}.{' '}
      {report.store_id ? (
        <>
          Money paid without naming a bill counts at the shop it was recorded for, else at the shop of that
          supplier's latest bill.
          {canPick &&
            ' A bill not yet placed in a shop (and the money paid against it), and money recorded with no shop for a supplier who has never billed, count under All stores only.'}
        </>
      ) : (
        <>
          A bill not yet placed in a shop (and the money paid against it), and money recorded with no shop for a
          supplier who has never billed, count here under All stores only
          {noShop.length > 0 ? `; in no shop: ${noShop.join(', ')}` : ''}.
        </>
      )}{' '}
      Transfers between our own companies are not purchases. Figures are rounded to the rupee and each Total adds
      the rows as shown
      {drift.length > 0 &&
        `; to the paise the ${drift.length === 1 ? 'total is' : 'totals are'} ${drift
          .map((c) => `${c.label} ${paiseText(exact(c.key))}`)
          .join(', ')}`}
      .
    </p>
  );
}
