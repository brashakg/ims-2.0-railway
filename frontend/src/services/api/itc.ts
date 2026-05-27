// ============================================================================
// IMS 2.0 - GST input-tax-credit (ITC) reconciliation API
// ============================================================================
// Import directly (not via the api barrel).

import api from './client';

export interface ItcPeriod {
  period: string;
  taxable: number;
  tax: number;
  cgst: number;
  sgst: number;
  igst: number;
  bills: number;
}
export interface ItcRegister {
  periods: ItcPeriod[];
  total_taxable: number;
  total_itc: number;
  total_cgst?: number;
  total_sgst?: number;
  total_igst?: number;
}

export interface Gstr2bRow {
  gstin?: string;
  invoice_no?: string;
  taxable?: number;
  tax?: number;
}

export interface ReconcileSummary {
  matched: number;
  mismatch: number;
  only_in_books: number;
  only_in_2b: number;
  itc_safe_to_claim: number;
  itc_in_mismatch: number;
  itc_at_risk: number;
  total_book_itc: number;
}

export interface ReconcileResult {
  as_of: string;
  summary: ReconcileSummary;
  matched: Array<Record<string, unknown>>;
  mismatch: Array<Record<string, unknown>>;
  only_in_books: Array<Record<string, unknown>>;
  only_in_2b: Array<Record<string, unknown>>;
}

export type ItcBucket = 'matched' | 'mismatch' | 'only_in_books' | 'only_in_2b';

export const itcApi = {
  register: async (period?: string) => {
    const params = period ? { period } : undefined;
    const res = await api.get('/finance/itc-register', { params });
    return res.data as ItcRegister;
  },
  reconcile: async (rows: Gstr2bRow[], as_of?: string) => {
    const res = await api.post('/finance/gstr2b-reconcile', { rows, as_of });
    return res.data as ReconcileResult;
  },
  // Bucket-scoped CSV export. POST because the GSTR-2B rows are client-side
  // state; re-uploading on every download would be terrible UX.
  exportBucketCsv: async (
    bucket: ItcBucket,
    rows: Gstr2bRow[],
    as_of?: string
  ): Promise<Blob> => {
    const res = await api.post(
      '/finance/itc-export',
      { rows, as_of },
      { params: { bucket }, responseType: 'blob' }
    );
    return res.data as Blob;
  },
};
