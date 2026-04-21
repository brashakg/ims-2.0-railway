// ============================================================================
// IMS 2.0 - Budget Tracking Tab
// ============================================================================

import { useState } from 'react';
import { Plus, X } from 'lucide-react';
import clsx from 'clsx';
import type { BudgetData } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface BudgetPanelProps {
  budgets: BudgetData[];
  selectedYear: string;
  onAllocateBudget: (category: string, amount: string) => void;
}

export default function BudgetPanel({ budgets, selectedYear, onAllocateBudget }: BudgetPanelProps) {
  const [showModal, setShowModal] = useState(false);
  const [budgetCategory, setBudgetCategory] = useState('');
  const [budgetAmount, setBudgetAmount] = useState('');

  const totalAllocated = budgets.reduce((s, b) => s + b.allocated, 0);
  const totalSpent = budgets.reduce((s, b) => s + b.spent, 0);

  const handleSubmit = () => {
    onAllocateBudget(budgetCategory, budgetAmount);
    setShowModal(false);
    setBudgetCategory('');
    setBudgetAmount('');
  };

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white border border-slate-700 rounded-lg p-4">
          <p className="text-sm text-slate-600">Total Budget</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{formatCurrency(totalAllocated)}</p>
        </div>
        <div className="bg-white border border-slate-700 rounded-lg p-4">
          <p className="text-sm text-slate-600">Total Spent</p>
          <p className="text-2xl font-bold text-blue-600 mt-1">{formatCurrency(totalSpent)}</p>
        </div>
        <div className="bg-white border border-slate-700 rounded-lg p-4">
          <p className="text-sm text-slate-600">Utilization</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">
            {totalAllocated > 0 ? ((totalSpent / totalAllocated) * 100).toFixed(1) : 0}%
          </p>
        </div>
      </div>

      {/* Budget Allocation Button */}
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold text-gray-900">Budget Allocations -- FY {selectedYear}</h3>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
        >
          <Plus className="w-4 h-4" /> Allocate Budget
        </button>
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
          <tbody className="divide-y divide-slate-700">
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

      {/* Budget Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-white border border-slate-700 rounded-xl p-6 w-full max-w-md">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-gray-900">Allocate Budget</h3>
              <button
                onClick={() => setShowModal(false)}
                className="text-slate-600 hover:text-gray-900"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-sm text-slate-700 mb-1">Category</label>
                <select
                  value={budgetCategory}
                  onChange={(e) => setBudgetCategory(e.target.value)}
                  className="w-full bg-slate-50 border border-slate-600 text-gray-900 rounded-lg px-3 py-2 text-sm"
                >
                  <option value="">Select category...</option>
                  <option value="Employee Salaries">Employee Salaries</option>
                  <option value="Rent & Utilities">Rent & Utilities</option>
                  <option value="Marketing">Marketing</option>
                  <option value="Inventory">Inventory</option>
                  <option value="Maintenance">Maintenance</option>
                  <option value="Technology">Technology</option>
                  <option value="Miscellaneous">Miscellaneous</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-slate-700 mb-1">Amount (INR)</label>
                <input
                  type="number"
                  value={budgetAmount}
                  onChange={(e) => setBudgetAmount(e.target.value)}
                  placeholder="e.g. 50000"
                  className="w-full bg-slate-50 border border-slate-600 text-gray-900 rounded-lg px-3 py-2 text-sm"
                />
              </div>
              <button
                onClick={handleSubmit}
                className="w-full py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700"
              >
                Save Allocation
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
