// ============================================================================
// IMS 2.0 - ONDC Seller Node API client  (BVI-20)
// ============================================================================
// Thin client for the ONDC seller-node status + publish endpoints.
// DARK by default: all backend calls are SIMULATED until IMS_ONDC_ENABLED=1
// and SNP credentials are configured on the server.
//
// GRACEFUL DEGRADATION: any error resolves to a safe placeholder so the ONDC
// status page always renders even if the backend hasn't deployed yet.

import api from './client';

export interface OndcStatus {
  enabled: boolean;
  env_gate: boolean;
  simulated_reason: string | null;
  last_published_at: string | null;
  last_item_count: number;
  ondc_order_count: number;
  tcs_total: number;
  note: string;
}

export interface OndcPublishResult {
  ok: boolean;
  mode: 'SIMULATED' | 'LIVE';
  item_count: number;
  simulated_reason: string | null;
  published_at: string | null;
  error: string | null;
}

const PLACEHOLDER_STATUS: OndcStatus = {
  enabled: false,
  env_gate: false,
  simulated_reason: 'ONDC integration not yet configured',
  last_published_at: null,
  last_item_count: 0,
  ondc_order_count: 0,
  tcs_total: 0,
  note: 'ONDC integration is DARK by default. Set IMS_ONDC_ENABLED=1 and configure the integration in Settings -> Integrations.',
};

export const ondcSellerApi = {
  /** Fetch ONDC module status. Never throws; falls back to placeholder. */
  getStatus: async (): Promise<OndcStatus> => {
    try {
      const res = await api.get('/ondc/status');
      return res.data as OndcStatus;
    } catch {
      return PLACEHOLDER_STATUS;
    }
  },

  /** Manually trigger catalog publish to the SNP.
   *  Returns SIMULATED when gate is off or creds are missing. */
  publishCatalog: async (): Promise<OndcPublishResult> => {
    const res = await api.post('/ondc/publish');
    return res.data as OndcPublishResult;
  },
};
