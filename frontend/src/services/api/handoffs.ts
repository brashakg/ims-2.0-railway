// ============================================================================
// IMS 2.0 — Handoffs API
// ============================================================================
// Typed wrapper for the 9 endpoints in `backend/api/routers/handoffs.py`:
//
//   POST   /handoffs                          upload + create
//   GET    /handoffs/inbox                    recipient's visible cards
//   GET    /handoffs/sent                     uploader's cards
//   GET    /handoffs/{id}                     fetch one
//   GET    /handoffs/{id}/file                stream raw bytes
//   POST   /handoffs/{id}/respond             approved/denied/accepted/received
//   POST   /handoffs/{id}/reshare             forward + mark parent reshared
//   POST   /handoffs/{id}/dismiss             dismiss/keep/snooze
//   DELETE /handoffs/{id}                     uploader-only revoke
//   GET    /handoffs/eligible-recipients/list user picker for the upload UI
//
// Body-shape and response-shape contracts mirror the backend Pydantic
// schemas verbatim — keep these in lock-step if you ever touch
// `handoffs.py`.

import api from './client';

// ============================================================================
// Types
// ============================================================================

export type HandoffResponseValue =
  | 'approved'
  | 'denied'
  | 'accepted'
  | 'received'
  | 'reshared';

export type HandoffRecipientStatus = 'pending' | 'responded';

export interface HandoffRecipient {
  user_id: string;
  user_name: string;
  role: string;
  status: HandoffRecipientStatus;
  response: HandoffResponseValue | null;
  comment: string | null;
  responded_at: string | null;
  dismissed: boolean;
  kept: boolean;
  snooze_until: string | null;
}

export interface HandoffFileMeta {
  file_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
}

/** Full handoff document — what `GET /{id}` and `GET /sent` return. */
export interface Handoff {
  handoff_id: string;
  uploader_id: string;
  uploader_name: string;
  title: string;
  description: string | null;
  file: HandoffFileMeta;
  recipients: HandoffRecipient[];
  created_at: string;
  expires_at: string;
  validity_days: number;
  parent_handoff_id: string | null;
}

/** Per-user inbox view — what `GET /inbox` and the per-recipient mutation
 *  endpoints return (`POST /respond`, `POST /dismiss`). Keys are flattened:
 *  the recipient's own status becomes `my_status`, etc. */
export interface InboxItem {
  handoff_id: string;
  uploader_id: string;
  uploader_name: string;
  title: string;
  description: string | null;
  file: HandoffFileMeta;
  created_at: string;
  expires_at: string;
  validity_days: number;
  parent_handoff_id: string | null;
  my_status: HandoffRecipientStatus;
  my_response: HandoffResponseValue | null;
  my_comment: string | null;
  my_responded_at: string | null;
  my_dismissed: boolean;
  my_kept: boolean;
  my_snooze_until: string | null;
}

export interface EligibleRecipient {
  user_id: string;
  name: string;
  username: string;
  role: string;
}

export type DismissAction = 'dismiss' | 'keep' | 'snooze';

// ============================================================================
// F50 — Clinical -> retail handover (CLINICAL_RX)
// ============================================================================

/** One free-text product recommendation row on a clinical handover (advisory,
 *  no catalog validation). */
export interface ProductRecommendation {
  category?: string | null;
  brand_preference?: string | null;
  notes?: string | null;
}

/** Small Rx summary attached to a clinical-inbox card (live-read from the
 *  prescription, never copied). */
export interface ClinicalRxSummary {
  right_eye?: Record<string, unknown> | null;
  left_eye?: Record<string, unknown> | null;
  expiry_date?: string | null;
  lens_recommendation?: string | null;
  prescription_number?: string | null;
}

/** Per-recipient view of a CLINICAL_RX handover (what GET /clinical-inbox
 *  returns). NO file — the only channel is the in-app bell. */
export interface ClinicalHandover {
  handoff_id: string;
  handoff_type: 'CLINICAL_RX';
  title: string;
  clinical_summary: string | null;
  description: string | null;
  optometrist_id: string | null;
  optometrist_name: string | null;
  patient_name: string | null;
  customer_id: string | null;
  patient_id: string | null;
  prescription_id: string | null;
  eye_test_id: string | null;
  store_id: string | null;
  product_recommendations: ProductRecommendation[];
  created_at: string;
  expires_at: string;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  mark_served: boolean;
  served_by: string | null;
  served_at: string | null;
  rx_summary: ClinicalRxSummary | null;
  my_status: HandoffRecipientStatus;
  my_dismissed: boolean;
  my_kept: boolean;
  my_snooze_until: string | null;
}

// ============================================================================
// Helpers
// ============================================================================

/**
 * Pull a usable filename out of a Content-Disposition header.
 * Falls back to the supplied default when the header is missing or malformed.
 */
function filenameFromContentDisposition(header: string | undefined, fallback: string): string {
  if (!header) return fallback;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  return match?.[1] ? decodeURIComponent(match[1]) : fallback;
}

// ============================================================================
// API
// ============================================================================

export const handoffsApi = {
  /**
   * Upload a file + create a handoff doc assigned to one or more recipients.
   * Sends as multipart/form-data; backend reads `file`, `title`, `description`,
   * `recipient_ids` (JSON-encoded array string), and `validity_days`.
   */
  upload: async (
    file: File,
    title: string,
    recipientIds: string[],
    validityDays: number,
    description?: string,
  ): Promise<Handoff> => {
    const form = new FormData();
    form.append('file', file);
    form.append('title', title);
    form.append('recipient_ids', JSON.stringify(recipientIds));
    form.append('validity_days', String(validityDays));
    if (description && description.trim()) {
      form.append('description', description.trim());
    }
    const response = await api.post<Handoff>('/handoffs', form, {
      // axios will set the boundary header automatically when given a
      // FormData; explicitly clear the JSON content-type so the default
      // doesn't leak in.
      headers: { 'Content-Type': 'multipart/form-data' },
      // 25 MB uploads on a slow line need more breathing room than the
      // shared 10 s default.
      timeout: 60000,
    });
    return response.data;
  },

  listInbox: async (): Promise<{ handoffs: InboxItem[]; total: number }> => {
    const response = await api.get<{ handoffs: InboxItem[]; total: number }>(
      '/handoffs/inbox',
    );
    return response.data;
  },

  listSent: async (): Promise<{ handoffs: Handoff[]; total: number }> => {
    const response = await api.get<{ handoffs: Handoff[]; total: number }>(
      '/handoffs/sent',
    );
    return response.data;
  },

  getHandoff: async (id: string): Promise<Handoff> => {
    const response = await api.get<Handoff>(`/handoffs/${id}`);
    return response.data;
  },

  /**
   * Fetch the underlying file bytes as a Blob plus its filename / mime so
   * callers can either render an inline preview (URL.createObjectURL) or
   * trigger a download. We hit the raw route directly so we keep the
   * response headers (Content-Disposition gives us the real filename).
   */
  downloadFileBlob: async (
    id: string,
  ): Promise<{ blob: Blob; filename: string; mime_type: string }> => {
    const response = await api.get<Blob>(`/handoffs/${id}/file`, {
      responseType: 'blob',
    });
    const mime_type = (response.headers['content-type'] as string) || 'application/octet-stream';
    const filename = filenameFromContentDisposition(
      response.headers['content-disposition'],
      `handoff-${id}`,
    );
    return { blob: response.data, filename, mime_type };
  },

  respond: async (
    id: string,
    response: Exclude<HandoffResponseValue, 'reshared'>,
    comment?: string,
  ): Promise<InboxItem> => {
    const r = await api.post<InboxItem>(`/handoffs/${id}/respond`, {
      response,
      comment: comment && comment.trim() ? comment.trim() : null,
    });
    return r.data;
  },

  reshare: async (
    id: string,
    recipientUserIds: string[],
    comment?: string,
  ): Promise<Handoff> => {
    const r = await api.post<Handoff>(`/handoffs/${id}/reshare`, {
      recipient_user_ids: recipientUserIds,
      comment: comment && comment.trim() ? comment.trim() : null,
    });
    return r.data;
  },

  dismiss: async (
    id: string,
    action: DismissAction,
    snoozeMinutes?: number,
  ): Promise<InboxItem> => {
    const r = await api.post<InboxItem>(`/handoffs/${id}/dismiss`, {
      action,
      snooze_minutes: action === 'snooze' ? snoozeMinutes ?? null : null,
    });
    return r.data;
  },

  revoke: async (id: string): Promise<{ deleted: boolean; handoff_id: string }> => {
    const r = await api.delete<{ deleted: boolean; handoff_id: string }>(
      `/handoffs/${id}`,
    );
    return r.data;
  },

  listEligibleRecipients: async (
    q?: string,
  ): Promise<{ recipients: EligibleRecipient[]; total: number }> => {
    const r = await api.get<{ recipients: EligibleRecipient[]; total: number }>(
      '/handoffs/eligible-recipients/list',
      { params: q && q.trim() ? { q: q.trim() } : undefined },
    );
    return r.data;
  },

  // --- F50: clinical -> retail handover ---

  /** Sales floor's CLINICAL_RX inbox (store-scoped server-side; only the
   *  caller's non-expired, non-dismissed handovers). */
  listClinicalInbox: async (): Promise<{ handoffs: ClinicalHandover[]; total: number }> => {
    const r = await api.get<{ handoffs: ClinicalHandover[]; total: number }>(
      '/handoffs/clinical-inbox',
    );
    return r.data;
  },

  /** Acknowledge a clinical handover (first-seen). Idempotent. */
  acknowledgeClinical: async (
    id: string,
  ): Promise<{ ok: boolean; handoff_id: string; acknowledged_by: string | null; acknowledged_at: string | null }> => {
    const r = await api.patch(`/handoffs/${id}/acknowledge`);
    return r.data;
  },

  /** Mark a clinical handover Served (manual, post-sale). 409 if already served. */
  markServedClinical: async (
    id: string,
  ): Promise<{ ok: boolean; handoff_id: string; served_by: string; served_at: string }> => {
    const r = await api.patch(`/handoffs/${id}/mark-served`);
    return r.data;
  },
};

export default handoffsApi;
