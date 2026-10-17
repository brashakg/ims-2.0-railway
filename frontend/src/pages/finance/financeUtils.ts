// ============================================================================
// IMS 2.0 - Finance Dashboard Utilities
// ============================================================================
// generateSampleData (fabricated P&L / GST / receivables / cash-flow) was
// removed — the dashboard now loads real data from finance.py via
// services/api/finance.ts. The shared currency formatter and the GST summary
// mapper live here.

import type { GSTSummaryData } from './financeTypes';

export const formatCurrency = (amount: number): string => {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
  }).format(amount);
};

// GET /finance/gst/summary -> the GST panel. OS-010: igst_collected was
// hardcoded to 0, so the "IGST (inter-state)" card could never show the
// inter-state tax the backend computes. finance.py splits output tax into
// cgst/sgst/igst precisely so these cards reconcile to gst_collected - map
// the real value and derive the type from it. net_gst_payable is GSTR-3B's
// cash: collected less credit (never below 0; the rest carries forward), plus
// reverse-charge GST, which credit never pays.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function mapGst(d: any): GSTSummaryData | null {
  if (!d) return null;
  const igst = Number(d.igst || 0);
  return {
    period: `${d.month ?? ''}/${d.year ?? ''}`,
    cgst_collected: Number(d.cgst || 0),
    sgst_collected: Number(d.sgst || 0),
    igst_collected: igst,
    total_gst: Number(d.gst_collected || 0),
    gst_payable: Number(d.net_gst_payable || 0),
    input_tax_credit: Number(d.gst_input_credit || 0),
    credit_carried_forward: Number(d.gst_input_credit_carried_forward || 0),
    reverse_charge_tax: Number(d.reverse_charge_tax || 0),
    gst_type: igst > 0 ? 'IGST' : 'CGST_SGST',
  };
}
