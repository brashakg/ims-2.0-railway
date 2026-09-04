// ============================================================================
// IMS 2.0 - Clinical module data via React Query
// ============================================================================
// Wave 2 split of ClinicalPage. Copies the Wave 1 template (reports/tasks):
// ONE cache shared by the layout's stat strip / tab counts and the section
// pages (5-min staleTime from the app QueryClient), so switching sections
// renders from cache instead of refetching, and completing an eye test on the
// queue page updates the "Completed today" count in the layout via a single
// invalidateQueries({ queryKey: CLINICAL_QK }).
//
// Fail-soft contract preserved from the old page: a failed fetch renders as an
// EMPTY list (the old loadData `.catch(() => ({ queue: [] }))`), and a failed
// store-identity lookup as null - printing is then unavailable but the queue
// still works.

import { useQuery } from '@tanstack/react-query';
import { clinicalApi } from '../../services/api';
import {
  resolveStoreIdentity,
  type StoreIdentity,
} from '../../components/print/storeIdentity';
import type { EntityLike } from '../../components/print/legalPrimitives';

/** Root query key - invalidate this to refresh every clinical section. */
export const CLINICAL_QK = ['clinical'] as const;

export type QueueStatus = 'WAITING' | 'IN_PROGRESS' | 'COMPLETED';

export interface QueueItem {
  id: string;
  tokenNumber: string;
  patientName: string;
  customerPhone: string;
  age?: number;
  reason?: string;
  status: QueueStatus;
  waitTime: number;
  createdAt: string;
  testId?: string;
  /** Linked customer id when the patient is a dependent (e.g. a child)
   *  whose bills are paid against a different customer record. Empty
   *  when the patient and the customer are the same person. */
  customerId?: string;
  /** Linked customer name — only set when patientName !== customerName,
   *  to make the patient-vs-customer relationship explicit on the card. */
  customerName?: string;
}

export interface CompletedTest {
  id: string;
  patientName: string;
  customerPhone: string;
  customerId?: string;
  completedAt: string;
  rightEye: { sphere: number | null; cylinder: number | null; axis: number | null };
  leftEye: { sphere: number | null; cylinder: number | null; axis: number | null };
}

/** Today's optometrist queue for the active store. */
export function useClinicalQueue(storeId: string | undefined) {
  return useQuery({
    queryKey: [...CLINICAL_QK, 'queue', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async (): Promise<QueueItem[]> => {
      const data = await clinicalApi
        .getQueue(storeId as string)
        .catch(() => ({ queue: [] }));
      const items = (data as { queue?: unknown })?.queue ?? data ?? [];
      return Array.isArray(items) ? (items as QueueItem[]) : [];
    },
  });
}

/** Eye tests completed TODAY at the active store. */
export function useTodayTests(storeId: string | undefined) {
  return useQuery({
    queryKey: [...CLINICAL_QK, 'today-tests', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async (): Promise<CompletedTest[]> => {
      const data = await clinicalApi
        .getTodayTests(storeId as string)
        .catch(() => ({ tests: [] }));
      const tests = (data as { tests?: unknown })?.tests ?? data ?? [];
      return Array.isArray(tests) ? (tests as CompletedTest[]) : [];
    },
  });
}

// --- issuing store identity (token + Rx card printing) ----------------------

/** The store fields the clinical print surfaces read (token + Rx card).
 *  NEVER defaulted to a fixed brand name - a WizOpt store prints WizOpt. */
export interface ClinicalStoreInfo {
  storeName: string;
  storeCode: string;
  brand: string;
  address: string;
  city: string;
  state: string;
  stateCode: string;
  pincode: string;
  phone: string;
  gstin: string;
}

export function storeInfoFrom(identity: StoreIdentity | null | undefined): ClinicalStoreInfo | null {
  if (!identity) return null;
  const sv = identity.store;
  return {
    storeName: sv.storeName || sv.storeCode || '',
    storeCode: sv.storeCode || '',
    brand: sv.brand || '',
    address: sv.address || '',
    city: sv.city || '',
    state: sv.state || '',
    stateCode: sv.stateCode || '',
    pincode: sv.pincode || '',
    phone: (sv as { phone?: string }).phone || '',
    gstin: sv.gstin || '',
  };
}

/**
 * Issuing store + legal entity, for printing tokens / Rx cards. Identity
 * changes only when the owner edits store setup, so it is cached longer than
 * the operational lists (mirrors the 30-min stores-list convention).
 */
export function useClinicalStoreIdentity(storeId: string | undefined): {
  storeInfo: ClinicalStoreInfo | null;
  storeEntity: EntityLike | null;
} {
  const { data } = useQuery({
    queryKey: ['store-identity', storeId ?? 'none'] as const,
    enabled: !!storeId,
    staleTime: 30 * 60 * 1000,
    queryFn: (): Promise<StoreIdentity | null> =>
      resolveStoreIdentity(storeId as string).catch(() => null),
  });
  return {
    storeInfo: storeInfoFrom(data),
    storeEntity: data?.entity ?? null,
  };
}
