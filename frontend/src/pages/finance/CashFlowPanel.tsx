// ============================================================================
// IMS 2.0 - Cash Flow Tab
// ============================================================================

import { CreditCard } from 'lucide-react';
import clsx from 'clsx';
import type { CashFlowData, ReconciliationData } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface CashFlowPanelProps {
  cashFlow: CashFlowData[];
  reconciliation: ReconciliationData[];
  onReconcile: (itemId: string) => void;
}

export default function CashFlowPanel({ cashFlow, reconciliation, onReconcile }: CashFlowPanelProps) {
  return (
    <div className="space-y-6">
      {/* Cash Flow Summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="bg-gradient-to-br from-emerald-900 to-emerald-800 border border-emerald-700 rounded-lg p-6 text-gray-900">
          <p className="text-emerald-200 text-sm font-medium">Current Cash Balance</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow[cashFlow.length - 1]?.closing_balance || 0)}
          </p>
          <p className="text-xs text-emerald-700 mt-2">Latest month closing</p>
        </div>

        <div className="bg-gradient-to-br from-green-900 to-green-800 border border-green-700 rounded-lg p-6 text-gray-900">
          <p className="text-green-200 text-sm font-medium">Total Cash Inflows</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow.reduce((sum, cf) => sum + cf.cash_inflows, 0))}
          </p>
          <p className="text-xs text-green-700 mt-2">3-month period</p>
        </div>

        <div className="bg-gradient-to-br from-red-900 to-red-800 border border-red-700 rounded-lg p-6 text-gray-900">
          <p className="text-red-200 text-sm font-medium">Total Cash Outflows</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow.reduce((sum, cf) => sum + cf.cash_outflows, 0))}
          </p>
          <p className="text-xs text-red-700 mt-2">3-month period</p>
        </div>
      </div>

      {/* Cash Flow Trend */}
      <div className="bg-white border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-50 px-6 py-4 border-b border-slate-700">
          <h3 className="text-gray-900 font-semibold">Cash Flow Analysis</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-700">
            <thead className="bg-slate-50 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-600">Period</th>
                <th className="px-6 py-3 text-right text-slate-600">Opening Balance</th>
                <th className="px-6 py-3 text-right text-slate-600">Inflows</th>
                <th className="px-6 py-3 text-right text-slate-600">Outflows</th>
                <th className="px-6 py-3 text-right text-slate-600">Closing Balance</th>
                <th className="px-6 py-3 text-right text-slate-600">Free Cash Flow</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {cashFlow.map((row) => (
                <tr key={row.period} className="hover:bg-gray-100/50 transition-colors">
                  <td className="px-6 py-4 font-medium">{row.period}</td>
                  <td className="px-6 py-4 text-right text-slate-600">
                    {formatCurrency(row.opening_balance)}
                  </td>
                  <td className="px-6 py-4 text-right font-medium text-green-600">
                    {formatCurrency(row.cash_inflows)}
                  </td>
                  <td className="px-6 py-4 text-right font-medium text-red-600">
                    {formatCurrency(row.cash_outflows)}
                  </td>
                  <td className="px-6 py-4 text-right font-semibold text-emerald-600">
                    {formatCurrency(row.closing_balance)}
                  </td>
                  <td className="px-6 py-4 text-right font-semibold text-blue-600">
                    {formatCurrency(row.free_cash_flow)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Bank Reconciliation */}
      <div className="bg-white border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-50 px-6 py-4 border-b border-slate-700 flex items-center justify-between">
          <h3 className="text-gray-900 font-semibold flex items-center gap-2">
            <CreditCard className="w-5 h-5 text-cyan-600" />
            Bank Reconciliation Status
          </h3>
          <span className="text-xs bg-green-50/50 text-green-700 px-3 py-1 rounded-full border border-green-700">
            Reconciled
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-700">
            <thead className="bg-slate-50 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-600">Date</th>
                <th className="px-6 py-3 text-right text-slate-600">Bank Amount</th>
                <th className="px-6 py-3 text-right text-slate-600">System Amount</th>
                <th className="px-6 py-3 text-right text-slate-600">Difference</th>
                <th className="px-6 py-3 text-center text-slate-600">Status</th>
                <th className="px-6 py-3 text-center text-slate-600">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {reconciliation.map((item) => (
                <tr key={item.id} className="hover:bg-gray-100/50 transition-colors">
                  <td className="px-6 py-4">{item.date}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(item.bank_amount)}</td>
                  <td className="px-6 py-4 text-right">{formatCurrency(item.system_amount)}</td>
                  <td
                    className={clsx(
                      'px-6 py-4 text-right font-semibold',
                      item.difference === 0
                        ? 'text-green-600'
                        : 'text-red-600'
                    )}
                  >
                    {item.difference === 0 ? 'Match' : formatCurrency(item.difference)}
                  </td>
                  <td className="px-6 py-4 text-center">
                    <span
                      className={clsx(
                        'px-3 py-1 rounded-full text-xs font-semibold inline-block',
                        item.status === 'matched'
                          ? 'bg-green-50/50 text-green-700 border border-green-700'
                          : 'bg-yellow-50/50 text-yellow-700 border border-yellow-700'
                      )}
                    >
                      {item.status === 'matched' ? 'Matched' : 'Pending'}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-center">
                    {item.status === 'pending' && (
                      <button
                        onClick={() => onReconcile(item.id)}
                        className="text-blue-600 hover:text-blue-700 transition-colors text-xs font-medium"
                      >
                        Mark Matched
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
