// ============================================================================
// IMS 2.0 — Daily Points API client (Pune Incentive Module ii)
// ============================================================================
// Backend: backend/api/routers/points.py at /api/v1/incentive/points

import api from './client';
import type {
  CreateDailyPointsRequest,
  BulkDailyPointsRequest,
  BulkPointsResponse,
  DailyListResponse,
  PointsLog,
  MTDResponse,
  LeaderboardResponse,
  LeaderboardScope,
  LeaderboardTitlesResponse,
  LeaderboardConfig,
  IncentiveSettings,
  EligibilityBand,
  KickerEntry,
  KickerRollupResponse,
} from '../../types';

export const incentiveApi = {
  /** P1 — log one staff's points for one day. 409 if duplicate. */
  createDaily: async (
    payload: CreateDailyPointsRequest,
    storeId?: string,
  ): Promise<PointsLog> => {
    const r = await api.post('/incentive/points/daily', payload, {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return r.data;
  },

  /** P2 — bulk save (per-row success/failure). */
  createBulk: async (
    body: BulkDailyPointsRequest,
    storeId?: string,
  ): Promise<BulkPointsResponse> => {
    const r = await api.post('/incentive/points/daily/bulk', body, {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return r.data;
  },

  /** P1 — list rows for one (store, date). */
  listDaily: async (
    date?: string, storeId?: string,
  ): Promise<DailyListResponse> => {
    const r = await api.get('/incentive/points/daily', {
      params: { ...(date ? { date } : {}), ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data;
  },

  /** P2 — soft-delete (frees the unique-key slot for re-save). */
  deleteDaily: async (
    logId: string, reason: string,
  ): Promise<{ log_id: string; deleted: boolean }> => {
    const r = await api.delete(`/incentive/points/daily/${logId}`, {
      data: { reason },
    });
    return r.data;
  },

  /** P3 — Module (iii) contract: per-staff MTD aggregation. */
  getMtd: async (
    year?: number, month?: number, storeId?: string,
  ): Promise<MTDResponse> => {
    const r = await api.get('/incentive/points/mtd', {
      params: {
        ...(year ? { year } : {}), ...(month ? { month } : {}),
        ...(storeId ? { store_id: storeId } : {}),
      },
    });
    return r.data;
  },

  /** P3 — leaderboard (sorted by avg.total desc, days_logged tiebreak).
   *  F33: scope=area|org widens the board (managers only — 403 otherwise). */
  getLeaderboard: async (
    days = 30, storeId?: string, scope: LeaderboardScope = 'store',
  ): Promise<LeaderboardResponse> => {
    const r = await api.get('/incentive/points/leaderboard', {
      params: {
        days, scope,
        ...(storeId ? { store_id: storeId } : {}),
      },
    });
    return r.data;
  },

  /** F33 — titles + badges catalog (any authenticated user). */
  getLeaderboardTitles: async (): Promise<LeaderboardTitlesResponse> => {
    const r = await api.get('/incentive/points/leaderboard/titles');
    return r.data;
  },

  /** F33 — upsert the per-store leaderboard display config (ADMIN/SUPERADMIN). */
  updateLeaderboardSettings: async (
    payload: Partial<LeaderboardConfig>,
    storeId?: string,
  ): Promise<{ store_id: string; leaderboard_config: LeaderboardConfig }> => {
    const r = await api.post(
      '/incentive/points/leaderboard/settings',
      payload,
      { params: storeId ? { store_id: storeId } : undefined },
    );
    return r.data;
  },

  /** P3 — per-staff history within a date range. */
  getStaffHistory: async (
    staffId: string,
    dateFrom?: string,
    dateTo?: string,
    storeId?: string,
  ): Promise<{ store_id: string; staff_id: string; date_from: string; date_to: string; items: PointsLog[] }> => {
    const r = await api.get(`/incentive/points/staff/${staffId}/history`, {
      params: {
        ...(dateFrom ? { date_from: dateFrom } : {}),
        ...(dateTo ? { date_to: dateTo } : {}),
        ...(storeId ? { store_id: storeId } : {}),
      },
    });
    return r.data;
  },

  /** P4 — current eligibility bands + visufit gate config. */
  getSettings: async (storeId?: string): Promise<IncentiveSettings> => {
    const r = await api.get('/incentive/points/settings/eligibility', {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return r.data;
  },

  /** P4 — replace eligibility bands (SUPERADMIN-only). */
  updateEligibility: async (
    bands: EligibilityBand[],
    storeId?: string,
  ): Promise<IncentiveSettings> => {
    const r = await api.patch(
      '/incentive/points/settings/eligibility',
      { bands },
      { params: storeId ? { store_id: storeId } : undefined },
    );
    return r.data;
  },

  /** P4 — toggle / re-tune the Visufit gate (SUPERADMIN-only). */
  updateVisufitGate: async (
    payload: { threshold?: number; enabled?: boolean },
    storeId?: string,
  ): Promise<IncentiveSettings> => {
    const r = await api.patch(
      '/incentive/points/settings/visufit-gate',
      payload,
      { params: storeId ? { store_id: storeId } : undefined },
    );
    return r.data;
  },

  /** Module iii — patch the payout calculator inputs (SUPERADMIN-only).
   *  Only the fields you pass are written; unset fields keep their current value. */
  updatePayoutSettings: async (
    payload: {
      growth_targets?: Record<string, number>;
      base_rates?: Record<string, number>;
      discount_kill_threshold?: number;
      discount_multipliers?: Array<{ max_pct: number; multiplier: number }>;
      staff_weightages?: Record<string, number>;
      supervisor_bonuses?: Array<{
        user_id: string;
        role: string;
        bonus_pct: Record<string, number>;
      }>;
    },
    storeId?: string,
  ): Promise<IncentiveSettings> => {
    const r = await api.patch(
      '/incentive/points/settings/payout',
      payload,
      { params: storeId ? { store_id: storeId } : undefined },
    );
    return r.data;
  },

  /** Module iii — set the per-(store, year, month) manual last_year_sale input. */
  setLastYearSale: async (
    payload: { year: number; month: number; last_year_sale: number },
    storeId?: string,
  ): Promise<{ store_id: string; year: number; month: number; last_year_sale: number }> => {
    const r = await api.post(
      '/incentive/points/inputs/last-year-sale',
      payload,
      { params: storeId ? { store_id: storeId } : undefined },
    );
    return r.data;
  },

  /** SC E2 — resolved settings (global -> entity -> store) with per-key source. */
  getEffectiveSettings: async (
    storeId?: string, entityId?: string,
  ): Promise<IncentiveSettings & { _resolution_sources?: Record<string, string> }> => {
    const r = await api.get('/incentive/points/settings/effective', {
      params: {
        ...(storeId ? { store_id: storeId } : {}),
        ...(entityId ? { entity_id: entityId } : {}),
      },
    });
    return r.data;
  },

  /** SC E2 — set chain-wide calculator defaults at entity/global scope. */
  updateScopeSettings: async (
    payload: {
      scope: 'global' | 'entity';
      entity_id?: string;
      eligibility_bands?: EligibilityBand[];
      growth_targets?: Record<string, number>;
      base_rates?: Record<string, number>;
      discount_kill_threshold?: number;
      discount_multipliers?: Array<{ max_pct: number; multiplier: number }>;
      staff_weightages?: Record<string, number>;
      visufit_gate_threshold?: number;
      visufit_gate_enabled?: boolean;
    },
  ): Promise<Record<string, unknown>> => {
    const r = await api.patch('/incentive/points/settings/scope', payload);
    return r.data;
  },

  /** SC Kicker — log a product-incentive sale (manual). 409 on dup order+sku. */
  logProductSale: async (
    payload: {
      staff_id: string;
      date: string;
      sku: string;
      brand: string;
      category: string;
      description?: string;
      order_id?: string;
      product_id?: string;
      incentive_amount: number;
    },
    storeId?: string,
  ): Promise<KickerEntry> => {
    const r = await api.post('/incentive/kicker/product-sale', payload, {
      params: storeId ? { store_id: storeId } : undefined,
    });
    return r.data;
  },

  /** SC Kicker — monthly product-incentive rollup per staff for 'YYYY-MM'. */
  getKickerRollup: async (
    ym: string, storeId?: string, staffId?: string,
  ): Promise<KickerRollupResponse> => {
    const r = await api.get(`/incentive/kicker/${ym}`, {
      params: {
        ...(storeId ? { store_id: storeId } : {}),
        ...(staffId ? { staff_id: staffId } : {}),
      },
    });
    return r.data;
  },
};

export default incentiveApi;
