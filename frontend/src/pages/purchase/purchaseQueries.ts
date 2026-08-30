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

export function usePurchaseOrdersQuery(storeId: string | undefined) {
  return useQuery<PurchaseOrder[]>({
    queryKey: purchaseOrdersQueryKey(storeId),
    queryFn: async () => {
      const resp = await vendorsApi.getPurchaseOrders(storeId ? { store_id: storeId } : {});
      return ((resp?.purchase_orders ?? []) as unknown[]).map(mapPOtoPurchaseOrder);
    },
  });
}
