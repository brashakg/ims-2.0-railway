// ============================================================================
// IMS 2.0 - Vendor Payments Tab
// ============================================================================

import { useEffect, useState } from 'react';
import clsx from 'clsx';
import { storeApi } from '../../services/api/stores';
import { normalizeStore } from '../../utils/storeAccess';
import type { VendorPaymentData } from './financeTypes';
import { formatCurrency } from './financeUtils';
import { PurchaseShopGate, usePurchaseShop } from '../purchase/purchaseShop';

interface VendorPaymentsProps {
  vendorPayments: VendorPaymentData[];
}

/** THE status of a supplier's ledger balance, on both Finance tabs (this one
 *  and Outstanding's payment schedule). The balance's sign decides first:
 *  below zero we paid ahead (an advance with the supplier), zero is settled.
 *  Only money we still owe is part-paid or unpaid. */
export function SupplierStatusBadge({ vendor }: { vendor: VendorPaymentData }) {
  const [label, tone] =
    vendor.amount_due < 0
      ? ['Advance', 'bg-blue-50 text-blue-700 border-blue-200']
      : vendor.amount_due === 0
        ? ['Settled', 'bg-green-50 text-green-700 border-green-200']
        : vendor.status === 'partial'
          ? ['Partial', 'bg-amber-50 text-amber-700 border-amber-200']
          : ['Pending', 'bg-gray-100 text-slate-700 border-gray-200'];
  return (
    <span className={clsx('px-2 py-1 rounded text-xs font-medium border inline-block', tone)}>
      {label}
    </span>
  );
}

/** Which shop the supplier figures cover: the one Purchase shop scope
 *  (usePurchaseShop) -- an admin's Purchase pick, every shop until he picks
 *  one; anyone else's own shop. Named from the store list, its id until that
 *  arrives. */
export function SupplierFiguresShop() {
  const storeId = usePurchaseShop().storeId || '';
  // The name found for a shop id; a stale answer for another id is ignored.
  const [named, setNamed] = useState<{ id: string; name: string } | null>(null);
  const name = named?.id === storeId ? named.name : '';
  useEffect(() => {
    if (!storeId) return;
    let cancelled = false;
    storeApi
      .getStores()
      .then((res: unknown) => {
        const body = res as { stores?: unknown } | unknown[] | null;
        const list = Array.isArray(body) ? body : Array.isArray(body?.stores) ? body.stores : [];
        const row = (list as Record<string, unknown>[])
          .map((s) => normalizeStore(s))
          .find((s) => s?.id === storeId);
        if (!cancelled && row) setNamed({ id: storeId, name: row.name });
      })
      .catch(() => {
        /* the id stands in for the name */
      });
    return () => {
      cancelled = true;
    };
  }, [storeId]);
  return (
    <p className="text-xs text-slate-600 mt-1">
      {storeId ? (
        <>
          Shop: <span className="font-medium text-gray-900">{name || storeId}</span>
        </>
      ) : (
        'All shops'
      )}
    </p>
  );
}

/** THE ONE 'WE OWE' RULE (audit F56), from the signed per-supplier ledger
 *  balances: what we owe is the sum of the balances above 0; money paid ahead
 *  to suppliers (the balances below 0) is a figure apart, never taken off it --
 *  one supplier's advance does not pay another's bills. Each row keeps its own
 *  signed balance. */
function owedAndAdvances(vendorPayments: VendorPaymentData[]) {
  let owed = 0;
  let advances = 0;
  for (const v of vendorPayments) {
    if (v.amount_due > 0) owed += v.amount_due;
    else if (v.amount_due < 0) advances -= v.amount_due;
  }
  return { owed, advances };
}

export default function VendorPayments({ vendorPayments }: VendorPaymentsProps) {
  const { owed: totalDue, advances } = owedAndAdvances(vendorPayments);
  // Only a supplier we still owe has a due: an advance or a settled account
  // is not one (same sign rule as the status badge).
  const withDues = vendorPayments.filter((v) => v.amount_due > 0).length;
  const overdueCount = vendorPayments.filter((v) => v.days_overdue > 0).length;

  // R3: a non-admin login with no shop reads the plain message, never an
  // 'All shops' Rs 0 (the server refused the read).
  return (
    <PurchaseShopGate>
    <div className="space-y-6">
      <SupplierFiguresShop />
      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4" data-testid="vp-total-payable">
          <p className="text-sm text-slate-600">Total Payable</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{formatCurrency(totalDue)}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4" data-testid="vp-advances">
          <p className="text-sm text-slate-600">Advances</p>
          <p className="text-2xl font-bold text-blue-700 mt-1">{formatCurrency(advances)}</p>
          <p className="text-xs text-slate-500 mt-1">Paid ahead to suppliers; not taken off the Total Payable</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-sm text-slate-600">Vendors with Dues</p>
          <p className="text-2xl font-bold text-amber-600 mt-1">{withDues}</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <p className="text-sm text-slate-600">Overdue</p>
          <p className="text-2xl font-bold text-red-600 mt-1">{overdueCount}</p>
        </div>
      </div>

      <h3 className="text-lg font-semibold text-gray-900">Vendor Payment Schedule</h3>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-left">
            <tr>
              <th className="px-4 py-3">Vendor</th>
              <th className="px-4 py-3 text-right">Amount Due</th>
              <th className="px-4 py-3">Due Date</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Overdue</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {vendorPayments.map((v) => (
              <tr key={v.id} className="text-gray-900">
                <td className="px-4 py-3 font-medium">{v.vendor_name}</td>
                <td className="px-4 py-3 text-right font-semibold">
                  {formatCurrency(v.amount_due)}
                </td>
                <td className="px-4 py-3 text-slate-700">
                  {v.due_date
                    ? new Date(v.due_date).toLocaleDateString('en-IN', {
                        day: 'numeric',
                        month: 'short',
                        year: 'numeric',
                      })
                    : '--'}
                </td>
                <td className="px-4 py-3">
                  <SupplierStatusBadge vendor={v} />
                </td>
                <td
                  className={clsx(
                    'px-4 py-3 text-right',
                    v.days_overdue > 0 ? 'text-red-600 font-medium' : 'text-slate-600'
                  )}
                >
                  {v.days_overdue > 0 ? `${v.days_overdue} days` : '--'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
    </PurchaseShopGate>
  );
}
