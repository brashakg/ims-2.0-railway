// ============================================================================
// IMS 2.0 - React Query Hook Wrappers
// ============================================================================
// Standardized API query hooks with automatic caching and deduplication

import { useQuery, useMutation, useInfiniteQuery } from '@tanstack/react-query';
import type { AxiosError } from 'axios';
import type { UseQueryOptions } from '@tanstack/react-query';

/**
 * Standard API response type
 */
export interface ApiResponse<T> {
  success: boolean;
  data: T;
  message?: string;
  error?: string;
}

/**
 * Paginated API response
 */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

/**
 * useApiQuery - Fetch single resource with automatic caching
 *
 * @example
 * const { data: product } = useApiQuery<Product>(
 *   ['products', id],
 *   () => api.getProduct(id),
 *   { enabled: !!id }
 * );
 */
export function useApiQuery<T>(
  queryKey: (string | number | object | undefined)[],
  queryFn: () => Promise<T>,
  options?: Omit<UseQueryOptions<T, AxiosError>, 'queryKey' | 'queryFn'>
) {
  return useQuery<T, AxiosError>({
    queryKey,
    queryFn,
    staleTime: 1000 * 60 * 5, // 5 minutes
    gcTime: 1000 * 60 * 10, // 10 minutes (formerly cacheTime)
    retry: (failureCount, error) => {
      // Don't retry 4xx errors
      if (error.response?.status && error.response.status < 500) {
        return false;
      }
      // Retry 5xx errors up to 3 times
      return failureCount < 3;
    },
    ...options,
  });
}

/**
 * useApiMutation - Create/Update/Delete with automatic refetch
 *
 * @example
 * const { mutate: createProduct } = useApiMutation(
 *   () => api.createProduct(data),
 *   {
 *     onSuccess: () => queryClient.invalidateQueries({ queryKey: ['products'] })
 *   }
 * );
 */
export function useApiMutation<T, V = void>(
  mutationFn: (variables: V) => Promise<T>,
  options?: Omit<any, 'mutationFn'>
) {
  return useMutation<T, AxiosError, V>({
    mutationFn,
    retry: (failureCount, error) => {
      // Don't retry 4xx errors
      if (error.response?.status && error.response.status < 500) {
        return false;
      }
      // Retry 5xx errors up to 2 times
      return failureCount < 2;
    },
    ...options,
  });
}

/**
 * useApiInfiniteQuery - Paginated list with automatic deduplication
 *
 * @example
 * const { data, fetchNextPage, hasNextPage } = useApiInfiniteQuery<Product>(
 *   ['products'],
 *   ({ pageParam = 1 }) => api.getProducts({ page: pageParam, limit: 20 }),
 *   {
 *     getNextPageParam: (lastPage) => lastPage.page < lastPage.totalPages ? lastPage.page + 1 : undefined
 *   }
 * );
 */
export function useApiInfiniteQuery<T>(
  queryKey: (string | number | object | undefined)[],
  queryFn: (context: { pageParam: number }) => Promise<PaginatedResponse<T>>,
  options?: any
) {
  return useInfiniteQuery<PaginatedResponse<T>, AxiosError>({
    queryKey,
    queryFn: queryFn as any,
    initialPageParam: 1,
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 10,
    retry: (failureCount, error) => {
      if (error.response?.status && error.response.status < 500) {
        return false;
      }
      return failureCount < 3;
    },
    ...options,
  });
}

/**
 * useApiListQuery - List query with built-in filters/sorting/pagination
 *
 * @example
 * const { data: products } = useApiListQuery<Product>(
 *   ['products', { search, category }],
 *   (params) => api.getProducts(params),
 *   { search, category, page: 1, limit: 20 }
 * );
 */
export function useApiListQuery<T>(
  queryKey: (string | number | object | undefined)[],
  queryFn: (params: any) => Promise<PaginatedResponse<T>>,
  params: Record<string, any>,
  options?: Omit<any, 'queryKey' | 'queryFn'>
) {
  return useQuery<PaginatedResponse<T>, AxiosError>({
    queryKey: [...queryKey, params],
    queryFn: () => queryFn(params),
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 10,
    enabled: Object.values(params).some(v => v !== undefined && v !== null && v !== ''),
    retry: (failureCount, error) => {
      if (error.response?.status && error.response.status < 500) {
        return false;
      }
      return failureCount < 3;
    },
    ...options,
  });
}
