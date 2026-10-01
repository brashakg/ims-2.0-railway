// ============================================================================
// IMS 2.0 - POS Query Hooks (TanStack Query)
// ============================================================================
// Reusable hooks for all POS data operations:
// customers, products, prescriptions, orders, inventory

import { useQuery } from '@tanstack/react-query';
import {
  inventoryApi,
  productApi,
  storeApi,
} from '../services/api';
import { mapCategory } from '../components/pos/submitOrder';
import { productIdOf } from '../components/pos/productIntake';
import { usePOSStore } from '../stores/posStore';

// ============================================================================
// Query Key Factories (for cache invalidation)
// ============================================================================

export const queryKeys = {
  customers: {
    all: ['customers'] as const,
    search: (query: string) => ['customers', 'search', query] as const,
    detail: (id: string) => ['customers', id] as const,
    byPhone: (phone: string) => ['customers', 'phone', phone] as const,
  },
  products: {
    all: ['products'] as const,
    list: (params: Record<string, unknown>) => ['products', 'list', params] as const,
    detail: (id: string) => ['products', id] as const,
    byBarcode: (barcode: string) => ['products', 'barcode', barcode] as const,
  },
  prescriptions: {
    all: ['prescriptions'] as const,
    byPatient: (patientId: string) => ['prescriptions', 'patient', patientId] as const,
    detail: (id: string) => ['prescriptions', id] as const,
  },
  orders: {
    all: ['orders'] as const,
    list: (params: Record<string, unknown>) => ['orders', 'list', params] as const,
    detail: (id: string) => ['orders', id] as const,
  },
  inventory: {
    stock: (storeId: string) => ['inventory', 'stock', storeId] as const,
    lowStock: (storeId: string) => ['inventory', 'lowStock', storeId] as const,
  },
  stores: {
    all: ['stores'] as const,
    detail: (id: string) => ['stores', id] as const,
  },
};

// ============================================================================
// CUSTOMER HOOKS
// ============================================================================

/** Search customers by name or phone */

export function useProducts(params?: { category?: string; brand?: string; search?: string; store_id?: string }) {
  return useQuery({
    queryKey: queryKeys.products.list(params || {}),
    queryFn: async () => {
      const response = await productApi.getProducts(params);
      return response?.products || response || [];
    },
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

/** F46: this shop's sellable count for a set of till rows (tiles or cart
 *  lines). `sellable[id]` is the oversell guard's OWN number (GET
 *  /inventory/sellable asks _assert_serialized_stock_available): a number, or
 *  null when the guard does not gate that row (not unit-tracked here, lens,
 *  service). `canonical[id]` is the id the guard adds that line up under.
 *
 *  Only THIS screen's shop (`storeId`) is ever shown. The server answers for
 *  the store in the sign-in token -- the one Complete sale checks -- and names
 *  it; an answer naming any other shop (or none) is thrown away, never shown,
 *  and asked again: 1 s, 2 s, 4 s later, then every 30 s. That covers a store
 *  switch, which moves the screen before the token (AuthContext
 *  setActiveStore). A screen whose token never moves (a switch the server
 *  refused, e.g. an AREA_MANAGER at a shop outside their list) shows no
 *  figures and no warnings rather than another shop's.
 *
 *  The guard at Complete sale stays the authority; this is kept as fresh as a
 *  read can be. Never "fresh" (the App default is 5 minutes), so a remount or
 *  a refocus re-reads; unobserved counts are dropped at once, so a tile coming
 *  back never shows an old figure; the key carries this till's last order id
 *  (submitPosOrder sets it on every sale), so a sale here re-reads at once.
 *  ponytail: stock another till or a receipt moves shows within 30 s (or on
 *  refocus); push it from the server if that ever proves too slow. */
export function useSellableStock(storeId: string | undefined, rows: any[]) {
  const lastSale = usePOSStore((s) => s.order_id);
  const typeOf = new Map<string, string>();
  for (const r of rows || []) {
    const id = productIdOf(r || {});
    if (id && !typeOf.has(id)) typeOf.set(id, mapCategory(r.category || ''));
  }
  const ids = [...typeOf.keys()].sort();
  return useQuery({
    queryKey: ['pos', 'sellable', storeId, lastSale, ids],
    queryFn: async () => {
      const answer = await inventoryApi.getSellable(ids, ids.map((id) => typeOf.get(id)!));
      if (answer.store_id !== storeId) {
        throw new Error(`Stock figures came back for ${answer.store_id}, not ${storeId}`);
      }
      return answer;
    },
    enabled: !!storeId && ids.length > 0,
    staleTime: 0,
    gcTime: 0,
    retry: 3,
    refetchInterval: 30_000,
    // While a changed cart re-reads, keep the last counts FROM THIS SHOP on
    // screen so a warning never blinks off and back on; never another shop's.
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[2] === storeId ? prev : undefined),
  });
}

export function useStores() {
  return useQuery({
    queryKey: queryKeys.stores.all,
    queryFn: async () => {
      const response = await storeApi.getStores();
      return response?.stores || response || [];
    },
    staleTime: 1000 * 60 * 30, // 30 minutes (stores rarely change)
  });
}
