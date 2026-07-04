// ============================================================================
// IMS 2.0 - IdleLogoutWatcher integration test (owner bug report 2026-07-04:
// "the popup shows but logout does not happen")
// ============================================================================
// Mounts the REAL watcher + REAL useIdleLogout hook with fake timers and a
// mocked auth/policy/router layer, walks the full idle -> warn -> expiry flow,
// and asserts logout() is called AND navigation to /login?reason=idle happens.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import { IdleLogoutWatcher } from '../IdleLogoutWatcher';

// ---- mocks -----------------------------------------------------------------

const logoutSpy = vi.fn(() => Promise.resolve());

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    logout: logoutSpy,
    user: { id: 'u1', roles: ['STORE_MANAGER'] },
  }),
}));

const parkSpy = vi.fn(() => null);
vi.mock('../../../stores/posStore', () => ({
  usePOSStore: {
    getState: () => ({ parkCurrentSale: parkSpy }),
  },
}));

// Short policy so the test walks the real timeline quickly:
// 1 minute timeout, 10s warning window.
vi.mock('../../../constants/sessionPolicy', () => ({
  loadSessionPolicy: () => Promise.resolve(),
  getSessionPolicy: () => ({ enabled: true, minutes: 1, warnSeconds: 10 }),
}));

// ---- helpers ----------------------------------------------------------------

/** Deterministic in-memory localStorage (the runner's shim lacks clear/removeItem
 *  -- same workaround as useIdleLogout.test.ts). */
function installMemoryLocalStorage() {
  const store = new Map<string, string>();
  const mock: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => store.delete(k),
    setItem: (k: string, v: string) => store.set(k, String(v)),
  };
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: mock,
  });
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

function mountApp() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route
          path="*"
          element={
            <>
              <IdleLogoutWatcher />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ---- tests -------------------------------------------------------------------

describe('IdleLogoutWatcher end-to-end (fake timers)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    installMemoryLocalStorage();
    logoutSpy.mockClear();
    parkSpy.mockClear();
    // Tab visible for the whole scenario.
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => false,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the warning popup, then actually logs out + navigates when the countdown ends', async () => {
    mountApp();

    // Not warning yet.
    expect(screen.queryByText(/Still there\?/)).toBeNull();

    // Advance to inside the warning window (60s timeout - 10s warn = warn at 50s).
    await act(async () => {
      vi.advanceTimersByTime(52_000);
    });
    expect(screen.getByText(/Still there\?/)).toBeTruthy();
    expect(logoutSpy).not.toHaveBeenCalled();

    // Let the warning window fully elapse (10s + slack for tick boundaries).
    await act(async () => {
      vi.advanceTimersByTime(12_000);
    });

    // The REPORTED BUG: popup shown but logout never fires. Assert it DOES.
    expect(logoutSpy).toHaveBeenCalledTimes(1);
    // Watcher must navigate to the login route with the idle reason.
    expect(screen.getByTestId('loc').textContent).toBe('/login?reason=idle');
    // Modal is gone after logout.
    expect(screen.queryByText(/Still there\?/)).toBeNull();
  });

  it('"Sign out now" button logs out immediately', async () => {
    mountApp();
    await act(async () => {
      vi.advanceTimersByTime(52_000); // into the warning window
    });
    const btn = screen.getByRole('button', { name: /Sign out now/i });
    await act(async () => {
      btn.click();
    });
    expect(logoutSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('loc').textContent).toBe('/login?reason=idle');
  });

  it('mouse movement while the popup is up does NOT dismiss it (only the button does)', async () => {
    mountApp();
    await act(async () => {
      vi.advanceTimersByTime(52_000); // popup up
    });
    expect(screen.getByText(/Still there\?/)).toBeTruthy();

    // Wiggle the mouse — the unattended-terminal bypass this fix closes.
    await act(async () => {
      window.dispatchEvent(new Event('mousemove'));
      vi.advanceTimersByTime(2_000);
    });
    expect(screen.getByText(/Still there\?/)).toBeTruthy(); // still up

    // Countdown completes -> logout fires despite the wiggling.
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(logoutSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('loc').textContent).toBe('/login?reason=idle');
  });

  it('"Stay signed in" cancels the warning and no logout fires', async () => {
    mountApp();
    await act(async () => {
      vi.advanceTimersByTime(52_000);
    });
    const stay = screen.getByRole('button', { name: /Stay signed in/i });
    await act(async () => {
      stay.click();
    });
    expect(screen.queryByText(/Still there\?/)).toBeNull();
    await act(async () => {
      vi.advanceTimersByTime(30_000); // well past the original expiry
    });
    expect(logoutSpy).not.toHaveBeenCalled();
  });
});
