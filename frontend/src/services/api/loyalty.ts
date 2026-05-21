// ============================================================================
// IMS 2.0 — Loyalty API client
// ============================================================================
// Backend: backend/api/routers/loyalty.py at /api/v1/loyalty

import api from './client';

// ============================================================================
// Types
// ============================================================================

export type LoyaltyTier = 'BRONZE' | 'SILVER' | 'GOLD' | 'PLATINUM';
export type LoyaltyTxnType = 'EARN' | 'REDEEM' | 'EXPIRE' | 'ADJUST';

export interface LoyaltyAccount {
  customer_id: string;
  balance_points: number;
  tier: LoyaltyTier;
  lifetime_earned: number;
  lifetime_redeemed: number;
  last_activity_at?: string;
  updated_at?: string;
  created_at?: string;
}

export interface LoyaltyTransaction {
  txn_id: string;
  customer_id: string;
  type: LoyaltyTxnType;
  points: number;
  rupee_value: number;
  order_id: string | null;
  reason: string;
  expires_at: string | null;
  expired?: boolean;
  created_at: string;
  created_by?: string;
  tier_at_earn?: LoyaltyTier;
  tier_multiplier?: number;
}

export interface LoyaltySettings {
  enabled: boolean;
  points_per_rupee: number;
  category_multipliers: Record<string, number>;
  min_order_for_earn: number;
  expiry_days: number;
  redeem_rupee_per_point: number;
  min_redeem_points: number;
  max_redeem_pct_of_order: number;
  tier_thresholds: Record<string, number>;
  tier_multipliers: Record<string, number>;
}

export interface LoyaltyAccountResponse {
  account: LoyaltyAccount;
  recent_transactions: LoyaltyTransaction[];
  expiring_soon_points?: number;
  settings: LoyaltySettings;
}

export interface LoyaltyLedgerResponse {
  items: LoyaltyTransaction[];
  total: number;
  limit: number;
  skip: number;
}

export interface EarnRequest {
  customer_id: string;
  order_id?: string;
  rupee_value: number;
  items?: Array<{
    item_total?: number;
    line_total?: number;
    amount?: number;
    unit_price?: number;
    quantity?: number;
    category?: string;
    item_type?: string;
  }>;
  reason?: string;
}

export interface EarnResponse {
  awarded: number;
  txn_id?: string;
  tier?: LoyaltyTier;
  tier_changed?: boolean;
  rupee_value?: number;
  skipped_reason?: string;
  deduped?: boolean;
}

export interface RedeemRequest {
  customer_id: string;
  order_id?: string;
  points: number;
  order_value?: number;
}

export interface RedeemResponse {
  redeemed_points: number;
  rupee_value: number;
  was_capped: boolean;
  txn_id: string;
}

export interface AdjustRequest {
  customer_id: string;
  points: number; // signed
  reason: string;
}

export interface LoyaltyProgramStats {
  total_members: number;
  by_tier: Record<string, number>;
  active_points_balance: number;
  points_issued: number;
  points_redeemed: number;
  redemption_rate: number; // percent
  avg_points_per_member: number;
}

export const loyaltyApi = {
  /** Account snapshot + last 20 ledger rows + engine config. */
  getAccount: async (customerId: string): Promise<LoyaltyAccountResponse> => {
    const r = await api.get(`/loyalty/account/${customerId}`);
    return r.data;
  },

  /** Paginated full ledger (newest-first). */
  getLedger: async (
    customerId: string,
    params: { limit?: number; skip?: number; type?: LoyaltyTxnType } = {},
  ): Promise<LoyaltyLedgerResponse> => {
    const r = await api.get(`/loyalty/account/${customerId}/ledger`, { params });
    return r.data;
  },

  /** Award earn points for an order. Idempotent on (customer_id, order_id). */
  earn: async (payload: EarnRequest): Promise<EarnResponse> => {
    const r = await api.post('/loyalty/earn', payload);
    return r.data;
  },

  /** Deduct points and return the rupee discount they map to. */
  redeem: async (payload: RedeemRequest): Promise<RedeemResponse> => {
    const r = await api.post('/loyalty/redeem', payload);
    return r.data;
  },

  /** SUPERADMIN/ADMIN only — manual credit/debit. */
  adjust: async (payload: AdjustRequest): Promise<{
    txn_id: string;
    delta: number;
    balance_after: number;
    tier: LoyaltyTier;
  }> => {
    const r = await api.post('/loyalty/adjust', payload);
    return r.data;
  },

  /** Engine config — readable by anyone, writable by SUPERADMIN. */
  getSettings: async (): Promise<LoyaltySettings> => {
    const r = await api.get('/loyalty/settings');
    return r.data;
  },

  /** Chain-wide program summary: members, tier mix, points issued/redeemed. */
  getProgramStats: async (): Promise<LoyaltyProgramStats> => {
    const r = await api.get('/loyalty/program-stats');
    return r.data;
  },

  updateSettings: async (
    patch: Partial<LoyaltySettings>,
  ): Promise<LoyaltySettings> => {
    const r = await api.put('/loyalty/settings', patch);
    return r.data;
  },

  /** CRON / admin sweep. */
  expireSweep: async (): Promise<{
    expired_txns: number;
    points_expired: number;
  }> => {
    const r = await api.post('/loyalty/expire');
    return r.data;
  },
};
