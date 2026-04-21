// ============================================================================
// IMS 2.0 - Bank Reconciliation Tab
// ============================================================================

import { CheckCircle, Loader2, AlertTriangle, Download } from 'lucide-react';
import clsx from 'clsx';
import type { ReconciliationData } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface ReconciliationPanelProps {
  reconciliation: ReconciliationData[];
  onReconcile: (itemId: string) => void;
  onImportStatement: () => void;
}

export default function ReconciliationPanel({
  reconciliation,
  onReconcile,
  onImportStatement,
}: ReconciliationPanelProps) {
  const matched = reconciliation.filter((r) => r.status === 'matched').length;
  const pending = reconciliation.filter((r) => r.status === 'pending').length;
  const discrepancies = reconciliation.filter((r) => r.status === 'discrepancy').length;

  return (
    <div className="space-y-6">
      {/* Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
        <div className="bg-white border border-slate-700 rounded-lg p-4">
          <p className="text-sm text-slate-600">Total Entries</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{reconciliation.length}</p>
        </div>
        <div className="bg-white border border-slate-700 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-green-600" />
            <p className="text-sm text-slate-600">Matched</p>
          </div>
          <p className="text-2xl font-bold text-green-600 mt-1">{matched}</p>
        </div>
        <div className="bg-white border border-slate-700 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <Loader2 className="w-4 h-4 text-amber-600" />
            <p className="text-sm text-slate-600">Pending</p>
          </div>
          <p className="text-2xl font-bold text-amber-600 mt-1">{pending}</p>
        </div>
        <div className="bg-white border border-slate-700 rounded-lg p-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-red-600" />
            <p className="text-sm text-slate-600">Discrepancies</p>
          </div>
          <p className="text-2xl font-bold text-red-600 mt-1">{discrepancies}</p>
        </div>
      </div>

      <div className="flex justify-between items-center">
        <h3 className="text-lg font-semibold text-gray-900">Bank vs System Reconciliation</h3>
        <button
          onClick={onImportStatement}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
        >
          <Download className="w-4 h-4" /> Import Bank Statement
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-left">
            <tr>
              <th className="px-4 py-3">Date</th>
              <th className="px-4 py-3 text-right">Bank Amount</th>
              <th className="px-4 py-3 text-right">System Amount</th>
              <th className="px-4 py-3 text-right">Difference</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {reconciliation.map((r) => (
              <tr key={r.id} className="text-gray-900">
                <td className="px-4 py-3">
                  {new Date(r.date).toLocaleDateString('en-IN', {
                    day: 'numeric',
                    month: 'short',
                    year: 'numeric',
                  })}
                </td>
                <td className="px-4 py-3 text-right">{formatCurrency(r.bank_amount)}</td>
                <td className="px-4 py-3 text-right">{formatCurrency(r.system_amount)}</td>
                <td
                  className={clsx(
                    'px-4 py-3 text-right font-medium',
                    r.difference === 0 ? 'text-green-600' : 'text-red-600'
                  )}
                >
                  {r.difference === 0 ? '--' : formatCurrency(r.difference)}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={clsx(
                      'px-2 py-1 rounded text-xs font-medium',
                      r.status === 'matched'
                        ? 'bg-green-50/50 text-green-600'
                        : r.status === 'discrepancy'
                          ? 'bg-red-50/50 text-red-600'
                          : 'bg-amber-50/50 text-amber-600'
                    )}
                  >
                    {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {r.status !== 'matched' && (
                    <button
                      onClick={() => onReconcile(r.id)}
                      className="px-3 py-1.5 bg-green-600 text-white rounded text-xs hover:bg-green-700"
                    >
                      <CheckCircle className="w-3 h-3 inline mr-1" /> Match
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
