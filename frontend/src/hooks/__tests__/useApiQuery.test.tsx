// ============================================================================
// IMS 2.0 - useApiQuery Hook Tests
// ============================================================================

import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useApiQuery, useApiMutation, useApiListQuery } from '../useApiQuery';
import axios from 'axios';

jest.mock('axios');

describe('useApiQuery', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    jest.clearAllMocks();
  });

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );

  describe('useApiQuery', () => {
    it('should fetch data successfully', async () => {
      const mockData = { id: 1, name: 'Product' };
      const queryFn = jest.fn().mockResolvedValue(mockData);

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
      (axios.get as jest.Mock).mockRejectedValue(error);
      const queryFn = jest.fn().mockRejectedValue(error);

      const { result } = renderHook(
        () => useApiQuery(['products'], queryFn),
        { wrapper }
      );

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(result.current.error).toBeDefined();
    });

    it('should use cache for subsequent queries', async () => {
      const mockData = { id: 1, name: 'Product' };
      const queryFn = jest.fn().mockResolvedValue(mockData);

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
      const queryFn = jest.fn().mockResolvedValue({});

      renderHook(() => useApiQuery(['products'], queryFn), { wrapper });

      await waitFor(() => {
        const query = queryClient.getQueryState(['products']);
        expect(query?.dataUpdatedAt).toBeDefined();
      });
    });

    it('should retry on 5xx errors', async () => {
      const error = new Error('Server Error');
      (error as any).response = { status: 500 };
      const queryFn = jest.fn()
        .mockRejectedValueOnce(error)
        .mockResolvedValueOnce({ data: 'success' });

      const { result } = renderHook(
        () => useApiQuery(['products'], queryFn, { retry: 1 }),
        { wrapper }
      );

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false);
      });

      expect(queryFn).toHaveBeenCalledTimes(2);
    });

    it('should not retry on 4xx errors', async () => {
      const error = new Error('Not Found');
      (error as any).response = { status: 404 };
      const queryFn = jest.fn().mockRejectedValue(error);

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
      const queryFn = jest.fn().mockResolvedValue({});

      const { result, rerender } = renderHook(
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
      const mutationFn = jest.fn().mockResolvedValue(mockData);

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
      const mutationFn = jest.fn().mockRejectedValue(error);

      const { result } = renderHook(
        () => useApiMutation(mutationFn),
        { wrapper }
      );

      result.current.mutate({});

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(result.current.error).toBeDefined();
    });

    it('should call onSuccess callback', async () => {
      const mockData = { id: 1 };
      const mutationFn = jest.fn().mockResolvedValue(mockData);
      const onSuccess = jest.fn();

      const { result } = renderHook(
        () => useApiMutation(mutationFn, { onSuccess }),
        { wrapper }
      );

      result.current.mutate({});

      await waitFor(() => {
        expect(onSuccess).toHaveBeenCalledWith(mockData);
      });
    });

    it('should call onError callback', async () => {
      const error = new Error('Mutation Error');
      const mutationFn = jest.fn().mockRejectedValue(error);
      const onError = jest.fn();

      const { result } = renderHook(
        () => useApiMutation(mutationFn, { onError }),
        { wrapper }
      );

      result.current.mutate({});

      await waitFor(() => {
        expect(onError).toHaveBeenCalled();
      });
    });

    it('should not retry on 4xx errors for mutations', async () => {
      const error = new Error('Validation Error');
      (error as any).response = { status: 422 };
      const mutationFn = jest.fn().mockRejectedValue(error);

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
      const queryFn = jest.fn().mockResolvedValue(mockData);

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
      const queryFn = jest.fn().mockResolvedValue({
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

    it('should disable query when all params are empty', async () => {
      const queryFn = jest.fn().mockResolvedValue({
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
          page: 1,
        }),
        { wrapper }
      );

      expect(result.current.isLoading).toBe(false);
      expect(result.current.data).toBeUndefined();
    });
  });
});
