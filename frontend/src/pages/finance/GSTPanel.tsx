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
        <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-6 text-gray-900">
          <p className="text-indigo-700 text-sm font-medium">CGST Collected</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.cgst_collected)}
          </p>
          <p className="text-xs text-indigo-700 mt-2">Central GST</p>
        </div>

        <div className="bg-violet-50 border border-violet-200 rounded-lg p-6 text-gray-900">
          <p className="text-violet-700 text-sm font-medium">SGST Collected</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.sgst_collected)}
          </p>
          <p className="text-xs text-violet-600 mt-2">State GST</p>
        </div>

        <div className="bg-cyan-50 border border-cyan-200 rounded-lg p-6 text-gray-900">
          <p className="text-cyan-700 text-sm font-medium">Total GST Payable</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.gst_payable)}
          </p>
          <p className="text-xs text-cyan-700 mt-2">Less Input Tax Credit</p>
        </div>
      </div>

      {/* GST Breakdown */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="bg-slate-50 px-6 py-4 border-b border-gray-200">
          <h3 className="text-gray-900 font-semibold flex items-center gap-2">
            <Percent className="w-5 h-5 text-indigo-600" />
            GST Breakdown
          </h3>
        </div>
        <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-4">
            <div className="flex justify-between items-center p-4 bg-slate-50 rounded border border-gray-200">
              <span className="text-slate-700">CGST</span>
              <span className="text-indigo-600 font-semibold">
                {formatCurrency(gstSummary.cgst_collected)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-slate-50 rounded border border-gray-200">
              <span className="text-slate-700">SGST</span>
              <span className="text-violet-700 font-semibold">
                {formatCurrency(gstSummary.sgst_collected)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-slate-50 rounded border border-gray-200">
              <span className="text-slate-700">IGST (inter-state)</span>
              <span className="text-cyan-600 font-semibold">
                {formatCurrency(gstSummary.igst_collected)}
              </span>
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex justify-between items-center p-4 bg-gray-100 rounded border border-gray-200">
              <span className="text-slate-700 font-medium">Total GST Collected</span>
              <span className="text-green-600 font-bold text-lg">
                {formatCurrency(gstSummary.total_gst)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-slate-50 rounded border border-gray-200">
              <span className="text-slate-700">Input Tax Credit</span>
              <span className="text-orange-600 font-semibold">
                {formatCurrency(gstSummary.input_tax_credit)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-red-50 rounded border border-red-200">
              <span className="text-red-700 font-medium">Net GST Payable</span>
              <span className="text-red-700 font-bold text-lg">
                {formatCurrency(gstSummary.gst_payable)}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
