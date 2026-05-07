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

  /** Build a CSV-download URL the browser can open directly. */
  csvUrl: (snapshotId: string): string => {
    return `/api/v1/payout/export/${snapshotId}.csv`;
  },
};

export default payoutApi;
