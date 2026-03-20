// ============================================================================
// IMS 2.0 - GST Management Tab
// ============================================================================

import { Percent } from 'lucide-react';
import type { GSTSummaryData } from './financeTypes';
import { formatCurrency } from './financeUtils';

interface GSTPanelProps {
  gstSummary: GSTSummaryData | null;
}

export default function GSTPanel({ gstSummary }: GSTPanelProps) {
  if (!gstSummary) return null;

  return (
    <div className="space-y-6">
      {/* GST Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-gradient-to-br from-indigo-900 to-indigo-800 border border-indigo-700 rounded-lg p-6 text-white">
          <p className="text-indigo-200 text-sm font-medium">CGST Collected</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.cgst_collected)}
          </p>
          <p className="text-xs text-indigo-300 mt-2">Central GST</p>
        </div>

        <div className="bg-gradient-to-br from-violet-900 to-violet-800 border border-violet-700 rounded-lg p-6 text-white">
          <p className="text-violet-200 text-sm font-medium">SGST Collected</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.sgst_collected)}
          </p>
          <p className="text-xs text-violet-300 mt-2">State GST</p>
        </div>

        <div className="bg-gradient-to-br from-cyan-900 to-cyan-800 border border-cyan-700 rounded-lg p-6 text-white">
          <p className="text-cyan-200 text-sm font-medium">Total GST Payable</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.gst_payable)}
          </p>
          <p className="text-xs text-cyan-300 mt-2">Less Input Tax Credit</p>
        </div>
      </div>

      {/* GST Breakdown */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold flex items-center gap-2">
            <Percent className="w-5 h-5 text-indigo-400" />
            GST Breakdown (18% standard rate)
          </h3>
        </div>
        <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-4">
            <div className="flex justify-between items-center p-4 bg-slate-900 rounded border border-slate-700">
              <span className="text-slate-300">CGST (9%)</span>
              <span className="text-indigo-400 font-semibold">
                {formatCurrency(gstSummary.cgst_collected)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-slate-900 rounded border border-slate-700">
              <span className="text-slate-300">SGST (9%)</span>
              <span className="text-violet-400 font-semibold">
                {formatCurrency(gstSummary.sgst_collected)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-slate-900 rounded border border-slate-700">
              <span className="text-slate-300">IGST (0%)</span>
              <span className="text-cyan-400 font-semibold">
                {formatCurrency(gstSummary.igst_collected)}
              </span>
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex justify-between items-center p-4 bg-slate-700 rounded border border-slate-600">
              <span className="text-slate-200 font-medium">Total GST Collected</span>
              <span className="text-green-400 font-bold text-lg">
                {formatCurrency(gstSummary.total_gst)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-slate-900 rounded border border-slate-700">
              <span className="text-slate-300">Input Tax Credit</span>
              <span className="text-orange-400 font-semibold">
                {formatCurrency(gstSummary.input_tax_credit)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-gradient-to-r from-red-900 to-red-800 rounded border border-red-700">
              <span className="text-red-200 font-medium">Net GST Payable</span>
              <span className="text-red-300 font-bold text-lg">
                {formatCurrency(gstSummary.gst_payable)}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
