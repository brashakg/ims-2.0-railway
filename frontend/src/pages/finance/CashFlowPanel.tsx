// ============================================================================
// IMS 2.0 - Cash Flow Tab
// ============================================================================

import type { CashFlowData } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface CashFlowPanelProps {
  cashFlow: CashFlowData[];
}

export default function CashFlowPanel({ cashFlow }: CashFlowPanelProps) {
  return (
    <div className="space-y-6">
      {/* Cash Flow Summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-6 text-gray-900">
          <p className="text-emerald-700 text-sm font-medium">Current Cash Balance</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow[cashFlow.length - 1]?.closing_balance || 0)}
          </p>
          <p className="text-xs text-emerald-700 mt-2">Latest month closing</p>
        </div>

        <div className="bg-green-50 border border-green-200 rounded-lg p-6 text-gray-900">
          <p className="text-green-700 text-sm font-medium">Total Cash Inflows</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow.reduce((sum, cf) => sum + cf.cash_inflows, 0))}
          </p>
          <p className="text-xs text-green-700 mt-2">3-month period</p>
        </div>

        <div className="bg-red-50 border border-red-200 rounded-lg p-6 text-gray-900">
          <p className="text-red-700 text-sm font-medium">Total Cash Outflows</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(cashFlow.reduce((sum, cf) => sum + cf.cash_outflows, 0))}
          </p>
          <p className="text-xs text-red-700 mt-2">3-month period</p>
        </div>
      </div>

      {/* Cash Flow Trend */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="bg-slate-50 px-6 py-4 border-b border-gray-200">
          <h3 className="text-gray-900 font-semibold">Cash Flow Analysis</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-700">
            <thead className="bg-slate-50 border-b border-gray-200">
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
      {/* The "Bank Reconciliation Status" block was removed: it fabricated
          bank/system/difference as 0 with a hardcoded "Reconciled" badge and a
          no-op "Mark Matched" button. There is no bank-statement reconciliation
          backend (SYSTEM_INTENT: never show fabricated money). */}
    </div>
  );
}
