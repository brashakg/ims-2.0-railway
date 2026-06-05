// ============================================================================
// IMS 2.0 - Budget Tracking Tab
// ============================================================================

import clsx from 'clsx';
import type { BudgetData } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface BudgetPanelProps {
  budgets: BudgetData[];
  selectedYear: string;
}

export default function BudgetPanel({ budgets, selectedYear }: BudgetPanelProps) {
  const totalAllocated = budgets.reduce((s, b) => s + b.allocated, 0);
  const totalSpent = budgets.reduce((s, b) => s + b.spent, 0);

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-sm text-slate-600">Total Budget</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{formatCurrency(totalAllocated)}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-sm text-slate-600">Total Spent</p>
          <p className="text-2xl font-bold text-blue-600 mt-1">{formatCurrency(totalSpent)}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-sm text-slate-600">Utilization</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">
            {totalAllocated > 0 ? ((totalSpent / totalAllocated) * 100).toFixed(1) : 0}%
          </p>
        </div>
      </div>

      {/* Budget allocations are planned in the dedicated Budgets module
          (/budgets, per store + period). The toast-only "Allocate Budget"
          button that previously sat here did not persist anything, so it was
          removed (SYSTEM_INTENT: fail loudly, never fake success). */}
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold text-gray-900">Budget Allocations -- FY {selectedYear}</h3>
      </div>

      {/* Budget Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-left">
            <tr>
              <th className="px-4 py-3">Category</th>
              <th className="px-4 py-3 text-right">Allocated</th>
              <th className="px-4 py-3 text-right">Spent</th>
              <th className="px-4 py-3 text-right">Remaining</th>
              <th className="px-4 py-3 text-right">Variance %</th>
              <th className="px-4 py-3">Progress</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {budgets.map((b) => {
              const pct = b.allocated > 0 ? (b.spent / b.allocated) * 100 : 0;
              const overBudget = b.remaining < 0;
              return (
                <tr key={b.category} className="text-gray-900">
                  <td className="px-4 py-3 font-medium">{b.category}</td>
                  <td className="px-4 py-3 text-right">{formatCurrency(b.allocated)}</td>
                  <td className="px-4 py-3 text-right">{formatCurrency(b.spent)}</td>
                  <td
                    className={clsx(
                      'px-4 py-3 text-right font-medium',
                      overBudget ? 'text-red-600' : 'text-green-600'
                    )}
                  >
                    {formatCurrency(b.remaining)}
                  </td>
                  <td
                    className={clsx(
                      'px-4 py-3 text-right',
                      b.variance_percent >= 0 ? 'text-green-600' : 'text-red-600'
                    )}
                  >
                    {b.variance_percent > 0 ? '+' : ''}
                    {b.variance_percent}%
                  </td>
                  <td className="px-4 py-3">
                    <div className="w-24 h-2 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className={clsx(
                          'h-full rounded-full',
                          overBudget
                            ? 'bg-red-500'
                            : pct > 80
                              ? 'bg-amber-500'
                              : 'bg-green-500'
                        )}
                        style={{ width: `${Math.min(pct, 100)}%` }}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
