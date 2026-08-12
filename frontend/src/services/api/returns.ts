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
// STORE_CREDIT is the refundable substitute for the part of a sale paid with an
// instrument that cannot come back as cash (gift voucher / loyalty / EMI /
// credit). It is RECORDED on the breakdown but never netted into a drawer.
export type RefundTenderMethod = RefundTenderCode | 'STORE_CREDIT';
export interface RefundTenderLinePayload {
  method: RefundTenderMethod;
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
  // NOTE: replacement_items[].unit_price is VALIDATED, not ignored. The server
  // treats the catalog price as a CEILING and a fixed percentage below it as a
  // FLOOR: an at-or-below price is honoured (a real negotiated discount), an
  // above-catalog price is refused, and a price so low it would flip the
  // settlement from COLLECT into REFUND is refused too (that flip minted store
  // credit while the drawer never moved). Quantity is bounded per line AND per
  // order, and the collected difference has its own ceiling -- every factor of
  // a drawer figure is server-checked. The quote echoes the resolved lines and
  // a server-computed replacement_total.
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

// AUTHORITATIVE server-computed money preview for a return. The till MUST
// prefill the refund-tender picker from `net_refund` rather than computing an
// amount client-side — a client-side GST gross-up drifted from the server on
// every inclusive-priced sale and 400'd the refund with no way to recover.
export interface ReturnQuote {
  order_id?: string;
  return_type: ReturnType;
  gross_refund: number;
  restocking_fee: number;
  /** THE figure the refund-tender split must sum to. */
  net_refund: number;
  gst_breakup?: Record<string, number>;
  settlement?: { direction: 'COLLECT' | 'REFUND' | 'EVEN'; difference: number } | null;
  /** What each tender actually collected on the source order. */
  captured_tenders: Record<string, number>;
  /** What the sale took on instruments that cannot come back as cash. */
  non_refundable_tenders?: Record<string, number>;
  prior_refunds_by_tender: Record<string, number>;
  /** What may still be refunded per tender (includes a STORE_CREDIT allowance). */
  refundable_by_tender: Record<string, number>;
  /** True when cash-in tenders alone cannot reach the net refund. */
  cash_in_shortfall?: boolean;
  /** Catalog-resolved replacement lines the settlement was computed from. */
  replacement_items_priced?: Array<{ name?: string; sku?: string; quantity?: number; unit_price: number }>;
  /** True when the server cannot certify a complete refundable split. */
  tenders_unverifiable: boolean;
}

export const returnsApi = {
  create: async (payload: CreateReturnPayload) => {
    const response = await api.post('/returns', payload);
    return response.data;
  },

  quote: async (payload: CreateReturnPayload): Promise<ReturnQuote> => {
    const response = await api.post('/returns/quote', payload);
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
