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
  bills: number;
}
export interface ItcRegister {
  periods: ItcPeriod[];
  total_taxable: number;
  total_itc: number;
}

export interface Gstr2bRow {
  gstin?: string;
  invoice_no?: string;
  taxable?: number;
  tax?: number;
}

export interface ReconcileResult {
  as_of: string;
  summary: {
    matched: number;
    mismatch: number;
    only_in_books: number;
    only_in_2b: number;
    itc_safe_to_claim: number;
    itc_at_risk: number;
  };
  matched: Array<Record<string, unknown>>;
  mismatch: Array<Record<string, unknown>>;
  only_in_books: Array<Record<string, unknown>>;
  only_in_2b: Array<Record<string, unknown>>;
}

export const itcApi = {
  register: async () => {
    const res = await api.get('/finance/itc-register');
    return res.data as ItcRegister;
  },
  reconcile: async (rows: Gstr2bRow[], as_of?: string) => {
    const res = await api.post('/finance/gstr2b-reconcile', { rows, as_of });
    return res.data as ReconcileResult;
  },
};
