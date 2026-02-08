// ============================================================================
// IMS 2.0 - Testing Utilities & Helpers
// ============================================================================
// Common test helpers, render functions, and assertion utilities

import { ReactElement } from 'react';
import { render, RenderOptions, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthContext, AuthContextType } from '../context/AuthContext';

/**
 * Custom render function with providers
 */
export function renderWithProviders(
  ui: ReactElement,
  {
    authState = {
      user: null,
      token: null,
      loading: false,
      error: null,
      isAuthenticated: false,
    },
    queryClient,
    ...renderOptions
  }: {
    authState?: Partial<AuthContextType>;
    queryClient?: QueryClient;
  } & Omit<RenderOptions, 'wrapper'> = {}
) {
  const testQueryClient = queryClient || new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const mockDispatch = jest.fn();
  const defaultAuthState: AuthContextType = {
    user: null,
    token: null,
    loading: false,
    error: null,
    isAuthenticated: false,
    dispatch: mockDispatch,
    ...authState,
  };

  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={testQueryClient}>
        <AuthContext.Provider value={defaultAuthState}>
          {children}
        </AuthContext.Provider>
      </QueryClientProvider>
    );
  }

  return {
    ...render(ui, { wrapper: Wrapper, ...renderOptions }),
    queryClient: testQueryClient,
    mockDispatch,
  };
}

/**
 * Async wait utilities
 */
export async function waitForLoadingToFinish() {
  const { queryByRole } = render(<div role="status" aria-label="Loading..." />);
  await waitFor(() => {
    expect(queryByRole('status')).not.toBeInTheDocument();
  });
}

export async function waitForElement(
  callback: () => HTMLElement | null,
  timeout = 1000
) {
  return waitFor(() => {
    const element = callback();
    expect(element).toBeInTheDocument();
    return element;
  }, { timeout });
}

/**
 * Mock API response helper
 */
export function createMockApiResponse<T>(
  data: T,
  status = 200
) {
  return Promise.resolve({
    data,
    status,
    statusText: 'OK',
    headers: {},
    config: {} as any,
  });
}

export function createMockApiError(
  message = 'API Error',
  status = 500,
  data: any = {}
) {
  const error = new Error(message) as any;
  error.response = {
    status,
    statusText: 'Error',
    data,
    headers: {},
    config: {} as any,
  };
  return Promise.reject(error);
}

/**
 * Form input helpers for testing
 */
export function fillInputField(
  container: HTMLElement,
  label: string,
  value: string
) {
  const input = container.querySelector(
    `input[aria-label="${label}"]`
  ) as HTMLInputElement;
  if (!input) throw new Error(`Input with label "${label}" not found`);

  input.value = value;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return input;
}

export function fillSelectField(
  container: HTMLElement,
  label: string,
  value: string
) {
  const select = container.querySelector(
    `select[aria-label="${label}"]`
  ) as HTMLSelectElement;
  if (!select) throw new Error(`Select with label "${label}" not found`);

  select.value = value;
  select.dispatchEvent(new Event('change', { bubbles: true }));
  return select;
}

export function submitForm(container: HTMLElement) {
  const form = container.querySelector('form');
  if (!form) throw new Error('Form not found');

  const submitButton = form.querySelector(
    'button[type="submit"]'
  ) as HTMLButtonElement;
  if (!submitButton) throw new Error('Submit button not found');

  submitButton.click();
}

/**
 * Component visibility helpers
 */
export function expectVisible(element: HTMLElement | null) {
  expect(element).toBeInTheDocument();
  expect(element).toBeVisible();
}

export function expectHidden(element: HTMLElement | null) {
  if (element) {
    expect(element).not.toBeVisible();
  }
}

export function expectExists(element: HTMLElement | null) {
  expect(element).toBeInTheDocument();
}

export function expectNotExists(element: HTMLElement | null) {
  expect(element).not.toBeInTheDocument();
}

/**
 * Event simulation helpers
 */
export function simulateKeyPress(
  element: HTMLElement,
  key: string,
  modifiers: { ctrl?: boolean; shift?: boolean; alt?: boolean } = {}
) {
  const event = new KeyboardEvent('keydown', {
    key,
    code: key,
    ctrlKey: modifiers.ctrl || false,
    shiftKey: modifiers.shift || false,
    altKey: modifiers.alt || false,
    bubbles: true,
  });
  element.dispatchEvent(event);
}

export function simulateChange(
  element: HTMLInputElement | HTMLSelectElement,
  value: string
) {
  element.value = value;
  element.dispatchEvent(new Event('change', { bubbles: true }));
  element.dispatchEvent(new Event('input', { bubbles: true }));
}

export function simulateClick(element: HTMLElement) {
  element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
}

/**
 * Wait for API calls helper
 */
export async function waitForApiCall(
  mockFn: jest.Mock,
  timeout = 1000
) {
  return waitFor(() => {
    expect(mockFn).toHaveBeenCalled();
  }, { timeout });
}

/**
 * Local storage mock helper
 */
export function mockLocalStorage() {
  const store: Record<string, string> = {};

  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = value.toString();
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      Object.keys(store).forEach(key => delete store[key]);
    },
  };
}

/**
 * IndexedDB mock helper
 */
export function mockIndexedDB() {
  const databases: Record<string, Record<string, any>> = {};

  return {
    open: jest.fn().mockImplementation((dbName: string) => ({
      onsuccess: null as any,
      onerror: null as any,
      onupgradeneeded: null as any,
      result: {
        objectStoreNames: {
          contains: (name: string) => !!databases[dbName]?.[name],
        },
        createObjectStore: (name: string) => {
          if (!databases[dbName]) databases[dbName] = {};
          databases[dbName][name] = {};
        },
        transaction: (storeNames: string[], mode: string) => ({
          objectStore: (name: string) => ({
            get: jest.fn().mockReturnValue({
              onsuccess: null as any,
              result: databases[dbName]?.[name],
            }),
            put: jest.fn().mockReturnValue({
              onsuccess: null as any,
            }),
            delete: jest.fn().mockReturnValue({
              onsuccess: null as any,
            }),
            clear: jest.fn().mockReturnValue({
              onsuccess: null as any,
            }),
          }),
        }),
      },
    })),
  };
}

/**
 * Intersection Observer mock helper
 */
export function mockIntersectionObserver() {
  return class MockIntersectionObserver {
    constructor(public callback: IntersectionObserverCallback) {}
    observe = jest.fn();
    unobserve = jest.fn();
    disconnect = jest.fn();
  };
}

/**
 * Request Animation Frame mock helper
 */
export function mockRequestAnimationFrame() {
  let id = 0;
  const callbacks: Record<number, FrameRequestCallback> = {};

  return {
    requestAnimationFrame: jest.fn((callback: FrameRequestCallback) => {
      callbacks[++id] = callback;
      return id;
    }),
    cancelAnimationFrame: jest.fn((id: number) => {
      delete callbacks[id];
    }),
    flush: (time = 16) => {
      Object.values(callbacks).forEach(cb => cb(time));
    },
  };
}

/**
 * Performance metrics mock
 */
export function mockPerformance() {
  const marks: Record<string, number> = {};
  const measures: Record<string, { startMark: string; duration: number }> = {};

  return {
    mark: jest.fn((name: string) => {
      marks[name] = Date.now();
    }),
    measure: jest.fn((name: string, startMark: string, endMark?: string) => {
      const start = marks[startMark] || 0;
      const end = endMark ? marks[endMark] : Date.now();
      measures[name] = {
        startMark,
        duration: end - start,
      };
    }),
    getEntriesByName: jest.fn((name: string) => [
      measures[name] || { duration: 0 },
    ]),
    now: jest.fn(() => Date.now()),
  };
}

/**
 * Debounce/Throttle test helpers
 */
export async function advanceTimersByTime(ms: number) {
  jest.advanceTimersByTime(ms);
  await new Promise(resolve => setTimeout(resolve, 0));
}

export function useFakeTimers() {
  jest.useFakeTimers();
  return {
    cleanup: () => jest.runOnlyPendingTimers(),
    restore: () => jest.useRealTimers(),
  };
}

/**
 * Promise helpers
 */
export function flushPromises() {
  return new Promise(resolve => setImmediate(resolve));
}

export async function waitForPromises() {
  return new Promise(resolve => {
    setTimeout(resolve, 0);
  });
}

/**
 * Error boundary test helper
 */
export function throwError(message: string) {
  throw new Error(message);
}

export const suppressConsoleErrors = () => {
  const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
  return () => spy.mockRestore();
};

export const suppressConsoleWarnings = () => {
  const spy = jest.spyOn(console, 'warn').mockImplementation(() => {});
  return () => spy.mockRestore();
};
