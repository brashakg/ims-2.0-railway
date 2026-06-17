// ============================================================================
// IMS 2.0 - E4 Approvals API (PIN-gated maker-checker)
// ============================================================================
// Typed client for the merged approval engine (backend/api/routers/approvals.py)
// + the per-user PIN endpoints (backend/api/routers/users.py).
//
// The approve / reject / consume endpoints return a STRUCTURED `detail`
// ({error, remaining, retry_after_min, status}) on failure with a meaningful
// HTTP status (423 PIN-locked, 403 insufficient-tier/wrong-pin, 409 already-
// reviewed/consumed, 410 expired). The shared axios error interceptor flattens
// non-string `detail` into a generic Error and drops the status code, so those
// three calls use `validateStatus: () => true` to read the raw status + detail
// here and re-shape them into a typed { ok, status, error } result the UI can
// branch on cleanly. The plain GET/list calls go through the normal client.
//
// Import directly (and via the api barrel) per the established convention.

import api from './client';

// ----------------------------------------------------------------------------
// Types
// ----------------------------------------------------------------------------

export type ApprovalActionType =
  | 'discount_override'
  | 'refund'
  | 'journal_entry'
  | 'profile_merge'
  | 'petty_cash'
  | 'endless_aisle'
  | 'rtv'
  | 'RETURN_SERIAL_OVERRIDE'
  // F27: a tiered, PIN-gated refund approval (configurable refund matrix).
  | 'REFUND_APPROVAL_MATRIX'
  // F26: a remote PIN-gated leave approval routed through the same engine.
  | 'leave_approval';

export type ApprovalStatus =
  | 'REQUESTED'
  | 'APPROVED'
  | 'REJECTED'
  | 'EXPIRED'
  | 'CONSUMED';

export type ApprovalTier = 'auto' | 'admin' | 'super';

/** A single approval request row (inbox / mine / get). Fields mirror the
 *  `approval_requests` collection; absent values come back null. */
export interface ApprovalRequest {
  request_id: string;
  action_type: ApprovalActionType | string;
  status: ApprovalStatus | string;
  requested_by: string | null;
  requested_by_roles?: string[];
  store_id: string | null;
  entity_id?: string | null;
  amount: number | null;
  required_tier?: ApprovalTier | string;
  required_roles?: string[];
  context?: Record<string, unknown>;
  reason?: string;
  maker_checker?: boolean;
  created_at: string | null;
  expires_at: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  reject_reason?: string | null;
  /** Only present to the maker / consumer / HQ (ADMIN, SUPERADMIN). */
  approval_token?: string | null;
  consumed?: boolean;
  consumed_at?: string | null;
  consumed_by?: string | null;
}

export interface ApprovalListResponse {
  requests: ApprovalRequest[];
  total: number;
}

export interface CreateRequestPayload {
  action_type: ApprovalActionType | string;
  store_id?: string | null;
  entity_id?: string | null;
  amount?: number | null;
  context?: Record<string, unknown>;
  reason?: string;
  required_tier?: ApprovalTier | string;
  dedupe_key?: string;
}

export interface CreateRequestResponse {
  ok: boolean;
  request_id: string;
  status: string;
  required_tier?: string;
  required_roles?: string[];
  expires_at?: string;
  deduped?: boolean;
}

/** Normalised result of an approve / reject call. `ok` true -> success body;
 *  `ok` false -> a status code + machine error the modal maps to a message. */
export interface ApproveResult {
  ok: boolean;
  status?: number; // HTTP status (only on failure)
  error?: string; // machine error code, e.g. 'wrong_pin'
  remaining?: number; // remaining PIN attempts (on wrong_pin)
  retry_after_min?: number; // minutes until unlock (on pin_locked)
  request_status?: string; // request status on a 409 already_reviewed
  // success-only:
  approval_token?: string;
  reviewed_at?: string;
}

export interface ConsumeResult {
  ok: boolean;
  status?: number;
  error?: string;
  request?: ApprovalRequest;
}

export interface PinStatus {
  has_pin: boolean;
  pin_set_at?: string | null;
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

interface ErrorDetail {
  error?: string;
  remaining?: number;
  retry_after_min?: number;
  status?: string;
}

/** The backend wraps structured failures as { detail: <string | object> }.
 *  Pull the machine fields out regardless of which shape arrived. */
function readDetail(data: unknown): ErrorDetail {
  const detail = (data as { detail?: unknown } | null | undefined)?.detail;
  if (detail && typeof detail === 'object') return detail as ErrorDetail;
  if (typeof detail === 'string') return { error: detail };
  return {};
}

// ----------------------------------------------------------------------------
// API
// ----------------------------------------------------------------------------

export const approvalsApi = {
  // --- maker ---------------------------------------------------------------
  createRequest: async (
    payload: CreateRequestPayload,
  ): Promise<CreateRequestResponse> => {
    const { data } = await api.post<CreateRequestResponse>(
      '/approvals/requests',
      payload,
    );
    return data;
  },

  /** A maker's own requests + live status (and approval_token once approved). */
  getMyRequests: async (): Promise<ApprovalListResponse> => {
    const { data } = await api.get<ApprovalListResponse>(
      '/approvals/requests/mine',
    );
    return data;
  },

  // --- approver inbox ------------------------------------------------------
  /** Approver inbox. status defaults to REQUESTED server-side; pass 'ALL' for
   *  history. store_id narrows a scoped approver to one of their stores. */
  getInbox: async (params?: {
    status?: ApprovalStatus | 'ALL';
    store_id?: string;
  }): Promise<ApprovalListResponse> => {
    const { data } = await api.get<ApprovalListResponse>(
      '/approvals/requests/inbox',
      { params },
    );
    return data;
  },

  getRequest: async (requestId: string): Promise<ApprovalRequest> => {
    const { data } = await api.get<ApprovalRequest>(
      `/approvals/requests/${requestId}`,
    );
    return data;
  },

  // --- approve / reject (PIN-gated, structured failures) -------------------
  approve: async (
    requestId: string,
    pin: string,
  ): Promise<ApproveResult> => {
    const res = await api.post(
      `/approvals/requests/${requestId}/approve`,
      { pin },
      { validateStatus: () => true },
    );
    if (res.status >= 200 && res.status < 300) {
      return {
        ok: true,
        approval_token: res.data?.approval_token,
        reviewed_at: res.data?.reviewed_at,
      };
    }
    const d = readDetail(res.data);
    return {
      ok: false,
      status: res.status,
      error: d.error,
      remaining: d.remaining,
      retry_after_min: d.retry_after_min,
      request_status: d.status,
    };
  },

  reject: async (
    requestId: string,
    pin: string,
    reason: string,
  ): Promise<ApproveResult> => {
    const res = await api.post(
      `/approvals/requests/${requestId}/reject`,
      { pin, reason },
      { validateStatus: () => true },
    );
    if (res.status >= 200 && res.status < 300) {
      return { ok: true };
    }
    const d = readDetail(res.data);
    return {
      ok: false,
      status: res.status,
      error: d.error,
      remaining: d.remaining,
      retry_after_min: d.retry_after_min,
      request_status: d.status,
    };
  },

  // --- consume (maker spends an APPROVED token exactly once) ---------------
  consume: async (
    requestId: string,
    body: { action_type: string; approval_token?: string; amount?: number },
  ): Promise<ConsumeResult> => {
    const res = await api.post(
      `/approvals/requests/${requestId}/consume`,
      body,
      { validateStatus: () => true },
    );
    if (res.status >= 200 && res.status < 300) {
      return { ok: true, request: res.data?.request };
    }
    const d = readDetail(res.data);
    return { ok: false, status: res.status, error: d.error };
  },

  // --- per-user PIN management (users.py) ----------------------------------
  /** Set / rotate the user's approval PIN. current_pin is required for a
   *  self-rotation when a PIN already exists (admins force-set without it). */
  setPin: async (
    userId: string,
    pin: string,
    currentPin?: string,
  ): Promise<{ ok: boolean; pin_set_at?: string }> => {
    const { data } = await api.put(`/users/${userId}/approval-pin`, {
      pin,
      ...(currentPin ? { current_pin: currentPin } : {}),
    });
    return data;
  },

  deletePin: async (userId: string): Promise<{ ok: boolean }> => {
    const { data } = await api.delete(`/users/${userId}/approval-pin`);
    return data;
  },

  getPinStatus: async (userId: string): Promise<PinStatus> => {
    const { data } = await api.get<PinStatus>(
      `/users/${userId}/approval-pin/status`,
    );
    return data;
  },
};

export default approvalsApi;
