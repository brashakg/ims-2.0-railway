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
  unit_price: number;
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
