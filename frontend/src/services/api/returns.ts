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

export interface CreateReturnPayload {
  order_id?: string;
  order_number?: string;
  customer_id?: string;
  store_id?: string;
  return_type: ReturnType;
  items: ReturnLinePayload[];
  replacement_items?: ReplacementLinePayload[];
  approval_note?: string;
  refund_method?: string;
  // Optional absolute Rs deduction for damaged / opened goods. 0 = full
  // refund. Net refund = gross - restocking_fee.
  restocking_fee?: number;
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
