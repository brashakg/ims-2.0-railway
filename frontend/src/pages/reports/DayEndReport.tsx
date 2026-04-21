// ============================================================================
// IMS 2.0 — Day-End Shift Closing Report
// ============================================================================
import { useState, useEffect, useMemo } from 'react';
import { useAuth } from '../../context/AuthContext';
import { orderApi } from '../../services/api';
import {
  IndianRupee, CreditCard, Phone, FileText,
  TrendingUp, Package, Printer,
  AlertTriangle, CheckCircle, ChevronDown, ChevronUp,
} from 'lucide-react';
import clsx from 'clsx';

interface DaySummary {
  totalSales: number;
  totalOrders: number;
  totalItems: number;
  cashCollected: number;
  upiCollected: number;
  cardCollected: number;
  bankTransfer: number;
  totalCollected: number;
  outstandingBalance: number;
  discountsGiven: number;
  taxCollected: number;
  avgBillValue: number;
  topProducts: Array<{ name: string; qty: number; revenue: number }>;
  staffSales: Array<{ name: string; orders: number; revenue: number }>;
  cancelledOrders: number;
  refundsProcessed: number;
}

export default function DayEndReport() {
  const { user } = useAuth();
  const [reportDate, setReportDate] = useState(new Date().toISOString().split('T')[0]);
  const [orders, setOrders] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [closingCash, setClosingCash] = useState('');
  const [closingNotes, setClosingNotes] = useState('');
  const [isClosed, setIsClosed] = useState(false);

  const storeId = user?.activeStoreId || user?.storeIds?.[0] || '';

  useEffect(() => {
    loadDayOrders();
  }, [reportDate]);

  const loadDayOrders = async () => {
    setIsLoading(true);
    try {
      const response = await orderApi.getOrders({ storeId, date: reportDate });
      setOrders(response.orders || response || []);
    } catch {
      setOrders([]);
    } finally {
      setIsLoading(false);
    }
  };

  const summary: DaySummary = useMemo(() => {
    const s: DaySummary = {
      totalSales: 0, totalOrders: 0, totalItems: 0,
      cashCollected: 0, upiCollected: 0, cardCollected: 0, bankTransfer: 0,
      totalCollected: 0, outstandingBalance: 0, discountsGiven: 0,
      taxCollected: 0, avgBillValue: 0, topProducts: [], staffSales: [],
      cancelledOrders: 0, refundsProcessed: 0,
    };

    const productMap = new Map<string, { name: string; qty: number; revenue: number }>();
    const staffMap = new Map<string, { name: string; orders: number; revenue: number }>();

    for (const order of orders) {
      if (order.orderStatus === 'CANCELLED') { s.cancelledOrders++; continue; }
      
      s.totalOrders++;
      s.totalSales += Math.round(order.grandTotal || 0);
      s.totalItems += (order.items || []).reduce((sum: number, i: any) => sum + (i.quantity || 1), 0);
      s.discountsGiven += Math.round(order.totalDiscount || 0);
      s.taxCollected += Math.round(order.taxAmount || 0);
      s.outstandingBalance += Math.round(order.balanceDue || 0);

      // Payment breakdown
      for (const p of (order.payments || [])) {
        const amt = Math.round(p.amount || 0);
        const method = (p.method || p.mode || '').toUpperCase();
        if (method === 'CASH') s.cashCollected += amt;
        else if (method === 'UPI') s.upiCollected += amt;
        else if (method === 'CARD') s.cardCollected += amt;
        else if (method === 'BANK_TRANSFER') s.bankTransfer += amt;
        s.totalCollected += amt;
      }

      // Product breakdown
      for (const item of (order.items || [])) {
        const name = item.productName || item.product_name || item.name || 'Unknown';
        const existing = productMap.get(name) || { name, qty: 0, revenue: 0 };
        existing.qty += item.quantity || 1;
        existing.revenue += Math.round(item.finalPrice || item.item_total || 0);
        productMap.set(name, existing);
      }

      // Staff breakdown
      const staffName = order.salespersonName || order.createdBy || 'Unknown';
      const staffEntry = staffMap.get(staffName) || { name: staffName, orders: 0, revenue: 0 };
      staffEntry.orders++;
      staffEntry.revenue += Math.round(order.grandTotal || 0);
      staffMap.set(staffName, staffEntry);
    }

    s.avgBillValue = s.totalOrders > 0 ? Math.round(s.totalSales / s.totalOrders) : 0;
    s.topProducts = [...productMap.values()].sort((a, b) => b.revenue - a.revenue).slice(0, 10);
    s.staffSales = [...staffMap.values()].sort((a, b) => b.revenue - a.revenue);

    return s;
  }, [orders]);

  const cashVariance = closingCash ? Math.round(parseFloat(closingCash) - summary.cashCollected) : null;

  const handleCloseDay = () => {
    // In production: POST to /api/v1/reports/day-end-close
    setIsClosed(true);
  };

  const handlePrint = () => {
    window.print();
  };

  const fc = (amount: number) => `₹${Math.round(amount).toLocaleString('en-IN')}`;

  return (
    <div className="max-w-4xl mx-auto p-4 tablet:p-6 space-y-6 print:p-0">
      {/* Header */}
      <div className="flex items-center justify-between print:hidden">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Day-End Closing Report</h1>
          <p className="text-sm text-gray-500 mt-1">Daily sales summary and cash reconciliation</p>
        </div>
        <div className="flex items-center gap-3">
          <input type="date" value={reportDate} onChange={e => setReportDate(e.target.value)} max={new Date().toISOString().split('T')[0]}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm" />
          <button onClick={handlePrint} className="flex items-center gap-1.5 px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">
            <Printer className="w-4 h-4" /> Print
          </button>
        </div>
      </div>

      {/* Print header */}
      <div className="hidden print:block text-center mb-6">
        <h2 className="text-lg font-bold">Better Vision Opticals — Day-End Report</h2>
        <p className="text-sm text-gray-600">{new Date(reportDate).toLocaleDateString('en-IN', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' })}</p>
      </div>

      {isLoading ? (
        <div className="text-center py-12"><div className="w-8 h-8 border-2 border-gray-200 border-t-bv-gold-500 rounded-full animate-spin mx-auto" /></div>
      ) : (
        <>
          {/* KPI Cards */}
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
            {[
              { label: 'Total Sales', value: fc(summary.totalSales), icon: TrendingUp, color: 'text-green-600 bg-green-50' },
              { label: 'Orders', value: String(summary.totalOrders), icon: Package, color: 'text-blue-600 bg-blue-50' },
              { label: 'Items Sold', value: String(summary.totalItems), icon: Package, color: 'text-purple-600 bg-purple-50' },
              { label: 'Avg Bill Value', value: fc(summary.avgBillValue), icon: IndianRupee, color: 'text-bv-red-600 bg-bv-gold-50' },
            ].map(card => (
              <div key={card.label} className="bg-white border border-gray-200 rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-gray-500">{card.label}</span>
                  <div className={clsx('w-8 h-8 rounded-lg flex items-center justify-center', card.color)}>
                    <card.icon className="w-4 h-4" />
                  </div>
                </div>
                <p className="text-xl font-bold text-gray-900">{card.value}</p>
              </div>
            ))}
          </div>

          {/* Payment Breakdown */}
          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <h3 className="font-semibold text-gray-900 mb-4">Payment Collection</h3>
            <div className="grid grid-cols-2 tablet:grid-cols-5 gap-4">
              {[
                { label: 'Cash', amount: summary.cashCollected, icon: IndianRupee, color: 'text-green-600' },
                { label: 'UPI', amount: summary.upiCollected, icon: Phone, color: 'text-purple-600' },
                { label: 'Card', amount: summary.cardCollected, icon: CreditCard, color: 'text-blue-600' },
                { label: 'Bank Transfer', amount: summary.bankTransfer, icon: FileText, color: 'text-gray-600' },
                { label: 'Total Collected', amount: summary.totalCollected, icon: TrendingUp, color: 'text-bv-red-600' },
              ].map(pm => (
                <div key={pm.label} className="text-center">
                  <pm.icon className={clsx('w-5 h-5 mx-auto mb-1', pm.color)} />
                  <p className="text-xs text-gray-500">{pm.label}</p>
                  <p className="text-lg font-bold text-gray-900">{fc(pm.amount)}</p>
                </div>
              ))}
            </div>

            {summary.outstandingBalance > 0 && (
              <div className="mt-4 pt-4 border-t border-gray-200 flex items-center justify-between">
                <span className="text-sm text-red-600 font-medium flex items-center gap-1"><AlertTriangle className="w-4 h-4" /> Outstanding Balance</span>
                <span className="text-lg font-bold text-red-600">{fc(summary.outstandingBalance)}</span>
              </div>
            )}

            <div className="mt-3 pt-3 border-t border-gray-100 flex items-center justify-between text-sm text-gray-500">
              <span>Discounts Given: {fc(summary.discountsGiven)}</span>
              <span>Tax Collected: {fc(summary.taxCollected)}</span>
            </div>
          </div>

          {/* Cash Reconciliation */}
          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <h3 className="font-semibold text-gray-900 mb-4">Cash Reconciliation</h3>
            <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4 items-end">
              <div>
                <p className="text-xs text-gray-500 mb-1">System Cash (from POS)</p>
                <p className="text-2xl font-bold text-gray-900">{fc(summary.cashCollected)}</p>
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Physical Cash Count</label>
                <input type="number" value={closingCash} onChange={e => setClosingCash(e.target.value)}
                  placeholder="Enter actual cash in drawer"
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm print:hidden" />
                {closingCash && <p className="text-sm font-medium mt-1 print:block hidden">{fc(parseFloat(closingCash))}</p>}
              </div>
              <div>
                {cashVariance !== null && (
                  <div className={clsx('p-3 rounded-lg', cashVariance === 0 ? 'bg-green-50' : 'bg-red-50')}>
                    <p className="text-xs text-gray-500">Variance</p>
                    <p className={clsx('text-xl font-bold', cashVariance === 0 ? 'text-green-600' : 'text-red-600')}>
                      {cashVariance === 0 ? '✓ Matches' : `${cashVariance > 0 ? '+' : ''}${fc(cashVariance)}`}
                    </p>
                    {cashVariance !== 0 && <p className="text-xs text-red-500 mt-0.5">{cashVariance > 0 ? 'Excess cash' : 'Cash short'}</p>}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Staff Performance */}
          {summary.staffSales.length > 0 && (
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <h3 className="font-semibold text-gray-900 mb-4">Staff Performance</h3>
              <div className="space-y-2">
                {summary.staffSales.map((staff, i) => (
                  <div key={staff.name} className="flex items-center justify-between py-2 px-3 rounded-lg bg-gray-50">
                    <div className="flex items-center gap-3">
                      <span className="w-6 h-6 rounded-full bg-bv-gold-100 text-bv-gold-700 text-xs font-bold flex items-center justify-center">{i + 1}</span>
                      <span className="font-medium text-sm">{staff.name}</span>
                    </div>
                    <div className="text-right">
                      <span className="font-bold text-sm">{fc(staff.revenue)}</span>
                      <span className="text-xs text-gray-500 ml-2">({staff.orders} orders)</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Top Products */}
          {summary.topProducts.length > 0 && (
            <div className="bg-white border border-gray-200 rounded-xl p-5">
              <button onClick={() => setShowDetails(!showDetails)} className="flex items-center justify-between w-full">
                <h3 className="font-semibold text-gray-900">Top Products Sold</h3>
                {showDetails ? <ChevronUp className="w-5 h-5 text-gray-500" /> : <ChevronDown className="w-5 h-5 text-gray-500" />}
              </button>
              {showDetails && (
                <div className="mt-3 space-y-1.5">
                  {summary.topProducts.map(p => (
                    <div key={p.name} className="flex items-center justify-between text-sm py-1.5 px-2 rounded hover:bg-gray-50">
                      <span className="text-gray-700 truncate flex-1 mr-4">{p.name}</span>
                      <div className="flex items-center gap-4 text-right">
                        <span className="text-gray-500 text-xs">{p.qty} pcs</span>
                        <span className="font-medium w-24 text-right">{fc(p.revenue)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Closing Action */}
          {!isClosed ? (
            <div className="bg-white border border-gray-200 rounded-xl p-5 print:hidden">
              <h3 className="font-semibold text-gray-900 mb-3">Close Day</h3>
              <textarea value={closingNotes} onChange={e => setClosingNotes(e.target.value)}
                placeholder="Add any notes about today's shift (optional)..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm h-20 resize-none mb-3" />
              <button onClick={handleCloseDay}
                className="w-full py-3 bg-bv-red-600 text-white rounded-lg font-semibold hover:bg-bv-red-700 flex items-center justify-center gap-2">
                <CheckCircle className="w-5 h-5" /> Confirm Day Closing
              </button>
            </div>
          ) : (
            <div className="bg-green-50 border border-green-200 rounded-xl p-5 text-center">
              <CheckCircle className="w-8 h-8 text-green-500 mx-auto mb-2" />
              <p className="font-semibold text-green-700">Day Closed Successfully</p>
              <p className="text-sm text-green-600 mt-1">{new Date().toLocaleString('en-IN')}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
