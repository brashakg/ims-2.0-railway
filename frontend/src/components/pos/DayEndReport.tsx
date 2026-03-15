// ============================================================================
// IMS 2.0 - Day-End Z-Report / Shift Cash Summary
// ============================================================================
// Accountant-grade daily summary: sales, tax, payment mode breakup, cash reconciliation
// Used by cashiers at shift close and accountants for daily JV posting

import { useState, useEffect, useMemo } from 'react';
import {
  Printer,
  X,
  IndianRupee,
  CreditCard,
  Phone,
  FileText,
  AlertTriangle,
  CheckCircle,
  TrendingUp,
  Receipt,
} from 'lucide-react';
import { orderApi } from '../../services/api';
import { getGSTRateByCategory, getHSNByCategory } from '../../constants/gst';
import { useAuth } from '../../context/AuthContext';

interface DayEndReportProps {
  storeId: string;
  onClose: () => void;
}

interface OrderSummary {
  totalOrders: number;
  grossSales: number;
  totalDiscount: number;
  taxableValue: number;
  cgst: number;
  sgst: number;
  igst: number;
  grandTotal: number;
  // Payment mode breakup
  cashCollected: number;
  upiCollected: number;
  cardCollected: number;
  bankCollected: number;
  totalCollected: number;
  totalOutstanding: number;
  // Category breakup
  categoryBreakup: Record<string, { count: number; value: number; tax: number }>;
  // HSN summary
  hsnSummary: Record<string, { hsn: string; description: string; taxableValue: number; rate: number; cgst: number; sgst: number }>;
  // Order list for reference
  orders: any[];
}

export function DayEndReport({ storeId, onClose }: DayEndReportProps) {
  const { user } = useAuth();
  const [date, setDate] = useState(new Date().toISOString().split('T')[0]);
  const [loading, setLoading] = useState(true);
  const [orders, setOrders] = useState<any[]>([]);
  const [openingFloat, setOpeningFloat] = useState('5000');
  const [actualCash, setActualCash] = useState('');

  useEffect(() => {
    loadOrders();
  }, [date, storeId]);

  const loadOrders = async () => {
    setLoading(true);
    try {
      const result = await orderApi.getOrders({
        store_id: storeId,
        date_from: date,
        date_to: date,
        page_size: 500,
      } as any);
      setOrders(result?.orders || result || []);
    } catch {
      setOrders([]);
    }
    setLoading(false);
  };

  const summary: OrderSummary = useMemo(() => {
    let grossSales = 0;
    let totalDiscount = 0;
    let taxableValue = 0;
    let cgst = 0;
    let sgst = 0;
    let cashCollected = 0;
    let upiCollected = 0;
    let cardCollected = 0;
    let bankCollected = 0;
    let totalOutstanding = 0;
    const categoryBreakup: Record<string, { count: number; value: number; tax: number }> = {};
    const hsnSummary: Record<string, { hsn: string; description: string; taxableValue: number; rate: number; cgst: number; sgst: number }> = {};

    for (const order of orders) {
      const items = order.items || [];
      for (const item of items) {
        const qty = item.quantity || 1;
        const unitPrice = item.unitPrice || item.unit_price || 0;
        const discount = item.discountAmount || item.discount_amount || 0;
        const lineTotal = (item.finalPrice || item.item_total || item.line_total || unitPrice * qty) - discount;
        const cat = item.category || 'FRAMES';
        const gstRate = getGSTRateByCategory(cat);
        const lineTax = Math.round(lineTotal * (gstRate / 100) * 100) / 100;

        grossSales += unitPrice * qty;
        totalDiscount += discount;
        taxableValue += lineTotal;
        cgst += lineTax / 2;
        sgst += lineTax / 2;

        // Category breakup
        if (!categoryBreakup[cat]) categoryBreakup[cat] = { count: 0, value: 0, tax: 0 };
        categoryBreakup[cat].count += qty;
        categoryBreakup[cat].value += lineTotal;
        categoryBreakup[cat].tax += lineTax;

        // HSN summary
        const hsnInfo = getHSNByCategory(cat, true);
        const hsnCode = hsnInfo?.code || '9004';
        if (!hsnSummary[hsnCode]) {
          hsnSummary[hsnCode] = {
            hsn: hsnCode,
            description: hsnInfo?.description || cat,
            taxableValue: 0,
            rate: gstRate,
            cgst: 0,
            sgst: 0,
          };
        }
        hsnSummary[hsnCode].taxableValue += lineTotal;
        hsnSummary[hsnCode].cgst += lineTax / 2;
        hsnSummary[hsnCode].sgst += lineTax / 2;
      }

      // Payment breakup
      const payments = order.payments || [];
      for (const p of payments) {
        const method = (p.mode || p.method || '').toUpperCase();
        const amt = p.amount || 0;
        if (method === 'CASH') cashCollected += amt;
        else if (method === 'UPI') upiCollected += amt;
        else if (method === 'CARD') cardCollected += amt;
        else if (method === 'BANK_TRANSFER' || method === 'BANK') bankCollected += amt;
      }

      totalOutstanding += order.balanceDue || order.balance_due || 0;
    }

    const totalCollected = cashCollected + upiCollected + cardCollected + bankCollected;
    const grandTotal = Math.round((taxableValue + cgst + sgst) * 100) / 100;

    return {
      totalOrders: orders.length,
      grossSales: Math.round(grossSales * 100) / 100,
      totalDiscount: Math.round(totalDiscount * 100) / 100,
      taxableValue: Math.round(taxableValue * 100) / 100,
      cgst: Math.round(cgst * 100) / 100,
      sgst: Math.round(sgst * 100) / 100,
      igst: 0,
      grandTotal,
      cashCollected: Math.round(cashCollected * 100) / 100,
      upiCollected: Math.round(upiCollected * 100) / 100,
      cardCollected: Math.round(cardCollected * 100) / 100,
      bankCollected: Math.round(bankCollected * 100) / 100,
      totalCollected: Math.round(totalCollected * 100) / 100,
      totalOutstanding: Math.round(totalOutstanding * 100) / 100,
      categoryBreakup,
      hsnSummary,
      orders,
    };
  }, [orders]);

  const floatAmt = parseFloat(openingFloat) || 0;
  const expectedCash = floatAmt + summary.cashCollected;
  const actualCashAmt = parseFloat(actualCash) || 0;
  const cashVariance = actualCash ? actualCashAmt - expectedCash : 0;

  const fmt = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`;

  const handlePrint = () => {
    window.print();
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between no-print z-10">
          <div>
            <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
              <Receipt className="w-5 h-5 text-bv-gold-500" /> Day-End Z-Report
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {user?.name} · {storeId} · Generated {new Date().toLocaleTimeString('en-IN')}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
              className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm" />
            <button onClick={handlePrint} className="px-3 py-1.5 bg-bv-gold-500 text-white rounded-lg text-sm font-medium hover:bg-bv-gold-600 flex items-center gap-1">
              <Printer className="w-4 h-4" /> Print
            </button>
            <button onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
          </div>
        </div>

        {loading ? (
          <div className="p-12 text-center text-gray-400">Loading...</div>
        ) : (
          <div className="p-6 space-y-6 print:p-2 print:space-y-3">
            {/* SECTION 1: Sales Summary */}
            <section>
              <h3 className="text-sm font-bold text-gray-900 uppercase tracking-wide border-b pb-1 mb-3 flex items-center gap-2">
                <TrendingUp className="w-4 h-4" /> Sales Summary
              </h3>
              <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
                <div className="flex justify-between"><span className="text-gray-600">Total Orders</span><span className="font-semibold">{summary.totalOrders}</span></div>
                <div className="flex justify-between"><span className="text-gray-600">Gross Sales</span><span className="font-semibold">{fmt(summary.grossSales)}</span></div>
                <div className="flex justify-between"><span className="text-gray-600">Total Discounts</span><span className="text-red-600">-{fmt(summary.totalDiscount)}</span></div>
                <div className="flex justify-between"><span className="text-gray-600">Taxable Value</span><span className="font-semibold">{fmt(summary.taxableValue)}</span></div>
                <div className="flex justify-between"><span className="text-gray-600">CGST</span><span>{fmt(summary.cgst)}</span></div>
                <div className="flex justify-between"><span className="text-gray-600">SGST</span><span>{fmt(summary.sgst)}</span></div>
                {summary.igst > 0 && <div className="flex justify-between"><span className="text-gray-600">IGST</span><span>{fmt(summary.igst)}</span></div>}
                <div className="flex justify-between border-t pt-1 col-span-2"><span className="font-bold text-gray-900">Grand Total (Billed)</span><span className="font-bold text-lg text-bv-gold-600">{fmt(summary.grandTotal)}</span></div>
              </div>
            </section>

            {/* SECTION 2: Payment Mode Breakup */}
            <section>
              <h3 className="text-sm font-bold text-gray-900 uppercase tracking-wide border-b pb-1 mb-3 flex items-center gap-2">
                <IndianRupee className="w-4 h-4" /> Payment Mode Breakup
              </h3>
              <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-sm">
                <div className="flex justify-between"><span className="text-gray-600 flex items-center gap-1"><IndianRupee className="w-3 h-3" /> Cash</span><span className="font-semibold">{fmt(summary.cashCollected)}</span></div>
                <div className="flex justify-between"><span className="text-gray-600 flex items-center gap-1"><Phone className="w-3 h-3" /> UPI</span><span className="font-semibold">{fmt(summary.upiCollected)}</span></div>
                <div className="flex justify-between"><span className="text-gray-600 flex items-center gap-1"><CreditCard className="w-3 h-3" /> Card</span><span className="font-semibold">{fmt(summary.cardCollected)}</span></div>
                <div className="flex justify-between"><span className="text-gray-600 flex items-center gap-1"><FileText className="w-3 h-3" /> Bank Transfer</span><span className="font-semibold">{fmt(summary.bankCollected)}</span></div>
                <div className="flex justify-between border-t pt-1"><span className="font-bold">Total Collected</span><span className="font-bold text-green-700">{fmt(summary.totalCollected)}</span></div>
                <div className="flex justify-between border-t pt-1"><span className="font-bold">Outstanding</span><span className={`font-bold ${summary.totalOutstanding > 0 ? 'text-red-600' : 'text-green-700'}`}>{fmt(summary.totalOutstanding)}</span></div>
              </div>
            </section>

            {/* SECTION 3: Cash Reconciliation */}
            <section className="bg-gray-50 rounded-xl p-4 no-print">
              <h3 className="text-sm font-bold text-gray-900 uppercase tracking-wide mb-3">Cash Drawer Reconciliation</h3>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Opening Float</label>
                  <div className="flex items-center">
                    <span className="text-gray-400 mr-1">₹</span>
                    <input type="number" value={openingFloat} onChange={(e) => setOpeningFloat(e.target.value)}
                      className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-right" />
                  </div>
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Expected Cash</label>
                  <p className="px-2 py-1.5 bg-white border border-gray-200 rounded text-sm text-right font-semibold">{fmt(expectedCash)}</p>
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">Actual Cash Count</label>
                  <div className="flex items-center">
                    <span className="text-gray-400 mr-1">₹</span>
                    <input type="number" value={actualCash} onChange={(e) => setActualCash(e.target.value)}
                      placeholder="Enter counted amount"
                      className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-right" />
                  </div>
                </div>
              </div>
              {actualCash && (
                <div className={`mt-3 p-3 rounded-lg flex items-center gap-2 text-sm font-medium ${
                  Math.abs(cashVariance) < 1 ? 'bg-green-50 text-green-700' :
                  Math.abs(cashVariance) <= 100 ? 'bg-amber-50 text-amber-700' : 'bg-red-50 text-red-700'
                }`}>
                  {Math.abs(cashVariance) < 1 ? (
                    <><CheckCircle className="w-4 h-4" /> Cash tallies — no variance</>
                  ) : cashVariance > 0 ? (
                    <><AlertTriangle className="w-4 h-4" /> Cash EXCESS: {fmt(cashVariance)} — investigate</>
                  ) : (
                    <><AlertTriangle className="w-4 h-4" /> Cash SHORT: {fmt(Math.abs(cashVariance))} — investigate</>
                  )}
                </div>
              )}
            </section>

            {/* SECTION 4: Category Breakup */}
            <section>
              <h3 className="text-sm font-bold text-gray-900 uppercase tracking-wide border-b pb-1 mb-3">Category-wise Breakup</h3>
              <table className="w-full text-sm">
                <thead><tr className="text-left text-gray-500 text-xs uppercase">
                  <th className="pb-1">Category</th><th className="text-right pb-1">Qty</th><th className="text-right pb-1">Value</th><th className="text-right pb-1">GST</th>
                </tr></thead>
                <tbody>
                  {Object.entries(summary.categoryBreakup).map(([cat, data]) => (
                    <tr key={cat} className="border-t border-gray-100">
                      <td className="py-1">{cat.replace(/_/g, ' ')}</td>
                      <td className="text-right py-1">{data.count}</td>
                      <td className="text-right py-1">{fmt(data.value)}</td>
                      <td className="text-right py-1">{fmt(data.tax)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {/* SECTION 5: HSN Summary (for GSTR-1) */}
            <section>
              <h3 className="text-sm font-bold text-gray-900 uppercase tracking-wide border-b pb-1 mb-3">HSN-wise Summary (GSTR-1)</h3>
              <table className="w-full text-sm">
                <thead><tr className="text-left text-gray-500 text-xs uppercase">
                  <th className="pb-1">HSN</th><th className="pb-1">Description</th><th className="text-right pb-1">Taxable</th><th className="text-right pb-1">Rate</th><th className="text-right pb-1">CGST</th><th className="text-right pb-1">SGST</th>
                </tr></thead>
                <tbody>
                  {Object.values(summary.hsnSummary).map((row) => (
                    <tr key={row.hsn} className="border-t border-gray-100">
                      <td className="py-1 font-mono text-xs">{row.hsn}</td>
                      <td className="py-1 truncate max-w-[200px]">{row.description}</td>
                      <td className="text-right py-1">{fmt(row.taxableValue)}</td>
                      <td className="text-right py-1">{row.rate}%</td>
                      <td className="text-right py-1">{fmt(row.cgst)}</td>
                      <td className="text-right py-1">{fmt(row.sgst)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {/* Print-only footer */}
            <div className="hidden print:block text-center text-xs text-gray-400 pt-4 border-t">
              <p>Generated by IMS 2.0 on {new Date().toLocaleString('en-IN')} by {user?.name}</p>
              <p>This is a system-generated report. Verify against physical cash count before acceptance.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default DayEndReport;
