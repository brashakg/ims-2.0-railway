// /finance/expenses/duplicates -- Duplicate-bill watch-list (approvers;
// route-gated). Moved byte-identical from the old ExpenseTracker tab.

import { AlertTriangle } from 'lucide-react';
import { useExpensesContext } from './expenseShared';
import { ExpenseTable } from './expenseWidgets';

export function ExpenseDuplicatesSection() {
  const { duplicates } = useExpensesContext();
  return (
    <div className="bg-white rounded-lg border border-gray-200">
      <div className="px-4 py-3 border-b border-gray-200 flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-500" />
        <h3 className="text-sm font-semibold text-gray-700">Possible duplicate bills</h3>
        <span className="text-xs text-gray-500">
          Same receipt fingerprint as an earlier expense in this store — scrutinise before approving.
        </span>
      </div>
      <ExpenseTable rows={duplicates} showOwner
        empty="No duplicate bills detected" />
    </div>
  );
}
