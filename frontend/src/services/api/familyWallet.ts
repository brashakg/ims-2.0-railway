// ============================================================================
// IMS 2.0 — Family/Household Loyalty Wallet API client (Feature #49)
// ============================================================================
// Backend: backend/api/routers/family_wallet.py at /api/v1/family-wallet
//
// A household groups up to 7 customers into ONE shared loyalty-points pool.
// Any member can redeem anywhere (chain-wide). Redemption is OTP-gated to the
// PRIMARY member's mobile, BUT outbound SMS/WhatsApp is currently DISABLED
// (DISPATCH_MODE), so the OTP step is deferred in the UI — redemption uses the
// owner policy (loyalty.pool_redeem_requires_otp) which may waive the gate.
//
// NOTE: imported DIRECTLY from this module (not via the services/api barrel) —
// newly-added services do not resolve through the barrel re-export (TS2614).

import api from './client';

// ============================================================================
// Types
// ============================================================================

export type HouseholdStatus = 'ACTIVE' | 'DISSOLVED';

export interface Household {
  household_id: string;
  primary_customer_id: string;
  member_customer_ids: string[];
  store_id?: string | null;
  status: HouseholdStatus;
  created_by?: string | null;
  created_at?: string;
  updated_at?: string;
  /** Live pool balance in POINTS, attached by the read endpoints. */
  pool_balance_points?: number;
}

export interface PoolEarnResponse {
  ok: boolean;
  household_id: string;
  points_credited: number;
  pool_balance_points: number;
  duplicate: boolean;
}

export interface RequestOtpResponse {
  otp_id: string;
  expires_at: string;
  sent_to: string;
}

export interface PoolRedeemResponse {
  ok: boolean;
  duplicate?: boolean;
  household_id: string;
  points_redeemed: number;
  rupee_value?: number;
  pool_balance_points: number;
  txn_id?: string;
  voucher: {
    voucher_id: string;
    code: string;
    balance: number;
    expiry_date?: string | null;
  };
}

// ============================================================================
// API
// ============================================================================

export const familyWalletApi = {
  /** The ACTIVE household containing this customer, or null if none. Chain-wide. */
  getByCustomer: async (customerId: string): Promise<Household | null> => {
    try {
      const r = await api.get(`/family-wallet/households/by-customer/${customerId}`);
      return r.data as Household;
    } catch (err: unknown) {
      // 404 => the customer is not in any household (a normal, expected state).
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 404) return null;
      throw err;
    }
  },

  /** One household + its live pool balance (points). Chain-wide. */
  getHousehold: async (householdId: string): Promise<Household> => {
    const r = await api.get(`/family-wallet/households/${householdId}`);
    return r.data as Household;
  },

  /** Create a household with this customer as the primary (member 0). Manager+. */
  createHousehold: async (
    primaryCustomerId: string,
    storeId?: string,
  ): Promise<Household> => {
    const r = await api.post('/family-wallet/households', {
      primary_customer_id: primaryCustomerId,
      store_id: storeId,
    });
    return r.data as Household;
  },

  /** Add a member (manager+). 409 if the household is full (max 7) or the
   * customer already belongs to a household. */
  addMember: async (householdId: string, customerId: string): Promise<Household> => {
    const r = await api.post(`/family-wallet/households/${householdId}/members`, {
      customer_id: customerId,
    });
    return r.data as Household;
  },

  /** Remove a NON-PRIMARY member (manager+). The primary is irremovable. */
  removeMember: async (
    householdId: string,
    customerId: string,
  ): Promise<Household> => {
    const r = await api.delete(
      `/family-wallet/households/${householdId}/members/${customerId}`,
    );
    return r.data as Household;
  },

  /** Credit POINTS to the household pool (manager+). Idempotent per order ref. */
  earn: async (
    householdId: string,
    points: number,
    sourceOrderId?: string,
  ): Promise<PoolEarnResponse> => {
    const r = await api.post(`/family-wallet/households/${householdId}/earn`, {
      points,
      source_order_id: sourceOrderId,
    });
    return r.data as PoolEarnResponse;
  },

  /** Issue a redemption OTP to the primary member's mobile. Returns otp_id only
   * (never the code). NOTE: SMS is gated by DISPATCH_MODE — when dark, no text
   * is actually sent and redemption should rely on the owner OTP-waive policy. */
  requestRedeemOtp: async (
    householdId: string,
    points: number,
  ): Promise<RequestOtpResponse> => {
    const r = await api.post(
      `/family-wallet/households/${householdId}/redeem/request-otp`,
      { points },
    );
    return r.data as RequestOtpResponse;
  },

  /** Redeem points from the pool -> mints a store-credit voucher. otp_id/otp_code
   * are required only when the owner policy keeps the OTP gate on. */
  redeem: async (
    householdId: string,
    payload: {
      points: number;
      redeeming_customer_id: string;
      otp_id?: string;
      otp_code?: string;
    },
  ): Promise<PoolRedeemResponse> => {
    const r = await api.post(
      `/family-wallet/households/${householdId}/redeem`,
      payload,
    );
    return r.data as PoolRedeemResponse;
  },
};
