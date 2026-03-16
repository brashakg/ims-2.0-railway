// ============================================================================
// IMS 2.0 - POS Query Hooks (TanStack Query)
// ============================================================================
// Reusable hooks for all POS data operations:
// customers, products, prescriptions, orders, inventory

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  customerApi,
  productApi,
  prescriptionApi,
  orderApi,
  inventoryApi,
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
export function useCustomerSearch(query: string, storeId?: string) {
  return useQuery({
    queryKey: [...queryKeys.customers.search(query), storeId],
    queryFn: async () => {
      const response = await customerApi.getCustomers({ search: query, limit: 10, storeId });
      return response?.customers || response || [];
    },
    enabled: query.length >= 2,
    staleTime: 1000 * 30,
  });
}

/** Search customer by phone number */
export function useCustomerByPhone(phone: string) {
  return useQuery({
    queryKey: queryKeys.customers.byPhone(phone),
    queryFn: async () => {
      const response = await customerApi.searchByPhone(phone);
      return response;
    },
    enabled: phone.length >= 10,
    staleTime: 1000 * 60,
  });
}

/** Get customer detail */
export function useCustomer(customerId: string) {
  return useQuery({
    queryKey: queryKeys.customers.detail(customerId),
    queryFn: () => customerApi.getCustomer(customerId),
    enabled: !!customerId,
  });
}

/** Create customer mutation */
export function useCreateCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof customerApi.createCustomer>[0]) =>
      customerApi.createCustomer(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.customers.all });
    },
  });
}

// ============================================================================
// PRODUCT HOOKS
// ============================================================================

/** List products with filters */
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

/** Get product by ID */
export function useProduct(productId: string) {
  return useQuery({
    queryKey: queryKeys.products.detail(productId),
    queryFn: () => productApi.getProduct(productId),
    enabled: !!productId,
  });
}

/** Search products by barcode */
export function useProductByBarcode(barcode: string, storeId: string) {
  return useQuery({
    queryKey: queryKeys.products.byBarcode(barcode),
    queryFn: async () => {
      const response = await inventoryApi.searchByBarcode(barcode, storeId);
      return response;
    },
    enabled: barcode.length >= 3,
    staleTime: 1000 * 60,
  });
}

/** Search products by text */
export function useProductSearch(query: string, category?: string) {
  return useQuery({
    queryKey: ['products', 'search', query, category],
    queryFn: async () => {
      const response = await productApi.searchProducts(query, category);
      return response?.products || response || [];
    },
    enabled: query.length >= 2,
    staleTime: 1000 * 30,
  });
}

// ============================================================================
// PRESCRIPTION HOOKS
// ============================================================================

/** Get prescriptions for a patient */
export function usePrescriptions(patientId: string) {
  return useQuery({
    queryKey: queryKeys.prescriptions.byPatient(patientId),
    queryFn: async () => {
      const response = await prescriptionApi.getPrescriptions(patientId);
      return response?.prescriptions || response || [];
    },
    enabled: !!patientId,
    staleTime: 1000 * 60 * 2,
  });
}

/** Get single prescription */
export function usePrescription(prescriptionId: string) {
  return useQuery({
    queryKey: queryKeys.prescriptions.detail(prescriptionId),
    queryFn: () => prescriptionApi.getPrescription(prescriptionId),
    enabled: !!prescriptionId,
  });
}

/** Create prescription mutation */
export function useCreatePrescription() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof prescriptionApi.createPrescription>[0]) =>
      prescriptionApi.createPrescription(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.prescriptions.all });
    },
  });
}

// ============================================================================
// ORDER HOOKS
// ============================================================================

/** List orders with filters */
export function useOrders(params?: {
  storeId?: string;
  status?: string;
  date?: string;
  customerId?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: queryKeys.orders.list(params || {}),
    queryFn: async () => {
      const response = await orderApi.getOrders(params);
      return response?.orders || response || [];
    },
    staleTime: 1000 * 30,
  });
}

/** Get single order */
export function useOrder(orderId: string) {
  return useQuery({
    queryKey: queryKeys.orders.detail(orderId),
    queryFn: () => orderApi.getOrder(orderId),
    enabled: !!orderId,
  });
}

/** Create order mutation */
export function useCreateOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Parameters<typeof orderApi.createOrder>[0]) =>
      orderApi.createOrder(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.orders.all });
    },
  });
}

/** Add payment to order */
export function useAddPayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ orderId, payment }: {
      orderId: string;
      payment: Parameters<typeof orderApi.addPayment>[1];
    }) => orderApi.addPayment(orderId, payment),
    onSuccess: (_, { orderId }) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.orders.detail(orderId) });
    },
  });
}

/** Confirm order */
export function useConfirmOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (orderId: string) => orderApi.confirmOrder(orderId),
    onSuccess: (_, orderId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.orders.detail(orderId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.orders.all });
    },
  });
}

// ============================================================================
// INVENTORY HOOKS
// ============================================================================

/** Get stock for a store */
export function useStock(storeId: string, productId?: string) {
  return useQuery({
    queryKey: [...queryKeys.inventory.stock(storeId), productId],
    queryFn: () => inventoryApi.getStock(storeId, productId),
    enabled: !!storeId,
    staleTime: 1000 * 60,
  });
}

/** Get low stock alerts */
export function useLowStock(storeId: string) {
  return useQuery({
    queryKey: queryKeys.inventory.lowStock(storeId),
    queryFn: () => inventoryApi.getLowStock(storeId),
    enabled: !!storeId,
    staleTime: 1000 * 60 * 5,
  });
}

// ============================================================================
// STORE HOOKS
// ============================================================================

/** Get all stores */
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

/** Get store detail */
export function useStore(storeId: string) {
  return useQuery({
    queryKey: queryKeys.stores.detail(storeId),
    queryFn: () => storeApi.getStore(storeId),
    enabled: !!storeId,
  });
}
