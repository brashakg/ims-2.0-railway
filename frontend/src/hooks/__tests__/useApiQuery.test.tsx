// ============================================================================
// IMS 2.0 - useApiQuery Hook Tests
// ============================================================================

import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { vi } from 'vitest';
import { useApiQuery, useApiMutation, useApiListQuery } from '../useApiQuery';
import axios from 'axios';

// axios is mocked so its methods are vi.fn() spies (the hooks under test drive
// behaviour through the supplied queryFn; a couple of error-path tests poke
// axios.get directly to assert it stays a controllable spy).
vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('useApiQuery', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    vi.clearAllMocks();
  });

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );

  describe('useApiQuery', () => {
    it('should fetch data successfully', async () => {
      const mockData = { id: 1, name: 'Product' };
      const queryFn = vi.fn().mockResolvedValue(mockData);

      const { result } = renderHook(
        () => useApiQuery(['products', 1], queryFn),
        { wrapper }
      );

      expect(result.current.isLoading).toBe(true);

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false);
      });

      expect(result.current.data).toEqual(mockData);
      expect(queryFn).toHaveBeenCalledTimes(1);
    });

    it('should handle errors gracefully', async () => {
      const error = new Error('API Error');
      (axios.get as ReturnType<typeof vi.fn>).mockRejectedValue(error);
      const queryFn = vi.fn().mockRejectedValue(error);

      // The hook's `...options` spread lets a caller override the built-in retry
      // policy. Disable retry so the rejection surfaces immediately as isError
      // (otherwise React Query's exponential backoff outruns waitFor).
      const { result } = renderHook(
        () => useApiQuery(['products'], queryFn, { retry: false }),
        { wrapper }
      );

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(result.current.error).toBeDefined();
      expect(queryFn).toHaveBeenCalled();
    });

    it('should use cache for subsequent queries', async () => {
      const mockData = { id: 1, name: 'Product' };
      const queryFn = vi.fn().mockResolvedValue(mockData);

      const { result: result1 } = renderHook(
        () => useApiQuery(['products', 1], queryFn),
        { wrapper }
      );

      await waitFor(() => {
        expect(result1.current.isLoading).toBe(false);
      });

      const { result: result2 } = renderHook(
        () => useApiQuery(['products', 1], queryFn),
        { wrapper }
      );

      // Should return cached data without calling queryFn again
      expect(result2.current.data).toEqual(mockData);
      expect(queryFn).toHaveBeenCalledTimes(1);
    });

    it('should have correct default stale time', async () => {
      const queryFn = vi.fn().mockResolvedValue({});

      renderHook(() => useApiQuery(['products'], queryFn), { wrapper });

      await waitFor(() => {
        const query = queryClient.getQueryState(['products']);
        expect(query?.dataUpdatedAt).toBeDefined();
      });
    });

    it('should retry on 5xx errors', async () => {
      const error = new Error('Server Error');
      (error as any).response = { status: 500 };
      const queryFn = vi.fn()
        .mockRejectedValueOnce(error)
        .mockResolvedValueOnce({ data: 'success' });

      // retry: 1 -> one retry after the first failure; retryDelay: 0 makes the
      // retry fire immediately so the (5xx) success on attempt 2 lands inside
      // waitFor instead of behind React Query's exponential backoff.
      const { result } = renderHook(
        () => useApiQuery(['products'], queryFn, { retry: 1, retryDelay: 0 }),
        { wrapper }
      );

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false);
      });

      expect(queryFn).toHaveBeenCalledTimes(2);
      expect(result.current.isError).toBe(false);
    });

    it('should not retry on 4xx errors', async () => {
      const error = new Error('Not Found');
      (error as any).response = { status: 404 };
      const queryFn = vi.fn().mockRejectedValue(error);

      const { result } = renderHook(
        () => useApiQuery(['products'], queryFn),
        { wrapper }
      );

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(queryFn).toHaveBeenCalledTimes(1);
    });

    it('should support enabled option', async () => {
      const queryFn = vi.fn().mockResolvedValue({});

      const { result: _result, rerender } = renderHook(
        ({ enabled }: { enabled: boolean }) =>
          useApiQuery(['products'], queryFn, { enabled }),
        { wrapper, initialProps: { enabled: false } }
      );

      expect(queryFn).not.toHaveBeenCalled();

      rerender({ enabled: true });

      await waitFor(() => {
        expect(queryFn).toHaveBeenCalled();
      });
    });
  });

  describe('useApiMutation', () => {
    it('should mutate data successfully', async () => {
      const mockData = { id: 1, name: 'Product' };
      const mutationFn = vi.fn().mockResolvedValue(mockData);

      const { result } = renderHook(
        () => useApiMutation(mutationFn),
        { wrapper }
      );

      result.current.mutate({});

      await waitFor(() => {
        expect(result.current.isPending).toBe(false);
      });

      expect(result.current.data).toEqual(mockData);
    });

    it('should handle mutation errors', async () => {
      const error = new Error('Mutation Error');
      const mutationFn = vi.fn().mockRejectedValue(error);

      // retry: false (via the hook's options passthrough) so the rejection
      // surfaces immediately instead of behind the built-in retry backoff.
      const { result } = renderHook(
        () => useApiMutation(mutationFn, { retry: false }),
        { wrapper }
      );

      result.current.mutate({});

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(result.current.error).toBeDefined();
    });

    it('should call onSuccess callback with the mutation result', async () => {
      const mockData = { id: 1 };
      const variables = { name: 'New Product' };
      const mutationFn = vi.fn().mockResolvedValue(mockData);
      const onSuccess = vi.fn();

      const { result } = renderHook(
        () => useApiMutation(mutationFn, { onSuccess }),
        { wrapper }
      );

      result.current.mutate(variables);

      await waitFor(() => {
        expect(onSuccess).toHaveBeenCalled();
      });
      // React Query v5 invokes onSuccess as (data, variables, context).
      expect(onSuccess.mock.calls[0][0]).toEqual(mockData);
      expect(onSuccess.mock.calls[0][1]).toEqual(variables);
      // mutationFn receives the variables as its first arg (v5 also passes a
      // context object as a 2nd arg, so assert on calls[0][0] not the whole call).
      expect(mutationFn.mock.calls[0][0]).toEqual(variables);
    });

    it('should call onError callback when the mutation rejects', async () => {
      const error = new Error('Mutation Error');
      const mutationFn = vi.fn().mockRejectedValue(error);
      const onError = vi.fn();

      const { result } = renderHook(
        () => useApiMutation(mutationFn, { onError, retry: false }),
        { wrapper }
      );

      result.current.mutate({});

      await waitFor(() => {
        expect(onError).toHaveBeenCalled();
      });
      // React Query v5 invokes onError as (error, variables, context).
      expect(onError.mock.calls[0][0]).toBe(error);
    });

    it('should not retry on 4xx errors for mutations', async () => {
      const error = new Error('Validation Error');
      (error as any).response = { status: 422 };
      const mutationFn = vi.fn().mockRejectedValue(error);

      const { result } = renderHook(
        () => useApiMutation(mutationFn),
        { wrapper }
      );

      result.current.mutate({});

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(mutationFn).toHaveBeenCalledTimes(1);
    });
  });

  describe('useApiListQuery', () => {
    it('should fetch paginated list', async () => {
      const mockData = {
        items: [{ id: 1 }],
        total: 1,
        page: 1,
        pageSize: 20,
        totalPages: 1,
      };
      const queryFn = vi.fn().mockResolvedValue(mockData);

      const { result } = renderHook(
        () => useApiListQuery(['products'], queryFn, { search: 'test', page: 1 }),
        { wrapper }
      );

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false);
      });

      expect(result.current.data).toEqual(mockData);
    });

    it('should include params in query key', async () => {
      const queryFn = vi.fn().mockResolvedValue({
        items: [],
        total: 0,
        page: 1,
        pageSize: 20,
        totalPages: 0,
      });

      renderHook(
        () => useApiListQuery(
          ['products'],
          queryFn,
          { search: 'test', category: 'frames', page: 1 }
        ),
        { wrapper }
      );

      await waitFor(() => {
        expect(queryFn).toHaveBeenCalledWith(
          expect.objectContaining({ search: 'test', category: 'frames' })
        );
      });
    });

    it('should disable query when all params are empty/null/undefined', async () => {
      // Real enabled rule: Object.values(params).some(v => v !== undefined &&
      // v !== null && v !== ''). With every value empty, the query is disabled,
      // queryFn is never invoked, and data stays undefined.
      const queryFn = vi.fn().mockResolvedValue({
        items: [],
        total: 0,
        page: 1,
        pageSize: 20,
        totalPages: 0,
      });

      const { result } = renderHook(
        () => useApiListQuery(['products'], queryFn, {
          search: '',
          category: null,
          status: undefined,
        }),
        { wrapper }
      );

      expect(result.current.isLoading).toBe(false);
      expect(result.current.data).toBeUndefined();
      expect(queryFn).not.toHaveBeenCalled();
    });

    it('should enable the query when at least one param is non-empty', async () => {
      // The mirror of the disable case: a single non-empty value (here page: 1)
      // is enough to flip `enabled` true and run the query.
      const payload = {
        items: [{ id: 7 }],
        total: 1,
        page: 1,
        pageSize: 20,
        totalPages: 1,
      };
      const queryFn = vi.fn().mockResolvedValue(payload);

      const { result } = renderHook(
        () => useApiListQuery(['products'], queryFn, {
          search: '',
          category: null,
          page: 1,
        }),
        { wrapper }
      );

      await waitFor(() => {
        expect(result.current.data).toEqual(payload);
      });
      expect(queryFn).toHaveBeenCalledTimes(1);
    });
  });
});
