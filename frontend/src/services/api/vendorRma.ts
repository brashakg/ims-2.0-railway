// ============================================================================
// IMS 2.0 - N4 Vendor RMA + credit-note reconciliation API
// ============================================================================
// Return Merchandise Authorization against a vendor: raise -> authorize (record
// the vendor RMA number) -> dispatch (courier/AWB) -> reconcile vendor credit
// notes (expected vs received, paisa-exact, partial) -> close. Money is RECORDED
// server-side, never executed; amounts are authoritative in integer paise with
// rupee display fields alongside.

import api from './client';

export type RMAStatus =
  | 'DRAFT'
  | 'AUTHORIZED'
  | 'DISPATCHED'
  | 'CREDIT_RECEIVED'
  | 'CLOSED'
  | 'REJECTED';

export type RMAReason = 'DEFECTIVE' | 'WRONG' | 'EXCESS' | 'WARRANTY' | 'NON_ADAPT';

export interface RMALinePayload {
  product_id: string;
  product_name: string;
  quantity: number;
  reason: RMAReason;
  unit_cost: number; // rupees
}

export interface CreateRMAPayload {
  vendor_id: string;
  vendor_name: string;
  store_id: string;
  lines: RMALinePayload[];
  notes?: string;
  po_id?: string;
  grn_id?: string;
  return_id?: string;
}

export interface RMACreditNote {
  credit_note_number: string;
  received_paise: number;
  recorded_at: string;
  recorded_by: string;
  notes?: string;
  approval_token?: string | null;
}

export interface RMACourier {
  carrier: string;
  awb: string;
  dispatch_date: string;
  recorded_at?: string;
  recorded_by?: string;
}

export interface VendorRMA {
  rma_id: string;
  vendor_id: string;
  vendor_name: string;
  store_id: string;
  status: RMAStatus;
  lines: Array<{
    product_id: string;
    product_name: string;
    quantity: number;
    reason: RMAReason;
    unit_cost_paise: number;
    line_expected_paise: number;
  }>;
  expected_credit_paise: number;
  received_credit_paise: number;
  variance_paise: number;
  expected_credit_rupees?: number;
  received_credit_rupees?: number;
  variance_rupees?: number;
  vendor_rma_number: string | null;
  courier: RMACourier | null;
  credit_notes: RMACreditNote[];
  notes?: string;
  created_at: string;
  created_by: string;
  status_history?: Array<{ status: string; at: string; by: string; notes?: string }>;
}

export interface ListRMAResponse {
  rmas: VendorRMA[];
  total: number;
}

export const vendorRmaApi = {
  list(params?: { store_id?: string; vendor_id?: string; status?: string; skip?: number; limit?: number }) {
    return api.get<ListRMAResponse>('/vendor-rma', { params }).then((r) => r.data);
  },

  get(rmaId: string) {
    return api.get<VendorRMA>(`/vendor-rma/${rmaId}`).then((r) => r.data);
  },

  raise(payload: CreateRMAPayload) {
    return api.post('/vendor-rma', payload).then((r) => r.data);
  },

  authorize(rmaId: string, vendor_rma_number: string, notes?: string) {
    return api.post(`/vendor-rma/${rmaId}/authorize`, { vendor_rma_number, notes }).then((r) => r.data);
  },

  dispatch(rmaId: string, body: { carrier: string; awb: string; dispatch_date?: string; notes?: string }) {
    return api.post(`/vendor-rma/${rmaId}/dispatch`, body).then((r) => r.data);
  },

  recordCreditNote(
    rmaId: string,
    body: {
      credit_note_number: string;
      received_amount: number;
      notes?: string;
      approval_token?: string;
      approval_request_id?: string;
    },
  ) {
    return api.post(`/vendor-rma/${rmaId}/credit-note`, body).then((r) => r.data);
  },

  reject(rmaId: string, reason?: string) {
    return api.post(`/vendor-rma/${rmaId}/reject`, { reason }).then((r) => r.data);
  },

  close(rmaId: string, body?: { notes?: string; write_off_variance?: boolean }) {
    return api.post(`/vendor-rma/${rmaId}/close`, body ?? {}).then((r) => r.data);
  },
};

export default vendorRmaApi;
