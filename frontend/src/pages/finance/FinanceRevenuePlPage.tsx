// ============================================================================
// IMS 2.0 - Finance dashboard: Revenue & P&L (the index section)
// ============================================================================
// One section of the old FinanceDashboard tab container, moved verbatim. The
// data and handlers come from FinanceLayout (useFinanceContext).

import { ArrowUpDown } from 'lucide-react';
import clsx from 'clsx';
import { useFinanceContext } from './FinanceLayout';
import FinanceSummary from './FinanceSummary';
import RestrictedTotalsNotice from './RestrictedTotalsNotice';

export function FinanceRevenuePlPage() {
  const {
    expensesRestricted,
    revenueData,
    plStatement,
    canSeeStorePayroll,
    pnlByStore,
    pnlStoreSort,
    togglePnlStoreSort,
    pnlStoreRows,
    pnlByCategory,
  } = useFinanceContext();
  return (
    <>
      {/* The operating-expense figure below is short by whatever the
          backend withheld from this role. Say so BEFORE the number,
          not after it. */}
      <RestrictedTotalsNotice
        show={expensesRestricted}
        scope="profit and expense figures"
        className="mb-4"
      />
      <FinanceSummary revenueData={revenueData} plStatement={plStatement} />
      {!canSeeStorePayroll && (
        <div className="card mt-4 p-4">
          <div className="text-sm font-medium text-gray-700">P&amp;L by store</div>
          <p className="text-sm text-gray-500 mt-1">
            Not shown for your role. This table includes each store's monthly
            salary bill, and with only a few people per store that total would
            reveal what individual colleagues are paid. Salary figures are
            restricted to administrators.
          </p>
          {/* Points at a screen that ACTUALLY EXISTS and that this role
              can actually open: ReportsPage's Forecast tab renders
              /reports/finance/expense-vs-revenue, which is scoped to the
              caller's own store and carries revenue, cost and profit
              with no payroll term. (/reports/profit/by-store is also
              clean and open, but nothing in the UI calls it, so naming
              it here would send people somewhere that isn't there.) */}
          <p className="text-sm text-gray-500 mt-2">
            Your own store's revenue, cost and profit — with no salary line —
            are on Reports &rsaquo; Forecast, under "Expense vs Revenue".
          </p>
        </div>
      )}
      {canSeeStorePayroll && pnlByStore.length > 0 && (
        <div className="card mt-4 overflow-x-auto">
          <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">P&amp;L by store</div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600"><tr>
              {([
                { key: 'store_id', label: 'Store', align: 'left' },
                { key: 'revenue', label: 'Revenue', align: 'right' },
                { key: 'cogs', label: 'COGS', align: 'right' },
                { key: 'gross_profit', label: 'Gross profit', align: 'right' },
                { key: 'margin', label: 'Margin %', align: 'right' },
                { key: 'expenses', label: 'Expenses', align: 'right' },
                { key: 'payroll', label: 'Payroll', align: 'right' },
                { key: 'net_profit', label: 'Net', align: 'right' },
              ] as Array<{ key: typeof pnlStoreSort.key; label: string; align: 'left' | 'right' }>).map((col) => (
                <th
                  key={col.key}
                  onClick={() => togglePnlStoreSort(col.key)}
                  className={clsx(
                    'px-3 py-2 cursor-pointer select-none hover:text-gray-900',
                    col.align === 'right' ? 'text-right' : 'text-left',
                  )}
                  title="Click to sort"
                >
                  <span className={clsx('inline-flex items-center gap-1', col.align === 'right' && 'flex-row-reverse')}>
                    {col.label}
                    <ArrowUpDown
                      className={clsx(
                        'w-3 h-3',
                        pnlStoreSort.key === col.key ? 'text-bv-red-600' : 'text-gray-300',
                      )}
                    />
                  </span>
                </th>
              ))}
            </tr></thead>
            <tbody>
              {pnlStoreRows.map((s) => (
                <tr key={s.store_id} className="border-t border-gray-100">
                  <td className="px-3 py-2">{s.store_name || s.store_id}</td>
                  <td className="px-3 py-2 text-right">₹{Math.round(s.revenue).toLocaleString('en-IN')}</td>
                  <td className="px-3 py-2 text-right">₹{Math.round(s.cogs).toLocaleString('en-IN')}</td>
                  <td className={clsx('px-3 py-2 text-right font-medium', s.gross_profit < 0 ? 'text-red-600' : 'text-gray-900')}>₹{Math.round(s.gross_profit).toLocaleString('en-IN')}</td>
                  <td className={clsx('px-3 py-2 text-right', s.margin < 0 ? 'text-red-600' : 'text-gray-600')}>{s.margin.toFixed(1)}%</td>
                  <td className="px-3 py-2 text-right">₹{Math.round(s.expenses || 0).toLocaleString('en-IN')}</td>
                  <td className="px-3 py-2 text-right">₹{Math.round(s.payroll || 0).toLocaleString('en-IN')}</td>
                  <td className={clsx('px-3 py-2 text-right font-semibold', (s.net_profit || 0) < 0 ? 'text-red-600' : 'text-gray-900')}>₹{Math.round(s.net_profit || 0).toLocaleString('en-IN')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pnlByCategory.length > 0 && (
        <div className="card mt-4 overflow-x-auto">
          <div className="px-4 py-2 text-sm font-medium text-gray-700 border-b border-gray-100">P&amp;L by category</div>
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600"><tr>
              <th className="px-3 py-2 text-left">Category</th>
              <th className="px-3 py-2 text-right">Revenue</th>
              <th className="px-3 py-2 text-right">COGS</th>
              <th className="px-3 py-2 text-right">Gross profit</th>
            </tr></thead>
            <tbody>
              {pnlByCategory.map((c) => (
                <tr key={c.category} className="border-t border-gray-100">
                  <td className="px-3 py-2">{c.category}</td>
                  <td className="px-3 py-2 text-right">₹{Math.round(c.revenue || 0).toLocaleString('en-IN')}</td>
                  <td className="px-3 py-2 text-right">₹{Math.round(c.cogs || 0).toLocaleString('en-IN')}</td>
                  <td className="px-3 py-2 text-right font-semibold">₹{Math.round(c.gross_profit || 0).toLocaleString('en-IN')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
