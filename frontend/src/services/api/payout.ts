// ============================================================================
// IMS 2.0 — Payout API client (Pune Incentive Module iii)
// ============================================================================
// Backend: backend/api/routers/payout.py at /api/v1/payout

import api from './client';
import type {
  PayoutEnvelope,
  PayoutSnapshot,
  PreviewParams,
  LockSnapshotRequest,
  SnapshotsListResponse,
} from '../../types';

export const payoutApi = {
  /** Live computation. No DB write. */
  preview: async (params: PreviewParams = {}): Promise<PayoutEnvelope> => {
    const r = await api.get('/payout/preview', { params });
    return r.data;
  },

  /** SUPERADMIN only — persist as immutable LOCKED snapshot. */
  lock: async (
    payload: LockSnapshotRequest,
    storeId?: string,
  ): Promise<PayoutSnapshot> => {
    const r = await api.post('/payout/lock', payload, {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return r.data;
  },

  list: async (
    year?: number, storeId?: string,
  ): Promise<SnapshotsListResponse> => {
    const r = await api.get('/payout/snapshots', {
      params: {
        ...(year ? { year } : {}),
        ...(storeId ? { store_id: storeId } : {}),
      },
    });
    return r.data;
  },

  get: async (snapshotId: string): Promise<PayoutSnapshot> => {
    const r = await api.get(`/payout/snapshot/${snapshotId}`);
    return r.data;
  },

  /** SUPERADMIN only — flip LOCKED → PAID. */
  markPaid: async (
    snapshotId: string, note?: string,
  ): Promise<PayoutSnapshot> => {
    const r = await api.patch(
      `/payout/snapshot/${snapshotId}/mark-paid`,
      { note: note || '' },
    );
    return r.data;
  },

  /** Download a snapshot's per-staff CSV via the AUTHENTICATED axios instance.
   *  The old `csvUrl` returned a bare relative path used as an <a href>, which on
   *  the Vercel frontend origin 404s (no backend there) and carries no JWT (401),
   *  so the export never worked in production. This fetches the CSV as a blob
   *  through `api` (correct base URL + the auth-header interceptor) and triggers a
   *  browser save. Throws on failure so the caller can surface it. */
  downloadCsv: async (snapshotId: string, filename?: string): Promise<void> => {
    const r = await api.get(`/payout/export/${snapshotId}.csv`, {
      responseType: 'blob',
    });
    const blob = new Blob([r.data], { type: 'text/csv;charset=utf-8' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || `payout-${snapshotId}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  },
};

export default payoutApi;
