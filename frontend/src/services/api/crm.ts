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
};
