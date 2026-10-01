// ============================================================================
// IMS 2.0 - Purchases this month (/purchase/this-month) -- audit F56
// ============================================================================
// Per vendor: what we ordered, received, were billed, paid, still owe and the
// next due date, for one month. Every rupee is the server's
// (GET /vendors/purchases-this-month): billed / paid / owed are the supplier
// ledger's own rows, so this page can never disagree with the vendor ledger.
// The shop filter is the one Purchase scope (purchaseShop.tsx).

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Download, Loader2 } from 'lucide-react';
import api from '../../services/api/client';
import { exportToCSV } from '../../utils/exportUtils';
import { formatDateIST, istDayString } from '../../utils/datetime';
import { usePurchaseShop } from './purchaseShop';

type Figures = { ordered: number; received: number; billed: number; paid: number; owed: number };
type VendorRow = Figures & {
  vendor_id: string;
  vendor_name: string;
  next_due_date: string | null;
  /** Past due on the day the report is as at (the month's end, or today). */
  next_due_overdue?: boolean;
};
type Report = { month: string; vendors: VendorRow[]; totals: Figures };

const COLUMNS: { key: keyof Figures; label: string }[] = [
  { key: 'ordered', label: 'Ordered' },
  { key: 'received', label: 'Received' },
  { key: 'billed', label: 'Billed' },
  { key: 'paid', label: 'Paid' },
  { key: 'owed', label: 'Owed' },
];

// Whole rupees, on screen, in the export and on the Total line alike: ONE rule.
// The size rounds half away from zero and the sign goes back on, so an advance
// of 1500.50 is 1501 everywhere (Math.round(-1500.5) is -1500, a rupee off the
// paid 1501 on the same row). A figure that rounds to nothing is 0, never -0.
const whole = (n: number) => {
  const size = Math.round(Math.abs(n));
  return n < 0 && size !== 0 ? -size : size;
};
const rupees = (n: number) => `₹${whole(n).toLocaleString('en-IN')}`;
/** Owed below zero is money already with the supplier (an advance). */
const owedText = (n: number) => {
  const w = whole(n);
  return w < 0 ? `${rupees(-w)} advance` : rupees(w);
};
const thisMonth = () => (istDayString(new Date()) ?? '').slice(0, 7);

export function PurchasesThisMonthSection() {
  const { storeId } = usePurchaseShop();
  const [month, setMonth] = useState(thisMonth);

  const q = useQuery<Report>({
    queryKey: ['purchase', 'this-month', month, storeId ?? 'all'],
    queryFn: async () =>
      (await api.get('/vendors/purchases-this-month', {
        params: { ...(month ? { month } : {}), ...(storeId ? { store_id: storeId } : {}) },
      })).data,
  });
  const rows = q.data?.vendors ?? [];

  // The CSV opens with the screen's numbers: the same rows, the same whole()
  // for every figure and the Total line, and plain numbers (an advance shown
  // as "Rs 1,501 advance" is -1501, summable).
  const exportCsv = () => {
    const totals = q.data?.totals;
    const line = (name: string, f: Figures, due = '', overdue = false) => ({
      vendor_name: name,
      ...Object.fromEntries(COLUMNS.map((c) => [c.key, whole(f[c.key])])),
      next_due_date: due,
      overdue: overdue ? 'overdue' : '',
    });
    exportToCSV(
      [
        ...rows.map((r) => line(r.vendor_name, r, r.next_due_date ?? '', !!r.next_due_overdue)),
        ...(totals ? [line('Total', totals)] : []),
      ],
      `purchases-${month}${storeId ? `-${storeId}` : ''}`,
      [
        { key: 'vendor_name', label: 'Vendor' },
        ...COLUMNS.map((c) => ({
          key: c.key,
          label: c.key === 'owed' ? 'Owed (Rs; below 0 = advance)' : `${c.label} (Rs)`,
        })),
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
            // A cleared box reads this month again, never an endless spinner.
            onChange={(e) => setMonth(e.target.value || thisMonth())}
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
              {rows.map((r) => (
                <tr key={r.vendor_id}>
                  <td className="px-3 py-2 font-medium text-gray-900">{r.vendor_name}</td>
                  {COLUMNS.map((c) => (
                    <td key={c.key} className="px-3 py-2 text-right text-gray-700 whitespace-nowrap">
                      {c.key === 'owed' ? owedText(r.owed) : rupees(r[c.key])}
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
                    {c.key === 'owed' ? owedText(q.data!.totals.owed) : rupees(q.data!.totals[c.key])}
                  </td>
                ))}
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      <p className="text-xs text-gray-500">
        Billed, paid and owed are the supplier ledger's figures; owed is the balance at the end of the
        month, and next due the earliest due date still owed then. In one shop's view, money paid without
        naming a bill counts at the shop of that supplier's latest bill; an advance to a supplier who has
        never billed shows under All stores only. Transfers between our own companies are not purchases.
      </p>
    </div>
  );
}
