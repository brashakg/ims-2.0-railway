// ============================================================================
// IMS 2.0 - GST reconciliation cross-check (accountant month-end sign-off)
// ============================================================================
// Import directly (not via the api barrel).

import api from './client';

export type CrossCheckStatus = 'MATCH' | 'MISMATCH' | 'INFO';

export interface CrossCheckRow {
  metric: string;
  sources: Record<string, number>;
  variance: number;
  status: CrossCheckStatus;
  note?: string;
}

export interface RateBreakupRow {
  gstRate: number;
  taxableValue: number;
  cgst: number;
  sgst: number;
  igst: number;
  tax: number;
}

export interface CdnrDetail {
  count: number;
  taxableValue: number;
  tax: number;
  rows: Array<Record<string, unknown>>;
}

export interface DeemedSupplyDetail {
  count: number;
  taxableValue: number;
  tax: number;
  rows: Array<Record<string, unknown>>;
}

export interface CrossCheckSignoff {
  year: number;
  month: number;
  entity_id: string;
  checked: boolean;
  checked_by?: string;
  checked_by_name?: string;
  checked_at?: string;
  note?: string;
  // Server-recomputed authoritative snapshot.
  mismatch_count?: number;
  gst_payable?: number;
  mismatch_metrics?: string[];
  // What the client claimed (drift forensics only).
  client_mismatch_count?: number;
  client_gst_payable?: number;
}

export interface GstCrossCheck {
  month: number;
  year: number;
  period: string;
  entity_id: string | null;
  entity_name: string | null;
  store_count: number;
  stores_computed: number;
  failed_store_ids: string[];
  partial: boolean;
  tolerance: number;
  comparisons: CrossCheckRow[];
  rate_breakup: RateBreakupRow[];
  cdnr: CdnrDetail;
  deemed_supply: DeemedSupplyDetail;
  validation: { ok: boolean; issueCount: number; issues: Array<Record<string, unknown>> };
  summary: {
    mismatch_count: number;
    mismatch_metrics: string[];
    all_matched: boolean;
    gst_payable: number;
  };
  gstr1: { totalTaxableValue: number; totalTax: number; cgst: number; sgst: number; igst: number };
  gstr3b: {
    outwardTaxableValue: number;
    outwardTax: number;
    itc: { cgst: number; sgst: number; igst: number; total: number };
    netCash: { cgst: number; sgst: number; igst: number; total: number };
    rcm: { taxableValue: number; cgst: number; sgst: number; igst: number; total: number };
  };
  books: {
    sales_grand_total: number;
    sales_tax: number;
    sales_taxable: number;
    payments_collected: number;
    input_credit: number | null;
  };
  tally: { taxable: number; tax: number; cgst: number; sgst: number; igst: number };
  signoff: CrossCheckSignoff | null;
}

export interface SignoffPayload {
  month: number;
  year: number;
  entity_id?: string | null;
  note?: string;
  mismatch_count?: number;
  gst_payable?: number;
}

export const gstCrossCheckApi = {
  get: async (month: number, year: number, entityId?: string | null) => {
    const params: Record<string, string | number> = { month, year };
    if (entityId) params.entity_id = entityId;
    const res = await api.get('/finance/gst/cross-check', { params });
    return res.data as GstCrossCheck;
  },
  signoff: async (payload: SignoffPayload) => {
    const res = await api.post('/finance/gst/cross-check-signoff', payload);
    return res.data as { ok: boolean; signoff: CrossCheckSignoff };
  },
};
