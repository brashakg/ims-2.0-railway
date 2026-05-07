// ============================================================================
// IMS 2.0 — Walkouts API client (Pune Incentive Module i, Phases 1-2)
// ============================================================================
// Backend: backend/api/routers/walkouts.py mounted at /api/v1/walkouts
// See docs/PUNE_INCENTIVE_BUILD_PLAN.md for the full programme spec.

import api from './client';
import type {
  Walkout,
  CreateWalkoutRequest,
  UpdateWalkoutRequest,
  ListWalkoutsParams,
  ListWalkoutsResponse,
} from '../../types';

export const walkoutsApi = {
  /**
   * Phase 1 — log a new walkout. Server stamps walkout_id, store_id
   * (from session.active_store_id), sales_person_name, and links/auto-
   * creates a customer row from the supplied mobile.
   */
  createWalkout: async (payload: CreateWalkoutRequest): Promise<Walkout> => {
    const response = await api.post('/walkouts', payload);
    return response.data;
  },

  /** Phase 1 — fetch one walkout by id. */
  getWalkout: async (walkoutId: string): Promise<Walkout> => {
    const response = await api.get(`/walkouts/${walkoutId}`);
    return response.data;
  },

  /**
   * Phase 2 — list walkouts for the active store, sorted newest-first.
   * SUPERADMIN/ADMIN may override the store via params.store_id; everyone
   * else is implicitly scoped to their session store.
   */
  listWalkouts: async (
    params: ListWalkoutsParams = {},
  ): Promise<ListWalkoutsResponse> => {
    const response = await api.get('/walkouts', { params });
    return response.data;
  },

  /** Phase 2 — partial update; server computes the diff and audit-logs it. */
  updateWalkout: async (
    walkoutId: string,
    diff: UpdateWalkoutRequest,
  ): Promise<Walkout> => {
    const response = await api.patch(`/walkouts/${walkoutId}`, diff);
    return response.data;
  },

  /** Phase 2 — soft-delete (SUPERADMIN / STORE_MANAGER only). */
  deleteWalkout: async (
    walkoutId: string,
    reason: string,
  ): Promise<{ walkout_id: string; deleted: boolean }> => {
    const response = await api.delete(`/walkouts/${walkoutId}`, {
      data: { reason },
    });
    return response.data;
  },
};

export default walkoutsApi;
