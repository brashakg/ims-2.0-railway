// /finance/expenses/summary -- Category Summary (every signed-in user; the
// user's own expenses). Moved byte-identical from the old ExpenseTracker tab.

import { BarChart3, Banknote } from 'lucide-react';
import { CATEGORIES, fc, useExpensesContext } from './expenseShared';
import { Row } from './expenseWidgets';

export function ExpenseSummarySection() {
  const { mine, totalAmt, pendingCount, approvedCount } = useExpensesContext();
  const byCategory = CATEGORIES.map((c) => ({
    ...c, amount: mine.filter((e) => e.category === c.value).reduce((s, e) => s + (e.amount || 0), 0),
  }));

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-6 flex items-center gap-2"><BarChart3 className="w-5 h-5" /> Spending by category</h3>
        <div className="space-y-4">
          {byCategory.map((c) => {
            const pct = totalAmt > 0 ? (c.amount / totalAmt) * 100 : 0;
            return (
              <div key={c.value}>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-sm font-medium text-gray-600">{c.label}</span>
                  <span className="text-sm font-semibold text-gray-900">{fc(c.amount)}</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-2"><div className="bg-bv-red-500 h-2 rounded-full" style={{ width: `${pct}%` }} /></div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-6 flex items-center gap-2"><Banknote className="w-5 h-5" /> Totals</h3>
        <div className="space-y-4">
          <Row label="My total expenses" value={fc(totalAmt)} />
          <Row label="Pending approval" value={String(pendingCount)} color="text-amber-600" />
          <Row label="Approved or beyond" value={String(approvedCount)} color="text-green-600" />
        </div>
      </div>
    </div>
  );
}
