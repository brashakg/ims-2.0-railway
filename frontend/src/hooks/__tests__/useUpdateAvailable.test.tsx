import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import {
  isUpdateAvailable,
  fetchServedBuild,
} from '../useUpdateAvailable';
import { UpdateAvailableBanner } from '../../components/common/UpdateAvailableBanner';

// ---------------------------------------------------------------------------
// Pure comparator
// ---------------------------------------------------------------------------
describe('isUpdateAvailable', () => {
  it('true only when both ids are known AND differ', () => {
    expect(isUpdateAvailable('abc123', 'def456')).toBe(true);
  });

  it('false when ids match', () => {
    expect(isUpdateAvailable('abc123', 'abc123')).toBe(false);
  });

  it('false when running build is unknown (empty)', () => {
    expect(isUpdateAvailable('', 'def456')).toBe(false);
  });

  it('false when served build is unknown (null) — no false positive on fetch failure', () => {
    expect(isUpdateAvailable('abc123', null)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// fetchServedBuild — fail-soft, cache-bypassing
// ---------------------------------------------------------------------------
describe('fetchServedBuild', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('returns the build id from a valid version.json with cache:no-store', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ build: 'newbuild99' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const build = await fetchServedBuild();
    expect(build).toBe('newbuild99');
    expect(fetchMock).toHaveBeenCalledWith('/version.json', { cache: 'no-store', signal: undefined });
  });

  it('returns null on a non-OK response (e.g. 404 before version.json exists)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
    expect(await fetchServedBuild()).toBeNull();
  });

  it('returns null on a network error (fail-soft, never throws)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    await expect(fetchServedBuild()).resolves.toBeNull();
  });

  it('returns null when JSON lacks a build field', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ foo: 'bar' }) }));
    expect(await fetchServedBuild()).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Banner — shows when a different build is served, dismissible, fail-soft
// ---------------------------------------------------------------------------
describe('UpdateAvailableBanner', () => {
  beforeEach(() => {
    // Stamp THIS running bundle's build id.
    vi.stubGlobal('__APP_BUILD_ID__', 'running-build-1');
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the banner when the served build differs, then reloads on Refresh', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ build: 'newer-build-2' }),
    }));
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });

    render(<UpdateAvailableBanner />);

    await waitFor(() => {
      expect(screen.getByText('A new version is available.')).toBeTruthy();
    });

    act(() => {
      screen.getByText('Refresh').click();
    });
    expect(reload).toHaveBeenCalled();
  });

  it('does NOT show the banner when the served build matches the running build', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ build: 'running-build-1' }),
    }));

    render(<UpdateAvailableBanner />);

    // Give the initial check a tick to resolve.
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByText('A new version is available.')).toBeNull();
  });

  it('does NOT show the banner when version.json fetch fails (fail-soft)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    render(<UpdateAvailableBanner />);

    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByText('A new version is available.')).toBeNull();
  });

  it('stays hidden after dismiss', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ build: 'newer-build-2' }),
    }));

    render(<UpdateAvailableBanner />);
    await waitFor(() => {
      expect(screen.getByText('A new version is available.')).toBeTruthy();
    });

    act(() => {
      screen.getByLabelText('Dismiss update notice').click();
    });
    expect(screen.queryByText('A new version is available.')).toBeNull();
  });
});
