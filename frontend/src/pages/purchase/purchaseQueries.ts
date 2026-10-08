// ============================================================================
// IMS 2.0 - Purchase section data via React Query
// ============================================================================
// Owner feedback on the Wave 1 URL split: switching sections "reloads" —
// every section re-fetched its data on mount. These shared hooks give all
// four section pages one cache (5-min staleTime from the app QueryClient):
// the FIRST visit fetches, every later switch renders instantly from cache
// and refreshes in the background. This is the TEMPLATE for every Wave 2
// module split — section pages must use shared query hooks, never their own
// useEffect fetch.

import { useQuery } from '@tanstack/react-query';
import { vendorsApi } from '../../services/api';
import { mapVendorToSupplier, mapPOtoPurchaseOrder } from './purchaseMappers';
import type { Supplier, PurchaseOrder } from './purchaseTypes';

export const vendorsQueryKey = ['purchase', 'vendors'] as const;
export const purchaseOrdersQueryKey = (storeId: string | undefined) =>
  ['purchase', 'orders', storeId ?? 'all'] as const;

export function useSuppliers() {
  return useQuery<Supplier[]>({
    queryKey: vendorsQueryKey,
    queryFn: async () => {
      const resp = await vendorsApi.getVendors({ is_active: true });
      return ((resp?.vendors ?? []) as unknown[]).map(mapVendorToSupplier);
    },
  });
}

/** One page of a Purchase list: the server answers its NEWEST rows first and
 *  `total` = every row matching the same shop scope and filters (review round
 *  2, #18). A screen holding fewer rows than that says "latest N of M" -- it
 *  never calls N a total. */
export interface PurchaseOrderPage {
  orders: PurchaseOrder[];
  total: number;
}

/** The count of every matching row from a list response, never less than the
 *  rows in hand (a server that sends no count = just those rows). */
export function matchingTotal(resp: unknown, shown: number): number {
  const total = (resp as { total?: unknown } | null | undefined)?.total;
  return typeof total === 'number' && Number.isFinite(total) && total > shown ? total : shown;
}

export function usePurchaseOrdersQuery(storeId: string | undefined) {
  return useQuery<PurchaseOrderPage>({
    queryKey: purchaseOrdersQueryKey(storeId),
    queryFn: async () => {
      const resp = await vendorsApi.getPurchaseOrders(storeId ? { store_id: storeId } : {});
      const orders = ((resp?.purchase_orders ?? []) as unknown[]).map(mapPOtoPurchaseOrder);
      return { orders, total: matchingTotal(resp, orders.length) };
    },
  });
}
