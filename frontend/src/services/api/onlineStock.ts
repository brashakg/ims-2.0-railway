// ============================================================================
// IMS 2.0 - Online vs in-store stock reconciliation API
// ============================================================================
// Import directly (not via the services/api barrel).

import api from './client';

export interface ReconcileItem {
  sku: string;
  name?: string;
  in_store: number;
  online: number;
  recommended: number;
  delta: number;
  status: 'OVERSELL_RISK' | 'OVER_ALLOCATED' | 'OK' | 'NOT_ONLINE';
}

export interface ReconcileResult {
  items: ReconcileItem[];
  summary: {
    total?: number;
    oversell_risk?: number;
    over_allocated?: number;
    ok?: number;
    not_online?: number;
    oversell_risk_units?: number;
    safety_buffer?: number;
  };
  online_configured?: boolean;
}

export const onlineStockApi = {
  reconcile: async (params?: { store_id?: string; safety_buffer?: number }) => {
    const res = await api.get('/catalog/online-stock-reconcile', {
      params: {
        ...(params?.store_id ? { store_id: params.store_id } : {}),
        safety_buffer: params?.safety_buffer ?? 0,
      },
    });
    return res.data as ReconcileResult;
  },
};
