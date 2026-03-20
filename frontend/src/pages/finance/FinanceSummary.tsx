// ============================================================================
// IMS 2.0 - Revenue & P&L Tab
// ============================================================================

import { BarChart3, TrendingUp, TrendingDown, FileText } from 'lucide-react';
import type { RevenueData, ProfitLossStatement } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface FinanceSummaryProps {
  revenueData: RevenueData[];
  plStatement: ProfitLossStatement | null;
}

export default function FinanceSummary({ revenueData, plStatement }: FinanceSummaryProps) {
  return (
    <div className="space-y-6">
      {/* Revenue Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-gradient-to-br from-green-900 to-green-800 border border-green-700 rounded-lg p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-green-200 text-sm font-medium">Total Revenue</p>
              <p className="text-2xl font-bold mt-2">{formatCurrency(1401000)}</p>
              <p className="text-xs text-green-300 mt-2">Apr - Jun 2025</p>
            </div>
            <TrendingUp className="w-10 h-10 text-green-400 opacity-50" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-blue-900 to-blue-800 border border-blue-700 rounded-lg p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-blue-200 text-sm font-medium">Gross Profit</p>
              <p className="text-2xl font-bold mt-2">{formatCurrency(840600)}</p>
              <p className="text-xs text-blue-300 mt-2">59.9% margin</p>
            </div>
            <BarChart3 className="w-10 h-10 text-blue-400 opacity-50" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-purple-900 to-purple-800 border border-purple-700 rounded-lg p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-purple-200 text-sm font-medium">Net Profit</p>
              <p className="text-2xl font-bold mt-2">{formatCurrency(516969)}</p>
              <p className="text-xs text-purple-300 mt-2">36.9% margin</p>
            </div>
            <TrendingUp className="w-10 h-10 text-purple-400 opacity-50" />
          </div>
        </div>

        <div className="bg-gradient-to-br from-orange-900 to-orange-800 border border-orange-700 rounded-lg p-6 text-white">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-orange-200 text-sm font-medium">Operating Expense</p>
              <p className="text-2xl font-bold mt-2">{formatCurrency(210150)}</p>
              <p className="text-xs text-orange-300 mt-2">15% of revenue</p>
            </div>
            <TrendingDown className="w-10 h-10 text-orange-400 opacity-50" />
          </div>
        </div>
      </div>

      {/* P&L Statement Detail */}
      {plStatement && (
        <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
          <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
            <h3 className="text-white font-semibold flex items-center gap-2">
              <FileText className="w-5 h-5 text-blue-400" />
              Profit & Loss Statement
            </h3>
          </div>
          <div className="p-6 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-slate-900 p-4 rounded border border-slate-700">
                <p className="text-slate-400 text-sm">Revenue</p>
                <p className="text-white text-lg font-semibold mt-1">
                  {formatCurrency(plStatement.revenue)}
                </p>
              </div>
              <div className="bg-slate-900 p-4 rounded border border-slate-700">
                <p className="text-slate-400 text-sm">Cost of Goods Sold</p>
                <p className="text-red-400 text-lg font-semibold mt-1">
                  {formatCurrency(plStatement.cost_of_goods)}
                </p>
              </div>

              <div className="col-span-2 bg-slate-700 p-4 rounded border border-slate-600">
                <p className="text-slate-300 text-sm">Gross Profit</p>
                <p className="text-green-400 text-xl font-semibold mt-1">
                  {formatCurrency(plStatement.gross_profit)}
                </p>
              </div>

              <div className="bg-slate-900 p-4 rounded border border-slate-700">
                <p className="text-slate-400 text-sm">Operating Expenses</p>
                <p className="text-red-400 text-lg font-semibold mt-1">
                  {formatCurrency(plStatement.operating_expenses)}
                </p>
              </div>
              <div className="bg-slate-900 p-4 rounded border border-slate-700">
                <p className="text-slate-400 text-sm">Tax Expense</p>
                <p className="text-red-400 text-lg font-semibold mt-1">
                  {formatCurrency(plStatement.tax_expense)}
                </p>
              </div>

              <div className="col-span-2 bg-gradient-to-r from-green-900 to-green-800 p-4 rounded border border-green-700">
                <p className="text-green-200 text-sm font-medium">Net Profit</p>
                <p className="text-green-300 text-2xl font-bold mt-1">
                  {formatCurrency(plStatement.net_profit)}
                </p>
                <p className="text-green-400 text-xs mt-2">
                  Profit Margin: {plStatement.profit_margin.toFixed(2)}%
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Monthly Revenue Breakdown */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold">Monthly Revenue Breakdown</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Period</th>
                <th className="px-6 py-3 text-right text-slate-400">Gross Sales</th>
                <th className="px-6 py-3 text-right text-slate-400">Deductions</th>
                <th className="px-6 py-3 text-right text-slate-400">Net Revenue</th>
                <th className="px-6 py-3 text-right text-slate-400">GST Collected</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {revenueData.map((row) => (
                <tr key={row.period} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4">{row.period}</td>
                  <td className="px-6 py-4 text-right font-medium">
                    {formatCurrency(row.gross_sales)}
                  </td>
                  <td className="px-6 py-4 text-right text-red-400">
                    -{formatCurrency(row.deductions)}
                  </td>
                  <td className="px-6 py-4 text-right font-medium text-green-400">
                    {formatCurrency(row.net_revenue)}
                  </td>
                  <td className="px-6 py-4 text-right font-medium text-blue-400">
                    {formatCurrency(row.gst_collected)}
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
