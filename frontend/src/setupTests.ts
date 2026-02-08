// ============================================================================
// IMS 2.0 - Jest Setup File
// ============================================================================
// Global test configuration and mocks

import '@testing-library/jest-dom';

/**
 * Mock IntersectionObserver
 */
global.IntersectionObserver = class IntersectionObserver {
  constructor(public callback: IntersectionObserverCallback) {}
  observe = jest.fn();
  unobserve = jest.fn();
  disconnect = jest.fn();
};

/**
 * Mock RequestAnimationFrame
 */
global.requestAnimationFrame = jest.fn((callback) => {
  setTimeout(callback, 0);
  return 1;
});

global.cancelAnimationFrame = jest.fn();

/**
 * Mock window.matchMedia
 */
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: jest.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

/**
 * Mock window.scrollTo
 */
window.scrollTo = jest.fn();

/**
 * Mock HTMLElement.scrollIntoView
 */
HTMLElement.prototype.scrollIntoView = jest.fn();

/**
 * Mock localStorage
 */
const localStorageMock = {
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn(),
  clear: jest.fn(),
};

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
});

/**
 * Mock sessionStorage
 */
const sessionStorageMock = {
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn(),
  clear: jest.fn(),
};

Object.defineProperty(window, 'sessionStorage', {
  value: sessionStorageMock,
});

/**
 * Mock indexedDB
 */
const indexedDBMock = {
  open: jest.fn().mockReturnValue({
    onsuccess: null,
    onerror: null,
    onupgradeneeded: null,
    result: {
      transaction: jest.fn(),
      objectStoreNames: {
        contains: jest.fn(),
      },
      createObjectStore: jest.fn(),
    },
  }),
};

Object.defineProperty(window, 'indexedDB', {
  value: indexedDBMock,
});

/**
 * Mock fetch API
 */
global.fetch = jest.fn(() =>
  Promise.resolve({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(''),
    blob: () => Promise.resolve(new Blob()),
    arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
    clone: () => ({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    }),
  })
) as jest.Mock;

/**
 * Mock crypto
 */
Object.defineProperty(global, 'crypto', {
  value: {
    getRandomValues: (arr: any) => {
      for (let i = 0; i < arr.length; i++) {
        arr[i] = Math.floor(Math.random() * 256);
      }
      return arr;
    },
  },
});

/**
 * Suppress console methods in tests (can be overridden per test)
 */
const originalError = console.error;
const originalWarn = console.warn;

beforeAll(() => {
  console.error = (...args: any[]) => {
    if (
      typeof args[0] === 'string' &&
      (args[0].includes('Warning: ReactDOM.render') ||
        args[0].includes('Not implemented: HTMLFormElement.prototype.submit'))
    ) {
      return;
    }
    originalError.call(console, ...args);
  };

  console.warn = (...args: any[]) => {
    if (
      typeof args[0] === 'string' &&
      args[0].includes('componentWillReceiveProps')
    ) {
      return;
    }
    originalWarn.call(console, ...args);
  };
});

afterAll(() => {
  console.error = originalError;
  console.warn = originalWarn;
});

/**
 * Clear all mocks after each test
 */
afterEach(() => {
  jest.clearAllMocks();
  localStorageMock.getItem.mockClear();
  localStorageMock.setItem.mockClear();
  localStorageMock.removeItem.mockClear();
  localStorageMock.clear.mockClear();
});

/**
 * Set default timezone for consistent testing
 */
process.env.TZ = 'UTC';

/**
 * Suppress specific warnings
 */
const originalConsoleWarn = console.warn;
console.warn = (...args: any[]) => {
  if (
    typeof args[0] === 'string' &&
    (args[0].includes('act(...)') ||
      args[0].includes('setState') ||
      args[0].includes('useLayoutEffect'))
  ) {
    return;
  }
  originalConsoleWarn(...args);
};
