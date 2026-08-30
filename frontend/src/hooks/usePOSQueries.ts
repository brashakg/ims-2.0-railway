// ============================================================================
// IMS 2.0 - POS Query Hooks (TanStack Query)
// ============================================================================
// Reusable hooks for all POS data operations:
// customers, products, prescriptions, orders, inventory

import { useQuery } from '@tanstack/react-query';
import {
  productApi,
  storeApi,
} from '../services/api';

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
