// ============================================================================
// IMS 2.0 - useAsyncOperation Hook
// ============================================================================
// Consolidates 10+ loading/error state management patterns across components

import { useState, useCallback, useRef } from 'react';

export interface AsyncOperationState<T = void> {
  /** Current loading state */
  isLoading: boolean;
  /** Current error message, if any */
  error: string | null;
  /** Operation was successful */
  isSuccess: boolean;
  /** Response data from successful operation */
  data: T | null;
}

export interface UseAsyncOperationOptions {
  /** Auto-clear error after N milliseconds (0 = never auto-clear) */
  errorTimeout?: number;
  /** Auto-clear success message after N milliseconds (0 = never auto-clear) */
  successTimeout?: number;
  /** Callback fired when operation completes successfully */
  onSuccess?: () => void;
  /** Callback fired when operation fails */
  onError?: (error: Error | string) => void;
}

/**
 * useAsyncOperation Hook
 * Manages async operation state (loading, error, success) and execution
 *
 * @example
 * const { isLoading, error, execute } = useAsyncOperation<User>(
 *   { errorTimeout: 5000, onSuccess: () => refreshUsers() }
 * );
 *
 * const handleCreate = () => {
 *   execute(async () => {
 *     const user = await api.createUser(formData);
 *     return user;
 *   });
 * };
 *
 * return (
 *   <div>
 *     {error && <ErrorAlert message={error} />}
 *     <button onClick={handleCreate} disabled={isLoading}>
 *       {isLoading ? 'Creating...' : 'Create'}
 *     </button>
 *   </div>
 * );
 */
export function useAsyncOperation<T = void>(options: UseAsyncOperationOptions = {}) {
  const { errorTimeout = 5000, successTimeout = 3000, onSuccess, onError } = options;

  const [state, setState] = useState<AsyncOperationState<T>>({
    isLoading: false,
    error: null,
    isSuccess: false,
    data: null,
  });

  // Use refs to track timeouts for cleanup
  const errorTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const successTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /**
   * Clear error message
   */
  const clearError = useCallback(() => {
    setState(prev => ({ ...prev, error: null }));
  }, []);

  /**
   * Clear success message
   */
  const clearSuccess = useCallback(() => {
    setState(prev => ({ ...prev, isSuccess: false }));
  }, []);

  /**
   * Reset entire state
   */
  const reset = useCallback(() => {
    if (errorTimeoutRef.current !== null) clearTimeout(errorTimeoutRef.current);
    if (successTimeoutRef.current !== null) clearTimeout(successTimeoutRef.current);
    setState({
      isLoading: false,
      error: null,
      isSuccess: false,
      data: null,
    });
  }, []);

  /**
   * Execute async operation
   */
  const execute = useCallback(
    async (operation: () => Promise<T>, onErrorCb?: (error: Error | string) => void) => {
      // Clear previous state
      if (errorTimeoutRef.current !== null) clearTimeout(errorTimeoutRef.current);
      if (successTimeoutRef.current !== null) clearTimeout(successTimeoutRef.current);

      setState({ isLoading: true, error: null, isSuccess: false, data: null });

      try {
        const result = await operation();
        setState({
          isLoading: false,
          error: null,
          isSuccess: true,
          data: result,
        });

        // Auto-clear success message
        if (successTimeout > 0) {
          successTimeoutRef.current = setTimeout(() => {
            setState(prev => ({ ...prev, isSuccess: false }));
          }, successTimeout);
        }

        onSuccess?.();
        return result;
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : String(err);
        setState({
          isLoading: false,
          error: errorMessage,
          isSuccess: false,
          data: null,
        });

        // Auto-clear error message
        if (errorTimeout > 0) {
          errorTimeoutRef.current = setTimeout(() => {
            setState(prev => ({ ...prev, error: null }));
          }, errorTimeout);
        }

        onError?.(err instanceof Error ? err : new Error(errorMessage));
        onErrorCb?.(err instanceof Error ? err : new Error(errorMessage));
        throw err;
      }
    },
    [errorTimeout, successTimeout, onSuccess, onError]
  );

  return {
    ...state,
    execute,
    clearError,
    clearSuccess,
    reset,
  };
}

/**
 * useAsyncList Hook
 * Manages loading/error/data for list operations (fetch, pagination, filtering)
 *
 * @example
 * const { items, isLoading, error, fetchMore } = useAsyncList<Product>(
 *   (page) => api.getProducts({ page }),
 *   { pageSize: 20, autoLoad: true }
 * );
 */
export interface UseAsyncListOptions {
  /** Items per page/batch */
  pageSize?: number;
  /** Auto-load on mount */
  autoLoad?: boolean;
  /** Error auto-clear timeout (ms) */
  errorTimeout?: number;
}

export function useAsyncList<T>(
  fetchFn: (page: number) => Promise<T[]>,
  options: UseAsyncListOptions = {}
) {
  const { pageSize = 20, errorTimeout = 5000 } = options;

  const [items, setItems] = useState<T[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);

  const errorTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetch = useCallback(
    async (page: number = 1, append: boolean = false) => {
      if (isLoading) return;

      if (errorTimeoutRef.current !== null) clearTimeout(errorTimeoutRef.current);

      setIsLoading(true);
      setError(null);

      try {
        const results = await fetchFn(page);
        const newItems = append ? [...items, ...results] : results;

        setItems(newItems);
        setCurrentPage(page);
        setHasMore(results.length === pageSize);
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : String(err);
        setError(errorMessage);

        if (errorTimeout > 0) {
          errorTimeoutRef.current = setTimeout(() => setError(null), errorTimeout);
        }
      } finally {
        setIsLoading(false);
      }
    },
    [fetchFn, pageSize, errorTimeout, items, isLoading]
  );

  const fetchMore = useCallback(() => {
    if (hasMore && !isLoading) {
      fetch(currentPage + 1, true);
    }
  }, [fetch, currentPage, hasMore, isLoading]);

  const refresh = useCallback(() => {
    setCurrentPage(1);
    fetch(1, false);
  }, [fetch]);

  const clearError = useCallback(() => {
    if (errorTimeoutRef.current) clearTimeout(errorTimeoutRef.current);
    setError(null);
  }, []);

  return {
    items,
    isLoading,
    error,
    currentPage,
    hasMore,
    fetch,
    fetchMore,
    refresh,
    clearError,
    setItems,
  };
}

/**
 * useAsyncMutation Hook
 * Manages state for data mutations (create, update, delete)
 *
 * @example
 * const { mutate, isLoading, error } = useAsyncMutation<Product>(
 *   (data) => api.createProduct(data)
 * );
 *
 * const handleSubmit = async (formData) => {
 *   const product = await mutate(formData);
 *   if (product) showSuccess('Product created');
 * };
 */
export interface UseAsyncMutationOptions<T, R = T> {
  /** Callback before mutation starts */
  onMutate?: (variables: T) => Promise<void>;
  /** Callback on success */
  onSuccess?: (data: R, variables: T) => void | Promise<void>;
  /** Callback on error */
  onError?: (error: Error, variables: T) => void | Promise<void>;
  /** Error auto-clear timeout (ms) */
  errorTimeout?: number;
}

export function useAsyncMutation<T, R = T>(
  mutateFn: (variables: T) => Promise<R>,
  options: UseAsyncMutationOptions<T, R> = {}
) {
  const { onMutate, onSuccess, onError, errorTimeout = 5000 } = options;

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<R | null>(null);

  const errorTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mutate = useCallback(
    async (variables: T) => {
      if (errorTimeoutRef.current !== null) clearTimeout(errorTimeoutRef.current);

      setIsLoading(true);
      setError(null);

      try {
        await onMutate?.(variables);

        const result = await mutateFn(variables);
        setData(result);

        await onSuccess?.(result, variables);

        return result;
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : String(err);
        setError(errorMessage);

        if (errorTimeout > 0) {
          errorTimeoutRef.current = setTimeout(() => setError(null), errorTimeout);
        }

        await onError?.(err instanceof Error ? err : new Error(errorMessage), variables);
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [mutateFn, onMutate, onSuccess, onError, errorTimeout]
  );

  const reset = useCallback(() => {
    if (errorTimeoutRef.current !== null) clearTimeout(errorTimeoutRef.current);
    setIsLoading(false);
    setError(null);
    setData(null);
  }, []);

  const clearError = useCallback(() => {
    if (errorTimeoutRef.current !== null) clearTimeout(errorTimeoutRef.current);
    setError(null);
  }, []);

  return {
    mutate,
    isLoading,
    error,
    data,
    reset,
    clearError,
  };
}

/**
 * useAsyncDebounced Hook
 * Debounced async operation (search, autocomplete, live validation)
 *
 * @example
 * const { execute, isLoading, error, data } = useAsyncDebounced<Product[]>(
 *   async (query) => api.searchProducts(query),
 *   300
 * );
 *
 * <input onChange={(e) => execute(e.target.value)} />
 */
export function useAsyncDebounced<T>(
  asyncFn: (input: string) => Promise<T>,
  delayMs: number = 300,
  options: UseAsyncOperationOptions = {}
) {
  const [input, setInput] = useState('');
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const errorTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const execute = useCallback(
    (value: string) => {
      setInput(value);

      if (debounceRef.current !== null) clearTimeout(debounceRef.current);
      if (value.length === 0) {
        setData(null);
        setError(null);
        return;
      }

      setIsLoading(true);

      debounceRef.current = setTimeout(async () => {
        try {
          const result = await asyncFn(value);
          setData(result);
          setError(null);
          options.onSuccess?.();
        } catch (err) {
          const errorMessage = err instanceof Error ? err.message : String(err);
          setError(errorMessage);

          if (options.errorTimeout !== undefined && options.errorTimeout > 0) {
            errorTimeoutRef.current = setTimeout(() => setError(null), options.errorTimeout);
          }

          options.onError?.(err instanceof Error ? err : new Error(errorMessage));
        } finally {
          setIsLoading(false);
        }
      }, delayMs);
    },
    [asyncFn, delayMs, options]
  );

  const reset = useCallback(() => {
    if (debounceRef.current !== null) clearTimeout(debounceRef.current);
    if (errorTimeoutRef.current !== null) clearTimeout(errorTimeoutRef.current);
    setInput('');
    setData(null);
    setError(null);
    setIsLoading(false);
  }, []);

  return {
    input,
    execute,
    data,
    isLoading,
    error,
    reset,
  };
}
