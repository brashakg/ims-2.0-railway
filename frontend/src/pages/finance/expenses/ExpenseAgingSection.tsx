// /finance/expenses/aging -- Reimbursement aging (accountant/admin;
// route-gated). Moved byte-identical from the old ExpenseTracker 'aging' tab.

import { Clock } from 'lucide-react';
import clsx from 'clsx';
import { formatDateIST } from '../../../utils/datetime';
import { catColor, catLabel, fc, useExpensesContext } from './expenseShared';
import { StatusPill } from './expenseWidgets';

export function ExpenseAgingSection() {
  const { aging } = useExpensesContext();
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {(['0-7', '8-15', '15+'] as const).map((b) => {
          const bk = aging?.buckets?.[b];
          const tone = b === '15+' ? 'text-red-600' : b === '8-15' ? 'text-amber-600' : 'text-green-600';
          const label = b === '0-7' ? '0-7 days' : b === '8-15' ? '8-15 days' : 'Over 15 days';
          return (
            <div key={b} className="bg-white border border-gray-200 rounded-lg p-5">
              <p className="text-gray-500 text-sm mb-1 flex items-center gap-1.5"><Clock className="w-4 h-4" /> {label}</p>
              <p className={clsx('text-2xl font-bold', tone)}>{bk?.count ?? 0}</p>
              <p className="text-xs text-gray-500 mt-1">{fc(bk?.amount ?? 0)} outstanding</p>
            </div>
          );
        })}
      </div>

      <div className="bg-white rounded-lg border border-gray-200">
        <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-700">Pending reimbursements (approved, not yet entered)</h3>
          <span className="text-xs text-gray-500">{aging?.total_count ?? 0} item(s) · {fc(aging?.total_amount ?? 0)}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-200 text-left text-xs font-semibold text-gray-500 uppercase">
                <th className="px-4 py-3">By</th>
                <th className="px-4 py-3">Category</th>
                <th className="px-4 py-3 text-right">Amount</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Waiting since</th>
                <th className="px-4 py-3 text-right">Days</th>
                <th className="px-4 py-3">Bucket</th>
              </tr>
            </thead>
            <tbody>
              {(aging?.rows || []).map((r) => (
                <tr key={r.expense_id} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm text-gray-700">{r.employee_name || r.employee_id || '—'}</td>
                  <td className="px-4 py-3 text-sm"><span className={clsx('inline-block px-2 py-0.5 rounded text-xs font-medium', catColor(r.category || ''))}>{catLabel(r.category || '')}</span></td>
                  <td className="px-4 py-3 text-sm font-semibold text-gray-900 text-right">{fc(r.amount)}</td>
                  <td className="px-4 py-3"><StatusPill status={r.status} /></td>
                  <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">{formatDateIST(r.since)}</td>
                  <td className="px-4 py-3 text-sm text-gray-700 text-right">{r.days_pending}</td>
                  <td className="px-4 py-3">
                    <span className={clsx('inline-block px-2 py-0.5 rounded-full text-xs font-medium',
                      r.bucket === '15+' ? 'bg-red-50 text-red-700' : r.bucket === '8-15' ? 'bg-amber-50 text-amber-700' : 'bg-green-50 text-green-700')}>
                      {r.bucket} days
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(aging?.rows?.length ?? 0) === 0 && <div className="p-10 text-center text-gray-500 text-sm">No pending reimbursements</div>}
        </div>
      </div>
    </div>
  );
}
