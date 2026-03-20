// ============================================================================
// IMS 2.0 - Vendor Payments Tab
// ============================================================================

import { Wallet } from 'lucide-react';
import clsx from 'clsx';
import type { VendorPaymentData } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface VendorPaymentsProps {
  vendorPayments: VendorPaymentData[];
  onPayVendor: (vendorName: string) => void;
}

export default function VendorPayments({ vendorPayments, onPayVendor }: VendorPaymentsProps) {
  const totalDue = vendorPayments.reduce((s, v) => s + v.amount_due, 0);
  const overdueCount = vendorPayments.filter((v) => v.days_overdue > 0).length;

  return (
    <div className="space-y-6">
      {/* Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
          <p className="text-sm text-slate-400">Total Payable</p>
          <p className="text-2xl font-bold text-white mt-1">{formatCurrency(totalDue)}</p>
        </div>
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
          <p className="text-sm text-slate-400">Vendors with Dues</p>
          <p className="text-2xl font-bold text-amber-400 mt-1">{vendorPayments.length}</p>
        </div>
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-4">
          <p className="text-sm text-slate-400">Overdue</p>
          <p className="text-2xl font-bold text-red-400 mt-1">{overdueCount}</p>
        </div>
      </div>

      <h3 className="text-lg font-semibold text-white">Vendor Payment Schedule</h3>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400 text-left">
            <tr>
              <th className="px-4 py-3">Vendor</th>
              <th className="px-4 py-3 text-right">Amount Due</th>
              <th className="px-4 py-3">Due Date</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Overdue</th>
              <th className="px-4 py-3">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {vendorPayments.map((v) => (
              <tr key={v.id} className="text-white">
                <td className="px-4 py-3 font-medium">{v.vendor_name}</td>
                <td className="px-4 py-3 text-right font-semibold">
                  {formatCurrency(v.amount_due)}
                </td>
                <td className="px-4 py-3 text-slate-300">
                  {new Date(v.due_date).toLocaleDateString('en-IN', {
                    day: 'numeric',
                    month: 'short',
                    year: 'numeric',
                  })}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={clsx(
                      'px-2 py-1 rounded text-xs font-medium',
                      v.status === 'paid'
                        ? 'bg-green-900/50 text-green-400'
                        : v.status === 'partial'
                          ? 'bg-amber-900/50 text-amber-400'
                          : 'bg-slate-700 text-slate-300'
                    )}
                  >
                    {v.status.charAt(0).toUpperCase() + v.status.slice(1)}
                  </span>
                </td>
                <td
                  className={clsx(
                    'px-4 py-3 text-right',
                    v.days_overdue > 0 ? 'text-red-400 font-medium' : 'text-slate-400'
                  )}
                >
                  {v.days_overdue > 0 ? `${v.days_overdue} days` : '--'}
                </td>
                <td className="px-4 py-3">
                  <button
                    onClick={() => onPayVendor(v.vendor_name)}
                    className="px-3 py-1.5 bg-green-600 text-white rounded text-xs hover:bg-green-700"
                  >
                    <Wallet className="w-3 h-3 inline mr-1" /> Pay
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
