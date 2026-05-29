// ============================================================================
// IMS 2.0 — Customer Self-Service Portal API client (public)
// ============================================================================
// Customer-facing. Two flows:
//   1. Order tracking  — public tokenized link, no login. The token in the
//      URL IS the credential (server-side checked).
//   2. Rx viewing      — OTP-gated. phone -> OTP -> short-lived view token,
//      then GET /portal/rx with that token in the Authorization header.
//
// We intentionally do NOT route through the shared `client.ts` (which injects
// the logged-in IMS user's JWT). This surface is unauthenticated by design;
// the only credential is the tokenized link or the portal view token, which
// we pass explicitly per-call.

import axios, { type AxiosInstance } from 'axios';

const baseURL =
  (import.meta.env.VITE_API_URL as string | undefined) ||
  (import.meta.env.PROD
    ? 'https://ims-20-railway-production.up.railway.app/api/v1'
    : '/api/v1');

const portalClient: AxiosInstance = axios.create({
  baseURL,
  timeout: 20_000,
  // No auth interceptor — credentials are passed explicitly per-call.
});

// ----------------------------------------------------------------------------
// Types — mirror the backend portal.py response shapes
// ----------------------------------------------------------------------------

export interface TrackingStatusEntry {
  status: string | null;
  timestamp: string | null;
}

export interface TrackingItem {
  description: string;
  quantity: number;
}

export interface OrderTracking {
  order_number: string | null;
  status: string | null;
  status_history: TrackingStatusEntry[];
  expected_delivery: string | null;
  delivery_priority: string | null;
  placed_at: string | null;
  item_count: number;
  items: TrackingItem[];
  customer_first_name: string | null;
  store_name: string | null;
  store_phone: string | null;
}

export interface OtpRequestResponse {
  ok: boolean;
  message: string;
  expires_in_seconds: number;
  /** Only present when PORTAL_OTP_DEBUG=true on the backend (non-prod). */
  debug_otp?: string;
}

export interface OtpVerifyResponse {
  ok: boolean;
  view_token: string;
  token_type: string;
  expires_in: number;
}

export interface PortalPrescription {
  prescription_id: string | null;
  prescription_number: string | null;
  prescription_date: string | null;
  expiry_date: string | null;
  type: string | null;
  right_eye: Record<string, unknown> | null;
  left_eye: Record<string, unknown> | null;
  pd: string | number | null;
  add_power: string | number | null;
  notes: string | null;
  optometrist_name: string | null;
  store_name: string | null;
}

export interface PortalRxResponse {
  customer_id: string;
  customer_first_name: string | null;
  prescriptions: PortalPrescription[];
  count: number;
}

// ----------------------------------------------------------------------------
// Public API
// ----------------------------------------------------------------------------

export const portalApi = {
  /** Public order tracking by tokenized link. */
  trackOrder: async (token: string): Promise<OrderTracking> => {
    const res = await portalClient.get(`/portal/track/${encodeURIComponent(token)}`);
    return res.data as OrderTracking;
  },

  /** Request an Rx-access OTP for a phone number. Always returns a generic
   *  success (never reveals whether the phone exists). */
  requestRxOtp: async (phone: string): Promise<OtpRequestResponse> => {
    const res = await portalClient.post('/portal/rx/request-otp', { phone });
    return res.data as OtpRequestResponse;
  },

  /** Verify the OTP; on success returns a short-lived view token. */
  verifyRxOtp: async (phone: string, otp: string): Promise<OtpVerifyResponse> => {
    const res = await portalClient.post('/portal/rx/verify-otp', { phone, otp });
    return res.data as OtpVerifyResponse;
  },

  /** Fetch the verified customer's prescriptions using the view token. */
  getMyPrescriptions: async (viewToken: string): Promise<PortalRxResponse> => {
    const res = await portalClient.get('/portal/rx', {
      headers: { Authorization: `Bearer ${viewToken}` },
    });
    return res.data as PortalRxResponse;
  },
};
