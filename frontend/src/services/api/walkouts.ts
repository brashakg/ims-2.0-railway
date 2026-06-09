// ============================================================================
// IMS 2.0 — Walkouts API client (Pune Incentive Module i, Phases 1-3)
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
  CreateFollowUpRequest,
  UpdateFollowUpRequest,
  ApproveFollowUpRequest,
  SetWalkoutResultRequest,
  FollowUpsDueResponse,
  WalkinTodayResponse,
  ManualTopupRequest,
  PerStaffResponse,
  TopReasonsResponse,
  ResultBreakdownResponse,
  FUStatusResponse,
  WalkoutPosComplianceCheck,
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

  /** Phase 3 — append a follow-up sub-doc (round 1 or 2). */
  appendFollowUp: async (
    walkoutId: string,
    payload: CreateFollowUpRequest,
  ): Promise<Walkout> => {
    const response = await api.post(
      `/walkouts/${walkoutId}/followups`, payload,
    );
    return response.data;
  },

  /** Phase 3 — partial update of a follow-up. Server stamps
   * completed_at + completed_by when status flips to DONE. */
  updateFollowUp: async (
    walkoutId: string,
    round: 1 | 2 | 3,
    patch: UpdateFollowUpRequest,
  ): Promise<Walkout> => {
    const response = await api.patch(
      `/walkouts/${walkoutId}/followups/${round}`, patch,
    );
    return response.data;
  },

  /** Manager approval / rejection of a DONE follow-up. Role-gated on
   * the server to STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN —
   * salespeople hit 403 if they try to self-approve. */
  approveFollowUp: async (
    walkoutId: string,
    round: 1 | 2 | 3,
    payload: ApproveFollowUpRequest,
  ): Promise<Walkout> => {
    const response = await api.post(
      `/walkouts/${walkoutId}/followups/${round}/approve`, payload,
    );
    return response.data;
  },

  /** Phase 3 — set walkout outcome. CONVERTED requires a real order id. */
  setResult: async (
    walkoutId: string,
    payload: SetWalkoutResultRequest,
  ): Promise<Walkout> => {
    const response = await api.patch(
      `/walkouts/${walkoutId}/result`, payload,
    );
    return response.data;
  },

  /** Phase 3 — pending follow-ups due today (for the FU dashboard widget). */
  followupsDueToday: async (
    storeId?: string,
  ): Promise<FollowUpsDueResponse> => {
    const response = await api.get('/walkouts/followups/due-today', {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return response.data;
  },

  /** Phase 3 — cron-callable; turns overdue FUs into Tasks. */
  escalateOverdueFollowUps: async (): Promise<{
    escalated: number;
    created_tasks: Array<{
      walkout_id: string;
      round: number;
      task_id: string;
      priority: 'P1' | 'P2';
      assignee: string;
    }>;
    as_of: string;
  }> => {
    const response = await api.post('/walkouts/followups/escalate-overdue');
    return response.data;
  },

  // Phase 4 — walk-in counter + dashboard

  walkinsToday: async (storeId?: string): Promise<WalkinTodayResponse> => {
    const r = await api.get('/walkouts/walkins/today', { params: storeId ? { store_id: storeId } : undefined });
    return r.data;
  },

  walkinsManualTopup: async (
    payload: ManualTopupRequest, storeId?: string,
  ): Promise<WalkinTodayResponse> => {
    const r = await api.post('/walkouts/walkins/manual-topup', payload, {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return r.data;
  },

  /** Sales-staff "+1 walk-in" from POS. Attributes to the salesperson;
   *  deduped per (mobile, day) server-side. */
  walkinsPosIncrement: async (
    payload: { sales_person_id: string; mobile?: string }, storeId?: string,
  ): Promise<{ total: number; pos_auto_count: number; deduped: boolean }> => {
    const r = await api.post('/walkouts/walkins/pos-increment', payload, {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return r.data;
  },

  dashboardPerStaff: async (storeId?: string): Promise<PerStaffResponse> => {
    const r = await api.get('/walkouts/dashboard/per-staff', { params: storeId ? { store_id: storeId } : undefined });
    return r.data;
  },

  dashboardTopReasons: async (
    days = 30, limit = 10, storeId?: string,
  ): Promise<TopReasonsResponse> => {
    const r = await api.get('/walkouts/dashboard/top-reasons', {
      params: { days, limit, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data;
  },

  dashboardResultBreakdown: async (
    days = 30, storeId?: string,
  ): Promise<ResultBreakdownResponse> => {
    const r = await api.get('/walkouts/dashboard/result-breakdown', {
      params: { days, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data;
  },

  dashboardFuStatus: async (
    days = 30, storeId?: string,
  ): Promise<FUStatusResponse> => {
    const r = await api.get('/walkouts/dashboard/fu-status', {
      params: { days, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data;
  },

  /** F45 D5 -- POS soft-block compliance counter for a (store, salesperson).
   *  Read-only; drives a dismissable POS banner. NEVER blocks a sale. */
  posComplianceCheck: async (
    storeId: string, salesPersonId: string,
  ): Promise<WalkoutPosComplianceCheck> => {
    const r = await api.get('/walkouts/pos-compliance-check', {
      params: { store_id: storeId, sales_person_id: salesPersonId },
    });
    return r.data;
  },
};

export default walkoutsApi;
