// ============================================================================
// IMS 2.0 - Expenses: presentational pieces shared by several sections
// ============================================================================
// Moved byte-identical from the old ExpenseTracker page. StatusPill and
// ExpenseTable were nested inside that page's component; they close over
// nothing, so they now sit at module level where every section can use them.

import { AlertTriangle } from 'lucide-react';
import clsx from 'clsx';
import type { ExpenseRecord } from '../../../services/api/expenses';
import { formatDateIST } from '../../../utils/datetime';
import { STATUS_META, catColor, catLabel, payLabel, fc } from './expenseShared';

const StatusPill = ({ status }: { status: string }) => {
  const m = STATUS_META[(status || '').toUpperCase()] || STATUS_META.PENDING;
  return <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', m.badge)}>{m.label}</span>;
};

function ExpenseTable({ rows, showOwner, empty, renderActions }: {
  rows: ExpenseRecord[]; showOwner: boolean; empty?: string;
  renderActions?: (e: ExpenseRecord) => React.ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-gray-200 text-left text-xs font-semibold text-gray-500 uppercase">
            <th className="px-4 py-3">Date</th>
            {showOwner && <th className="px-4 py-3">By</th>}
            <th className="px-4 py-3">Category</th>
            <th className="px-4 py-3">Payment</th>
            <th className="px-4 py-3 text-right">Amount</th>
            <th className="px-4 py-3">Description</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.expense_id} className="border-b border-gray-100 hover:bg-gray-50">
              <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">{formatDateIST(e.expense_date || e.created_at)}</td>
              {showOwner && <td className="px-4 py-3 text-sm text-gray-700">{e.employee_name || e.employee_id || '—'}</td>}
              <td className="px-4 py-3 text-sm"><span className={clsx('inline-block px-2 py-0.5 rounded text-xs font-medium', catColor(e.category))}>{catLabel(e.category)}</span></td>
              <td className="px-4 py-3 text-sm text-gray-600">{payLabel(e.payment_mode)}</td>
              <td className="px-4 py-3 text-sm font-semibold text-gray-900 text-right">{fc(e.amount)}</td>
              <td className="px-4 py-3 text-sm text-gray-600 max-w-xs">
                <div className="truncate" title={e.description}>{e.description}</div>
                {e.duplicate_bill && (
                  <span className="mt-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-700"
                    title={e.duplicate_of ? `Matches expense ${e.duplicate_of}` : 'Bill matches an earlier receipt'}>
                    <AlertTriangle className="w-3 h-3" /> Duplicate bill
                  </span>
                )}
              </td>
              <td className="px-4 py-3"><StatusPill status={e.status} /></td>
              <td className="px-4 py-3 text-right">{renderActions?.(e)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <div className="p-10 text-center text-gray-500 text-sm">{empty || 'No expenses found'}</div>}
    </div>
  );
}

function Card({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5">
      <p className="text-gray-500 text-sm mb-1">{label}</p>
      <p className={clsx('text-2xl font-bold', color || 'text-gray-900')}>{value}</p>
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between items-center pb-3 border-b border-gray-100 last:border-0">
      <span className="text-gray-500">{label}</span>
      <span className={clsx('text-xl font-bold', color || 'text-gray-900')}>{value}</span>
    </div>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-sm font-medium text-gray-600 mb-1">{label}</span>
      {children}
    </label>
  );
}

export { StatusPill, ExpenseTable, Card, Row, Labeled };
