import api from './client';

export type CreditHeads = { taxable: number; cgst: number; sgst: number; igst: number; total: number };
export type CreditAllocation = Omit<CreditHeads, 'total'> & { source_key: string };
export interface CreditSourceSelection {
  return_line_index: number;
  grn_id: string;
  bill_id: string;
  bill_line_index: number;
  quantity: number;
  stock_ids?: string[];
}
export interface CreditPendingReason {
  code: string;
  message: string;
  return_line_index?: number;
}
export interface SupplierCreditConfirmation {
  confirmation_id: string;
  credit_note_number: string;
  credit_note_date: string;
  received_amount: number;
  notes?: string | null;
  posting_status: 'POSTED' | 'PENDING_EVIDENCE';
  source_selections?: CreditSourceSelection[];
  allocations?: CreditAllocation[];
  retry_unchanged?: boolean;
  retry_payload?: ConfirmSupplierCredit | null;
  pending_reasons: CreditPendingReason[];
}
export interface CreditSource {
  source_key: string;
  return_line_index: number;
  product_id: string;
  product_name: string;
  quantity: number;
  grn_id: string;
  bill_id: string;
  bill_line_index: number;
  bill_number: string;
  bill_date: string;
  claims_credit: boolean;
  reverse_charge: boolean;
  stock_ids: string[];
  suggested: CreditHeads;
  remaining_caps: CreditHeads;
}
export interface CreditBillCandidate {
  grn_id: string;
  bill_id: string;
  bill_line_index: number;
  bill_number: string;
  bill_date: string;
  quantity: number;
  stock_ids: string[];
  stock_units?: Array<{ stock_id: string; label: string }>;
  disabled_reason?: string;
}
export interface SupplierCreditPreview {
  return_id: string;
  source_status: 'RESOLVED' | 'PENDING_EVIDENCE';
  pending_reasons: CreditPendingReason[];
  sources: CreditSource[];
  choices: Array<{
    return_line_index: number;
    product_name: string;
    quantity: number;
    candidates: CreditBillCandidate[];
  }>;
  source_selections: CreditSourceSelection[];
  expected_credit_amount: number | null;
  posted_amount: number;
  remaining_amount: number | null;
  confirmation: SupplierCreditConfirmation | null;
  confirmations: SupplierCreditConfirmation[];
}
export interface ConfirmSupplierCredit {
  idempotency_key: string;
  credit_note_number: string;
  credit_note_date: string;
  received_amount: number;
  notes?: string | null;
  source_selections: CreditSourceSelection[];
  allocations: CreditAllocation[];
}
export interface SupplierCreditResult {
  idempotent: boolean;
  confirmation: SupplierCreditConfirmation;
  return: { return_id: string; status: string; [key: string]: unknown };
  summary: {
    posted_amount: number;
    remaining_amount: number | null;
    settlement_status: 'PARTIAL' | 'CONFIRMED' | 'PENDING_EVIDENCE';
  };
}

export const supplierCreditsApi = {
  preview: async (returnId: string, body: {
    source_selections?: CreditSourceSelection[];
    confirmation_id?: string;
  } = {}): Promise<SupplierCreditPreview> =>
    (await api.post(`/vendor-returns/${encodeURIComponent(returnId)}/credit-preview`, body)).data,
  confirm: async (returnId: string, body: ConfirmSupplierCredit): Promise<SupplierCreditResult> =>
    (await api.post(`/vendor-returns/${encodeURIComponent(returnId)}/credit-confirmations`, body)).data,
};
