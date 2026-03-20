// ============================================================================
// IMS 2.0 - Outstanding & Collections Tab
// ============================================================================

import clsx from 'clsx';
import type { OutstandingReceivable, VendorPaymentData } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface OutstandingPanelProps {
  outstanding: OutstandingReceivable[];
  vendorPayments: VendorPaymentData[];
}

export default function OutstandingPanel({ outstanding, vendorPayments }: OutstandingPanelProps) {
  return (
    <div className="space-y-6">
      {/* Outstanding Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-gradient-to-br from-red-900 to-red-800 border border-red-700 rounded-lg p-6 text-white">
          <p className="text-red-200 text-sm font-medium">Total Outstanding</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(outstanding.reduce((sum, r) => sum + r.amount, 0))}
          </p>
          <p className="text-xs text-red-300 mt-2">{outstanding.length} customers</p>
        </div>

        <div className="bg-gradient-to-br from-orange-900 to-orange-800 border border-orange-700 rounded-lg p-6 text-white">
          <p className="text-orange-200 text-sm font-medium">Overdue Amount</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(
              outstanding
                .filter((r) => r.status === 'overdue')
                .reduce((sum, r) => sum + r.amount, 0)
            )}
          </p>
          <p className="text-xs text-orange-300 mt-2">
            {outstanding.filter((r) => r.status === 'overdue').length} overdue
          </p>
        </div>

        <div className="bg-gradient-to-br from-green-900 to-green-800 border border-green-700 rounded-lg p-6 text-white">
          <p className="text-green-200 text-sm font-medium">With GST</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(outstanding.reduce((sum, r) => sum + r.amount + r.gst_amount, 0))}
          </p>
          <p className="text-xs text-green-300 mt-2">Including tax</p>
        </div>
      </div>

      {/* Receivables Table */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold">Outstanding Receivables</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Customer</th>
                <th className="px-6 py-3 text-right text-slate-400">Amount</th>
                <th className="px-6 py-3 text-right text-slate-400">GST</th>
                <th className="px-6 py-3 text-left text-slate-400">Due Date</th>
                <th className="px-6 py-3 text-center text-slate-400">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {outstanding.map((item) => (
                <tr key={item.id} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4 font-medium">{item.customer_name}</td>
                  <td className="px-6 py-4 text-right font-semibold">
                    {formatCurrency(item.amount)}
                  </td>
                  <td className="px-6 py-4 text-right text-slate-400">
                    {formatCurrency(item.gst_amount)}
                  </td>
                  <td className="px-6 py-4 text-slate-400">{item.due_date}</td>
                  <td className="px-6 py-4 text-center">
                    <span
                      className={clsx(
                        'px-3 py-1 rounded-full text-xs font-semibold inline-block',
                        item.status === 'overdue'
                          ? 'bg-red-900/50 text-red-300 border border-red-700'
                          : 'bg-green-900/50 text-green-300 border border-green-700'
                      )}
                    >
                      {item.status === 'overdue'
                        ? `${item.days_overdue} days overdue`
                        : 'Active'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Vendor Payments */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold">Vendor Payment Schedule</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-slate-300">
            <thead className="bg-slate-900 border-b border-slate-700">
              <tr>
                <th className="px-6 py-3 text-left text-slate-400">Vendor</th>
                <th className="px-6 py-3 text-right text-slate-400">Amount Due</th>
                <th className="px-6 py-3 text-left text-slate-400">Due Date</th>
                <th className="px-6 py-3 text-center text-slate-400">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700">
              {vendorPayments.map((item) => (
                <tr key={item.id} className="hover:bg-slate-700/50 transition-colors">
                  <td className="px-6 py-4 font-medium">{item.vendor_name}</td>
                  <td className="px-6 py-4 text-right font-semibold">
                    {formatCurrency(item.amount_due)}
                  </td>
                  <td className="px-6 py-4 text-slate-400">{item.due_date}</td>
                  <td className="px-6 py-4 text-center">
                    <span
                      className={clsx(
                        'px-3 py-1 rounded-full text-xs font-semibold inline-block',
                        item.status === 'pending'
                          ? 'bg-yellow-900/50 text-yellow-300 border border-yellow-700'
                          : item.status === 'partial'
                            ? 'bg-blue-900/50 text-blue-300 border border-blue-700'
                            : 'bg-green-900/50 text-green-300 border border-green-700'
                      )}
                    >
                      {item.status === 'pending' ? 'Pending' : 'Partial'}
                    </span>
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
