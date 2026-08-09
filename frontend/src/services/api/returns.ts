// ============================================================================
// IMS 2.0 - Customer Returns / Exchange / Credit-Note API
// ============================================================================
// Records a customer return, exchange, or store-credit note against an
// original sale. Money is RECORDED server-side, never executed.

import api from './client';

export type ReturnType = 'RETURN' | 'EXCHANGE' | 'CREDIT_NOTE';
export type ItemCondition = 'GOOD' | 'OPENED' | 'DAMAGED';

export interface ReturnLinePayload {
  order_item_id?: string;
  product_id?: string;
  product_name: string;
  sku: string;
  return_qty: number;
  // NET (pre-GST) unit price from the original order line. The server grosses
  // it up by gst_rate to refund the GST-inclusive amount the customer paid.
  unit_price: number;
  // GST rate (%) the line was billed at. Hint only; the server prefers the
  // rate stamped on the original order line.
  gst_rate?: number;
  reason?: string;
  condition: ItemCondition;
  notes?: string;
}

export interface ReplacementLinePayload {
  product_id?: string;
  name: string;
  sku: string;
  quantity: number;
  unit_price: number;
  gst_rate?: number;
}

// One leg of HOW a money refund was physically handed back. The Day-End drawer
// nets off THIS breakdown (never the inferred original-sale tender). Codes:
// CASH / UPI / CARD / BANK. Sum must equal the net refund.
export type RefundTenderCode = 'CASH' | 'UPI' | 'CARD' | 'BANK';
export interface RefundTenderLinePayload {
  method: RefundTenderCode;
  amount: number;
}

export interface CreateReturnPayload {
  order_id?: string;
  order_number?: string;
  customer_id?: string;
  store_id?: string;
  return_type: ReturnType;
  items: ReturnLinePayload[];
  replacement_items?: ReplacementLinePayload[];
  approval_note?: string;
  // Legacy metadata / Tally hint (the ORIGINAL sale's tender). NOT used by the
  // drawer readers anymore — they consume refund_tenders / collect_method.
  refund_method?: string;
  // RETURN only: explicit per-tender breakdown of the cash actually returned.
  // Sum must equal the net refund. Absent -> the refund is not auto-netted.
  refund_tenders?: RefundTenderLinePayload[];
  // EXCHANGE COLLECT only: the tender the price difference was collected in.
  collect_method?: RefundTenderCode;
  // Optional absolute Rs deduction for damaged / opened goods. 0 = full
  // refund. Net refund = gross - restocking_fee.
  restocking_fee?: number;
  // F27 refund approval matrix: when the gate requires a tiered sign-off the
  // server 403s with reason=REFUND_APPROVAL_REQUIRED; the till mints + PIN-gets
  // an approval and re-submits with this token + request id bound to the refund.
  refund_approval_token?: string;
  refund_approval_request_id?: string;
  // Overall refund reason that drives the matrix tier (DEFECTIVE / GOODWILL /
  // PRICE_MATCH / CHANGE_OF_MIND). Optional; the server falls back to per-line.
  refund_reason?: string;
}

export const returnsApi = {
  create: async (payload: CreateReturnPayload) => {
    const response = await api.post('/returns', payload);
    return response.data;
  },

  list: async (params?: { store_id?: string; return_type?: string; skip?: number; limit?: number }) => {
    const response = await api.get('/returns', { params });
    return response.data;
  },

  get: async (returnId: string) => {
    const response = await api.get(`/returns/${returnId}`);
    return response.data;
  },
};
