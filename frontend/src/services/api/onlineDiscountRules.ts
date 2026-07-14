// ============================================================================
// IMS 2.0 - Online Discount Rules API  (rebuild of BVI DiscountRule; DARK)
// ============================================================================
// Owner-editable CRUD for the automatic ONLINE storefront discount rules. A rule
// sets what the WEBSITE shows (online-only -- it never changes in-store POS
// pricing). The winning rule for a product is the most specific active match
// (category + brand + sub-brand  >  category + brand  >  category), tie-broken by
// priority; online_offer = round(MRP * (1 - pct/100)), clamped to [cost, MRP].
//
// Import DIRECT from this module (NOT the services/api/index.ts barrel).
// Mounted at /api/v1/online-store/discount-rules. Role-gated to
// SUPERADMIN / ADMIN / CATALOG_MANAGER.

import api from './client';

const BASE = '/online-store/discount-rules';

export interface DiscountRule {
  rule_id: string;
  id?: string;
  category: string;
  brand?: string | null;
  sub_brand?: string | null;
  discount_percentage: number;
  active: boolean;
  priority: number;
  source?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** Summary of the fail-soft bulk recompute fired after a rule change. */
export interface RecomputeResult {
  ok: boolean;
  products?: number;
  variants?: number;
  errors?: number;
  rules?: number;
  error?: string;
}

export interface RuleListResponse {
  rules: DiscountRule[];
  count: number;
  db_connected: boolean;
}

export interface RuleCreatePayload {
  category: string;
  brand?: string | null;
  sub_brand?: string | null;
  discount_percentage: number;
  active?: boolean;
  priority?: number;
}

export type RuleUpdatePayload = Partial<RuleCreatePayload>;

export interface RuleListParams {
  category?: string;
  brand?: string;
  active?: boolean;
}

export const onlineDiscountRulesApi = {
  list: async (params: RuleListParams = {}): Promise<RuleListResponse> => {
    const res = await api.get(BASE, { params });
    const data = (res?.data ?? {}) as Partial<RuleListResponse>;
    return {
      rules: Array.isArray(data.rules) ? data.rules : [],
      count: data.count ?? 0,
      db_connected: data.db_connected ?? false,
    };
  },

  create: async (
    payload: RuleCreatePayload,
  ): Promise<{ rule: DiscountRule; recompute?: RecomputeResult }> => {
    const res = await api.post(BASE, payload);
    return res.data;
  },

  update: async (
    ruleId: string,
    payload: RuleUpdatePayload,
  ): Promise<{ rule: DiscountRule; recompute?: RecomputeResult }> => {
    const res = await api.put(`${BASE}/${encodeURIComponent(ruleId)}`, payload);
    return res.data;
  },

  remove: async (
    ruleId: string,
  ): Promise<{ deleted: boolean; rule_id: string; recompute?: RecomputeResult }> => {
    const res = await api.delete(`${BASE}/${encodeURIComponent(ruleId)}`);
    return res.data;
  },

  recompute: async (category?: string): Promise<{ recompute: RecomputeResult }> => {
    const res = await api.post(`${BASE}/recompute`, category ? { category } : {});
    return res.data;
  },
};

export default onlineDiscountRulesApi;
