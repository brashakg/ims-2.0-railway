// ============================================================================
// IMS 2.0 - CRM API
// ============================================================================
// CRM analytics endpoints (backend/api/routers/crm.py): RFM segmentation,
// churn-risk, lifecycle. These previously had no dedicated service module —
// pages hit `api` directly. Centralised here so callers go through the
// service layer.

import api from './client';

// One row from GET /crm/customers/churn-risk/list. The endpoint returns raw
// customer documents filtered by engagement, so most fields are optional.
export interface ChurnRiskCustomer {
  customer_id?: string;
  name?: string;
  phone?: string;
  mobile?: string;
  email?: string;
  loyalty_points?: number;
  total_purchases?: number;
  // Some customer docs carry a last-order timestamp; surfaced when present.
  last_order_date?: string | null;
  last_purchase_date?: string | null;
  created_at?: string;
  [key: string]: unknown;
}

export type ChurnRiskLevel = 'high' | 'medium' | 'low';

// ---------------------------------------------------------------------------
// VIP churn prediction (F40) — ORACLE's nightly scan writes a vip_churn_risk
// subdoc onto VIP customers (LTV >= 1,00,000 AND >= 3 completed orders) based
// on their PERSONAL buying rhythm, not the flat-recency churn model above.
// ---------------------------------------------------------------------------

// VIP risk label. The watchlist endpoint only ever returns WATCH / HIGH rows
// (NONE customers are filtered out server-side), but the subdoc itself can
// carry NONE so the Customer-360 shape allows it.
export type VipRiskLabel = 'NONE' | 'WATCH' | 'HIGH';

// The personalised-interval risk subdoc written back by ORACLE.
export interface VipChurnRisk {
  usual_interval_days: number; // median gap between consecutive completed orders
  last_purchase_days_ago: number; // days since last completed order, at scan time
  overdue_by_days: number; // last_purchase_days_ago - usual_interval_days
  risk_score: number; // overdue_by_days / usual_interval_days, clamped 0.0-1.0
  risk_label: VipRiskLabel;
  narrative: string | null; // Claude one-liner; null when AI unavailable / not top-10
}

// One row from GET /crm/vip-churn — a VIP customer with their risk subdoc.
export interface VipChurnRiskCustomer {
  customer_id: string;
  name: string;
  store_id: string;
  ltv: number;
  vip_churn_risk: VipChurnRisk;
}

// Latest daily snapshot for the trend line at the top of the watchlist.
export interface VipChurnTrend {
  scanned_at: string;
  vip_count: number;
  watch_count: number;
  high_risk_count: number;
}

export interface VipChurnResponse {
  customers: VipChurnRiskCustomer[];
  trend: VipChurnTrend | null;
  total: number;
}

export type VipInterventionType =
  | 'PERSONAL_CALL'
  | 'EXCLUSIVE_OFFER'
  | 'LOYALTY_BONUS'
  | 'WINBACK_WHATSAPP';

export interface VipInterveneRequest {
  intervention_type: VipInterventionType;
  notes: string;
}

export interface VipInterveneResponse {
  ok: boolean;
  task_id: string | null;
  intervention_type: string;
  already_intervened: boolean;
}

export type VipChurnSortBy = 'overdue_by_days' | 'ltv' | 'last_purchase_days_ago';

export const crmApi = {
  // At-risk customers by engagement band. Read-only.
  getChurnRiskCustomers: async (params?: { risk_level?: ChurnRiskLevel; limit?: number }) => {
    const response = await api.get('/crm/customers/churn-risk/list', { params });
    return response.data as ChurnRiskCustomer[];
  },

  // RFM segments computed from real purchase history.
  getRfmSegments: async () => {
    const response = await api.get('/crm/customers/segment/rfm');
    return response.data;
  },

  // VIP churn watchlist (F40). Read-only ranked list of overdue VIPs, plus the
  // latest daily snapshot for the trend line. risk_label omitted = both bands.
  getVipChurn: async (params?: {
    store_id?: string;
    risk_label?: 'HIGH' | 'WATCH';
    sort_by?: VipChurnSortBy;
    limit?: number;
  }) => {
    const response = await api.get('/crm/vip-churn', { params });
    return response.data as VipChurnResponse;
  },

  // Log an intervention against a VIP customer. Creates a deduped P1 task +
  // an immutable audit row server-side. already_intervened=true when this
  // customer was already actioned in the current 30-day window.
  interveneVipChurn: async (customerId: string, body: VipInterveneRequest) => {
    const response = await api.post(`/crm/vip-churn/${customerId}/intervene`, body);
    return response.data as VipInterveneResponse;
  },
};
