// ============================================================================
// IMS 2.0 - Loyalty account + ledger: THE one read for a customer's points
// ============================================================================
// GET /loyalty/account/{id} (balance, tier, expiring-soon) together with
// GET /loyalty/account/{id}/ledger (paginated, newest first). Used by the
// per-customer Loyalty Ledger page and the POS customer panel's Offers &
// loyalty section, so one fetch feeds both screens. Read-only.

import { useQuery } from '@tanstack/react-query';
import {
  loyaltyApi,
  type LoyaltyAccountResponse,
  type LoyaltyLedgerResponse,
  type LoyaltyTxnType,
} from '../services/api/loyalty';

export interface LoyaltyLedgerParams {
  limit?: number;
  skip?: number;
  type?: LoyaltyTxnType | null;
}

export const loyaltyLedgerQueryKey = (customerId: string, params: LoyaltyLedgerParams) =>
  ['loyalty', 'ledger', customerId, params.limit ?? null, params.skip ?? 0, params.type ?? null] as const;

export function useLoyaltyLedger(
  customerId: string | null | undefined,
  params: LoyaltyLedgerParams = {},
) {
  return useQuery<{ account: LoyaltyAccountResponse; ledger: LoyaltyLedgerResponse }, Error>({
    queryKey: loyaltyLedgerQueryKey(customerId || '', params),
    enabled: !!customerId,
    queryFn: async () => {
      const id = customerId as string;
      const [account, ledger] = await Promise.all([
        loyaltyApi.getAccount(id),
        loyaltyApi.getLedger(id, {
          limit: params.limit,
          skip: params.skip,
          type: params.type ?? undefined,
        }),
      ]);
      return { account, ledger };
    },
  });
}
