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
import { getErrorMessage, getErrorStatus } from '../../utils/errorHandler';

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

// The wallet router answers with machine codes (`already_in_household`);
// staff read sentences. An unknown code reads as '' so the screen's own
// fallback shows instead; a real sentence from the server passes through.
const WALLET_ERROR_TEXT: Record<string, string> = {
  already_in_household: 'This customer is already in another household.',
  already_member: 'This customer is already a member of this household.',
  customer_not_found: 'Customer not found.',
  invalid_customer: 'That customer record is not valid.',
  household_not_found: 'Household not found.',
  household_inactive: 'This household is no longer active.',
  household_full: 'This household already has the most members it can hold.',
  insufficient_balance: 'The household does not have enough points.',
  invalid_points: 'Enter a valid number of points.',
  not_a_member: 'This customer is not a member of the household.',
  primary_irremovable: 'The primary member cannot be removed.',
  otp_amount_mismatch: 'That OTP was issued for a different number of points.',
  otp_household_mismatch: 'That OTP was issued for a different household.',
  otp_unavailable: 'OTP delivery is not available right now.',
  voucher_mint_failed: 'The voucher could not be created. The points were returned to the household.',
};

export function walletErrorText(error: unknown): string {
  const text = getErrorMessage(error, '');
  return WALLET_ERROR_TEXT[text] ?? (/^[a-z_]+$/.test(text) ? '' : text);
}

export const familyWalletApi = {
  /** The ACTIVE household containing this customer, or null if none. Chain-wide. */
  getByCustomer: async (customerId: string): Promise<Household | null> => {
    try {
      const r = await api.get(`/family-wallet/households/by-customer/${customerId}`);
      return r.data as Household;
    } catch (err: unknown) {
      // 404 => the customer is not in any household (a normal, expected state).
      if (getErrorStatus(err) === 404) return null;
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
