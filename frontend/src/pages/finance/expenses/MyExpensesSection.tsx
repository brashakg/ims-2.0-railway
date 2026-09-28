// /finance/expenses -- My Expenses (the index; every signed-in user).
// Moved byte-identical from the old ExpenseTracker 'my' tab.

import { useState } from 'react';
import { Search, Send } from 'lucide-react';
import { expensesApi } from '../../../services/api/expenses';
import { CATEGORIES, STATUS_META, useExpensesContext } from './expenseShared';
import { ExpenseTable } from './expenseWidgets';

export function MyExpensesSection() {
  const { mine, isApprover, doAction } = useExpensesContext();
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');

  const filteredMine = mine.filter((e) => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = !q || e.description?.toLowerCase().includes(q) || e.expense_id?.toLowerCase().includes(q);
    const matchesStatus = statusFilter === 'all' || (e.status || '').toUpperCase() === statusFilter;
    const matchesCategory = categoryFilter === 'all' || e.category === categoryFilter;
    return matchesSearch && matchesStatus && matchesCategory;
  });

  return (
    <div className="bg-white rounded-lg border border-gray-200">
      <div className="p-4 border-b border-gray-200 grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="relative">
          <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" />
          <input placeholder="Search…" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:border-bv-red-500" />
        </div>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:border-bv-red-500" title="Filter by status">
          <option value="all">All status</option>
          {Object.entries(STATUS_META).filter(([k]) => k !== 'DRAFT').map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}
          className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-white focus:outline-none focus:border-bv-red-500" title="Filter by category">
          <option value="all">All categories</option>
          {CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
      </div>
      <ExpenseTable rows={filteredMine} showOwner={false}
        renderActions={(e) => (
          (e.status || '').toUpperCase() === 'APPROVED' && isApprover ? (
            <button className="text-blue-600 hover:underline text-xs inline-flex items-center gap-1"
              onClick={() => doAction(() => expensesApi.sendToAccountant(e.expense_id), 'Sent to accountant')}>
              <Send className="w-3.5 h-3.5" /> To accountant
            </button>
          ) : null
        )} />
    </div>
  );
}
