// ============================================================================
// IMS 2.0 — Vendor Portal API client (public, token-auth)
// ============================================================================
// External lens labs hit /api/v1/vendor-portal/{token_id}/* — no IMS user
// account, no JWT. The token_id in the URL IS the auth.
//
// We intentionally do NOT route this through the shared `client.ts` (which
// adds the JWT auth interceptor) — vendor portal is unauthenticated by
// design. We use a dedicated axios instance that only carries the base URL.

import axios, { type AxiosInstance } from 'axios';

const baseURL =
  (import.meta.env.VITE_API_URL as string | undefined) ||
  'https://ims-20-railway-production.up.railway.app/api/v1';

const portalClient: AxiosInstance = axios.create({
  baseURL,
  timeout: 20_000,
  // No auth interceptor — token is in the URL path
});

// ----------------------------------------------------------------------------
// Types — mirror the backend `_redact_job_for_vendor` shape
// ----------------------------------------------------------------------------

export type VendorPortalStatus =
  | 'RECEIVED'
  | 'IN_PRODUCTION'
  | 'DISPATCHED'
  | 'DELIVERED'
  | 'ON_HOLD'
  | 'CANCELLED';

export interface VendorStatusHistoryEntry {
  status: string;
  note: string | null;
  source: 'ims_user' | 'vendor_portal';
  logged_at: string;
}

export interface VendorPortalJob {
  job_id: string;
  job_number?: string | null;
  order_number?: string | null;
  customer_initials: string;
  frame_brand?: string | null;
  frame_model?: string | null;
  lens_type?: string | null;
  lens_coating?: string | null;
  lens_diameter?: string | number | null;
  fitting_height?: string | number | null;
  base_curve?: string | number | null;
  tint?: string | null;
  expected_date?: string | null;
  expected_lens_receive_date?: string | null;
  vendor_id?: string;
  vendor_order_id?: string | null;
  vendor_status?: VendorPortalStatus | string | null;
  vendor_dispatch_date?: string | null;
  vendor_received_date?: string | null;
  vendor_tracking_url?: string | null;
  vendor_status_history?: VendorStatusHistoryEntry[];
  ims_status?: string;
}

export interface VendorPortalListResponse {
  vendor_id: string;
  vendor_name: string;
  jobs: VendorPortalJob[];
  total: number;
  as_of: string;
}

// ----------------------------------------------------------------------------
// Public API
// ----------------------------------------------------------------------------

export const vendorPortalApi = {
  /** List the open jobs for the vendor authenticated by `tokenId`. */
  listJobs: async (tokenId: string): Promise<VendorPortalListResponse> => {
    const res = await portalClient.get(`/vendor-portal/${encodeURIComponent(tokenId)}/jobs`);
    return res.data as VendorPortalListResponse;
  },

  /** Single-job view. */
  getJob: async (tokenId: string, jobId: string): Promise<VendorPortalJob> => {
    const res = await portalClient.get(
      `/vendor-portal/${encodeURIComponent(tokenId)}/jobs/${encodeURIComponent(jobId)}`,
    );
    return res.data as VendorPortalJob;
  },

  /** Lab posts a status update + optional vendor_order_id + tracking URL. */
  postStatus: async (
    tokenId: string,
    jobId: string,
    payload: {
      status: VendorPortalStatus;
      note?: string;
      vendor_order_id?: string;
      vendor_tracking_url?: string;
    },
  ): Promise<VendorPortalJob> => {
    const res = await portalClient.post(
      `/vendor-portal/${encodeURIComponent(tokenId)}/jobs/${encodeURIComponent(jobId)}/status`,
      payload,
    );
    return res.data as VendorPortalJob;
  },
};
