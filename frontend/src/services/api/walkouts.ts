// ============================================================================
// IMS 2.0 — Walkouts API client (Pune Incentive Module i, Phase 1)
// ============================================================================
// Backend: backend/api/routers/walkouts.py mounted at /api/v1/walkouts
// See docs/PUNE_INCENTIVE_BUILD_PLAN.md for the full programme spec.

import api from './client';
import type {
  Walkout,
  CreateWalkoutRequest,
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

  /**
   * Phase 1 — fetch one walkout by id.
   */
  getWalkout: async (walkoutId: string): Promise<Walkout> => {
    const response = await api.get(`/walkouts/${walkoutId}`);
    return response.data;
  },
};

export default walkoutsApi;
