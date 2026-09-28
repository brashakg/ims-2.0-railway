// /finance/expenses/entry -- For Entry (accountant; route-gated).
// Moved byte-identical from the old ExpenseTracker 'entry' tab.

import { BookCheck } from 'lucide-react';
import { expensesApi } from '../../../services/api/expenses';
import { useExpensesContext } from './expenseShared';
import { ExpenseTable } from './expenseWidgets';

export function ExpenseEntrySection() {
  const { toEnter, doAction } = useExpensesContext();
  return (
    <div className="bg-white rounded-lg border border-gray-200">
      <ExpenseTable rows={toEnter} showOwner
        empty="Nothing awaiting ledger entry"
        renderActions={(e) => (
          <button className="px-3 py-1 rounded-md bg-green-600 text-white text-xs inline-flex items-center gap-1 hover:bg-green-700"
            onClick={() => doAction(() => expensesApi.markEntered(e.expense_id), 'Marked as entered')}>
            <BookCheck className="w-3.5 h-3.5" /> Mark entered
          </button>
        )} />
    </div>
  );
}
