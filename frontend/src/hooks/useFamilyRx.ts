// ============================================================================
// IMS 2.0 - Family Rx: THE one read of a household's prescriptions
// ============================================================================
// GET /prescriptions/family/{customer_id}, grouped by member and annotated by
// the server with expiry_date / is_valid per Rx. Used by the Family Rx page,
// the POS widget tile AND the POS customer panel, so "expired" / "due" can
// never mean two different things on two screens. One rule, one place.
//
// Read-only. Cached under the ['prescriptions', ...] key family so the POS
// query-key invalidation for prescriptions refreshes it too.

import { useQuery } from '@tanstack/react-query';
import { prescriptionApi, type FamilyRxResponse } from '../services/api/sales';

export const familyRxQueryKey = (customerId: string) =>
  ['prescriptions', 'family', customerId] as const;

export function useFamilyRx(customerId: string | null | undefined) {
  return useQuery<FamilyRxResponse, Error>({
    queryKey: familyRxQueryKey(customerId || ''),
    enabled: !!customerId,
    queryFn: () => prescriptionApi.getFamilyRx(customerId as string),
  });
}

/** Months from now until `expiry` (negative once past). null when unknown. */
export function monthsUntil(expiry: string | null | undefined, now: Date = new Date()): number | null {
  if (!expiry) return null;
  const d = new Date(expiry);
  if (Number.isNaN(d.getTime())) return null;
  return Math.round((d.getTime() - now.getTime()) / (1000 * 60 * 60 * 24 * 30.4375));
}

/** The chip a member's latest Rx earns. `dueWithinDays` is the recall window
 *  (owner mockup: expired OR due within 60 days is the household summary). */
export function rxExpiryStatus(
  latest: { expiry_date: string | null; is_valid: boolean | null } | null | undefined,
  now: Date = new Date(),
  dueWithinDays = 60,
): { kind: 'none' | 'expired' | 'due' | 'valid' | 'unknown'; months: number | null } {
  if (!latest) return { kind: 'none', months: null };
  const months = monthsUntil(latest.expiry_date, now);
  if (latest.is_valid === false) return { kind: 'expired', months };
  if (latest.expiry_date) {
    const msLeft = new Date(latest.expiry_date).getTime() - now.getTime();
    if (msLeft <= 0) return { kind: 'expired', months };
    if (msLeft <= dueWithinDays * 24 * 60 * 60 * 1000) return { kind: 'due', months };
    return { kind: 'valid', months };
  }
  return { kind: latest.is_valid === true ? 'valid' : 'unknown', months };
}
