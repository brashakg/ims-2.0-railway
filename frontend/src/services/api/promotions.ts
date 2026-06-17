// ============================================================================
// IMS 2.0 - Promotions API client (F11 advanced promotions + F12 bundling)
// ============================================================================
// Backend: backend/api/routers/promotions.py mounted at /api/v1/promotions.
// The live POS apply is dark behind PROMO_ENGINE_ENABLED (default off); CRUD +
// the pure /evaluate preview are always available so rules can be authored and
// previewed before go-live. The Offer Tally report lives at /reports/promotions.
//
// NOTE: import this directly (`from '../../services/api/promotions'`) -- the
// barrel re-export is unreliable for newly-added services (see CLAUDE memory).

import api from './client';

export type PromoType = 'THRESHOLD' | 'BOGO' | 'COMBO' | 'SECOND_PAIR' | 'PERCENT';

export interface ComboGroup {
  category?: string | null;
  item_type?: string | null;
  brand?: string | null;
}

export interface PromoRule {
  promo_id: string;
  name: string;
  promo_type: PromoType;
  description?: string | null;
  reward_value: number;
  max_discount_amount?: number | null;
  stackable: boolean;
  priority: number;
  min_cart_value?: number | null;
  min_qty?: number | null;
  trigger_categories?: string[] | null;
  product_ids?: string[] | null;
  buy_quantity?: number | null;
  get_quantity?: number | null;
  combo_groups?: ComboGroup[] | null;
  customer_tiers?: string[] | null;
  first_purchase_only?: boolean;
  store_ids?: string[] | null;
  active: boolean;
  valid_from?: string | null;
  valid_until?: string | null;
  uses_count?: number;
  created_at?: string;
  updated_at?: string;
}

export interface PromoRuleCreate {
  name: string;
  promo_type: PromoType;
  description?: string | null;
  reward_value?: number;
  max_discount_amount?: number | null;
  stackable?: boolean;
  priority?: number;
  min_cart_value?: number | null;
  min_qty?: number | null;
  trigger_categories?: string[] | null;
  product_ids?: string[] | null;
  buy_quantity?: number | null;
  get_quantity?: number | null;
  combo_groups?: ComboGroup[] | null;
  customer_tiers?: string[] | null;
  first_purchase_only?: boolean;
  store_ids?: string[] | null;
  active?: boolean;
  valid_from?: string | null;
  valid_until?: string | null;
}

export type PromoRuleUpdate = Partial<PromoRuleCreate>;

export interface EvaluateItem {
  product_id?: string | null;
  product_name?: string | null;
  brand?: string | null;
  item_type?: string | null;
  discount_category?: string | null;
  category?: string | null;
  quantity: number;
  unit_price: number;
  cost_at_sale?: number | null;
}

export interface PromoEvaluation {
  applied: boolean;
  total_discount: number;
  raw_total_discount: number;
  fired: string[];
  suppressed: string[];
  exclusive_winner: string | null;
  breakdown: Record<string, number>;
  per_line_discount: Record<string, number>;
  evaluated_count: number;
  names: Record<string, string>;
}

export interface EvaluateResponse {
  flag_enabled: boolean;
  evaluation: PromoEvaluation;
  margin_impact: {
    total_discount_given: number;
    estimated_cogs: number;
    net_margin_after_promo: number;
    cogs_is_estimated: boolean;
  };
}

export interface PromoReportRow {
  promo_id: string;
  promo_name: string;
  promo_type: string | null;
  orders_count: number;
  total_discount_given: number;
  estimated_cogs: number;
  net_margin_after_promo: number;
  cogs_is_estimated: boolean;
}

export interface PromoReport {
  summary: {
    total_discount_given: number;
    orders_with_promos: number;
    promos_fired: number;
    net_margin_impact: number;
    any_cogs_estimated: boolean;
  };
  promos: PromoReportRow[];
  start_date?: string | null;
  end_date?: string | null;
}

export const promotionsApi = {
  /** List promo rules (newest first). */
  listRules: async (params: { store_id?: string; active_only?: boolean } = {}): Promise<{
    rules: PromoRule[];
    total: number;
  }> => {
    const res = await api.get('/promotions', { params });
    return res.data;
  },

  getRule: async (promoId: string): Promise<PromoRule> => {
    const res = await api.get(`/promotions/${promoId}`);
    return res.data;
  },

  createRule: async (payload: PromoRuleCreate): Promise<{ message: string; rule: PromoRule }> => {
    const res = await api.post('/promotions', payload);
    return res.data;
  },

  updateRule: async (
    promoId: string,
    payload: PromoRuleUpdate,
  ): Promise<{ message: string; rule: PromoRule }> => {
    const res = await api.put(`/promotions/${promoId}`, payload);
    return res.data;
  },

  /** Soft-deactivate a rule (never hard-deleted; preserves the audit trail). */
  deactivateRule: async (promoId: string): Promise<{ message: string; promo_id: string }> => {
    const res = await api.delete(`/promotions/${promoId}`);
    return res.data;
  },

  /** PURE preview -- no side effects. Safe to call live from POS cart-review. */
  evaluate: async (
    items: EvaluateItem[],
    opts: { customer_id?: string; store_id?: string } = {},
  ): Promise<EvaluateResponse> => {
    const res = await api.post('/promotions/evaluate', { items, ...opts });
    return res.data;
  },

  /** Offer Tally report (fired promos + margin impact). */
  report: async (params: {
    start_date?: string;
    end_date?: string;
    store_id?: string;
  } = {}): Promise<PromoReport> => {
    const res = await api.get('/reports/promotions', { params });
    return res.data;
  },
};

export default promotionsApi;
