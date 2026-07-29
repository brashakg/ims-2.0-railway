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
      {/* OS-031: /gst/summary has no store/entity/date params — these figures
          are ALWAYS org-wide for the current month, while the rest of the
          Finance dashboard is scoped to the active store + date filters. Say
          so explicitly instead of letting the card silently imply the same
          scope as its neighbours. The per-GSTIN filable numbers live in the
          "GST reconciliation by entity" table below. */}
      <div className="flex items-center gap-2 text-xs text-gray-500">
        <span className="inline-flex items-center rounded-full bg-gray-100 text-gray-600 border border-gray-200 px-2 py-0.5 font-medium">
          All entities &amp; stores · current month
        </span>
        <span>
          Not affected by the store or date filters above — per-entity figures are in the
          reconciliation table below.
        </span>
      </div>

      {/* GST Summary Cards — unified neutral chrome (was indigo/violet/cyan). */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-6 text-gray-900">
          <p className="text-gray-500 text-sm font-medium">CGST Collected</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.cgst_collected)}
          </p>
          <p className="text-xs text-gray-500 mt-2">Central GST</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-6 text-gray-900">
          <p className="text-gray-500 text-sm font-medium">SGST Collected</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.sgst_collected)}
          </p>
          <p className="text-xs text-gray-500 mt-2">State GST</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-6 text-gray-900">
          <p className="text-gray-500 text-sm font-medium">Total GST Payable</p>
          <p className="text-2xl font-bold mt-2">
            {formatCurrency(gstSummary.gst_payable)}
          </p>
          <p className="text-xs text-gray-500 mt-2">Less Input Tax Credit</p>
        </div>
      </div>

      {/* GST Breakdown */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="bg-gray-50 px-6 py-4 border-b border-gray-200">
          <h3 className="text-gray-900 font-semibold flex items-center gap-2">
            <Percent className="w-5 h-5 text-gray-500" />
            GST Breakdown
          </h3>
        </div>
        <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-4">
            <div className="flex justify-between items-center p-4 bg-gray-50 rounded border border-gray-200">
              <span className="text-gray-700">CGST</span>
              <span className="text-gray-900 font-semibold">
                {formatCurrency(gstSummary.cgst_collected)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-gray-50 rounded border border-gray-200">
              <span className="text-gray-700">SGST</span>
              <span className="text-gray-900 font-semibold">
                {formatCurrency(gstSummary.sgst_collected)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-gray-50 rounded border border-gray-200">
              <span className="text-gray-700">IGST (inter-state)</span>
              <span className="text-gray-900 font-semibold">
                {formatCurrency(gstSummary.igst_collected)}
              </span>
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex justify-between items-center p-4 bg-gray-100 rounded border border-gray-200">
              <span className="text-gray-700 font-medium">Total GST Collected</span>
              <span className="text-gray-900 font-bold text-lg">
                {formatCurrency(gstSummary.total_gst)}
              </span>
            </div>
            <div className="flex justify-between items-center p-4 bg-gray-50 rounded border border-gray-200">
              <span className="text-gray-700">Input Tax Credit</span>
              <span className="text-gray-900 font-semibold">
                {formatCurrency(gstSummary.input_tax_credit)}
              </span>
            </div>
            {/* Net payable keeps the one meaningful (danger) accent. */}
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
