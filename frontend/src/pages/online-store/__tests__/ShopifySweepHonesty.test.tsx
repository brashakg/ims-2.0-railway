// ============================================================================
// IMS 2.0 - the bulk press must show what it did NOT do
// ============================================================================
// The owner's original complaint was a screen that said "pending: 0" while
// nothing had been queued. The same lie moved into the sweep panel: the backend
// counts a photo-less product as `refused_no_photo` and a product that reached
// Shopify but was never made visible as `publish_withheld` -- and the screen
// rendered NEITHER, showing only "N processed". A sweep of 30 good products and
// 10 photo-less ones read as "35 processed" with a green toast.
//
// These tests drive the REAL page (the api module is mocked), because a test of
// a badge component on its own would pass even if nobody ever wired the counts
// into the screen -- which is precisely the bug.
//
// BOTH DIRECTIONS: a panel that always shows the line is as useless as one that
// never does, so the clean-sweep case asserts it is ABSENT.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastCalls: { kind: string; msg: string }[] = [];

vi.mock('../../../services/api/onlineStore', () => ({
  pushApi: {
    getStatus: vi.fn(),
    pushAllPending: vi.fn(),
    getHistory: vi.fn(),
  },
  syncHealthApi: {
    getSyncHealth: vi.fn(),
    getParity: vi.fn(),
    getDrift: vi.fn(),
  },
}));

vi.mock('../../../components/online-store/OnlineStoreSyncBanner', () => ({
  __esModule: true,
  default: () => null,
  formatPushResult: (label: string) => label,
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { user_id: 'u1', roles: ['ADMIN'], activeStoreId: 'ZZ-SOLO' },
    hasRole: (roles: string[]) => roles.includes('ADMIN'),
  }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    success: (m: string) => toastCalls.push({ kind: 'success', msg: m }),
    error: (m: string) => toastCalls.push({ kind: 'error', msg: m }),
    info: (m: string) => toastCalls.push({ kind: 'info', msg: m }),
    warning: (m: string) => toastCalls.push({ kind: 'warning', msg: m }),
  }),
}));

vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

import OnlineShopifySyncPage from '../OnlineShopifySyncPage';
import { pushApi, syncHealthApi } from '../../../services/api/onlineStore';

const LIVE_MODE = {
  mode: 'LIVE' as const,
  is_live: true,
  writes_enabled: true,
  dispatch_mode: 'live',
  creds_present: true,
  online_store_publication_id: 'gid://shopify/Publication/1',
  online_store_publication_source: 'pinned',
};

function status(overrides: Record<string, any> = {}) {
  return {
    mode: { ...LIVE_MODE, ...overrides },
    db_connected: true,
    counts: {
      products: { staged: 40, pushed: 0, pending: 40 },
      collections: { total: 0, pushed: 0, pending: 0 },
      menus: { total: 0, pushed: 0, pending: 0 },
      images: { approved: 0, pushed: 0, pending: 0 },
    },
    status_reason: null,
  };
}

function sweep(summary: Record<string, any>, pushed_count: number) {
  return {
    mode: LIVE_MODE,
    db_connected: true,
    pushed_count,
    limit_reached: false,
    batch_cap: 25,
    offset: 0,
    next_offset: null,
    eligible_total: null,
    summary: { products: summary },
    results: [],
  };
}

async function pressEntity(index: number) {
  render(<OnlineShopifySyncPage />);
  await waitFor(() => expect(pushApi.getStatus).toHaveBeenCalled());
  const buttons = await screen.findAllByRole('button', { name: /^Push$/ });
  await userEvent.click(buttons[index]);
  await waitFor(() => expect(pushApi.pushAllPending).toHaveBeenCalled());
}

async function pressProducts() {
  render(<OnlineShopifySyncPage />);
  await waitFor(() => expect(pushApi.getStatus).toHaveBeenCalled());
  const buttons = await screen.findAllByRole('button', { name: /^Push$/ });
  await userEvent.click(buttons[0]); // the Products card is the first entity
  await waitFor(() => expect(pushApi.pushAllPending).toHaveBeenCalled());
}

beforeEach(() => {
  toastCalls.length = 0;
  vi.clearAllMocks();
  (pushApi.getStatus as any).mockResolvedValue(status());
  (pushApi.getHistory as any).mockResolvedValue({ entries: [], db_connected: true });
  (syncHealthApi.getSyncHealth as any).mockResolvedValue({ unavailable: true });
  (syncHealthApi.getParity as any).mockResolvedValue({ unavailable: true });
  (syncHealthApi.getDrift as any).mockResolvedValue({ unavailable: true });
});

describe('the bulk press reports what it refused', () => {
  it('shows the photo refusals and does not fold them into "processed"', async () => {
    // 25 went to Shopify; 10 were refused for having no photograph.
    (pushApi.pushAllPending as any).mockResolvedValue(
      sweep({ pushed: 25, failed: 0, noop: 0, refused_no_photo: 10 }, 25),
    );

    await pressProducts();

    expect(await screen.findByText(/10 refused \(no photograph\)/i)).toBeTruthy();
    expect(screen.getByText(/25 processed/)).toBeTruthy();
    expect(screen.queryByText(/35 processed/)).toBeNull();
    const msg = toastCalls.map((t) => t.msg).join(' | ');
    expect(msg).toMatch(/10 refused \(no photograph\)/i);
  });

  it('shows the products that reached Shopify but were never made visible', async () => {
    (pushApi.pushAllPending as any).mockResolvedValue(
      sweep({ pushed: 0, failed: 0, noop: 0, publish_withheld: 5 }, 0),
    );

    await pressProducts();

    expect(await screen.findByText(/5 NOT made visible/i)).toBeTruthy();
    // ...and the toast is NOT a green success over five invisible products.
    expect(toastCalls.some((t) => t.kind === 'success')).toBe(false);
  });

  it('says nothing about refusals when there were none', async () => {
    (pushApi.pushAllPending as any).mockResolvedValue(
      sweep({ pushed: 25, failed: 0, noop: 0 }, 25),
    );

    await pressProducts();

    await screen.findByText(/25 processed/);
    expect(screen.queryByText(/refused/i)).toBeNull();
    expect(screen.queryByText(/NOT made visible/i)).toBeNull();
    expect(toastCalls.some((t) => t.kind === 'success')).toBe(true);
  });
});

describe('the third door is visible before the press', () => {
  it('warns when no Online Store channel is resolved', async () => {
    (pushApi.getStatus as any).mockResolvedValue(
      status({ online_store_publication_id: null, online_store_publication_source: 'unresolved' }),
    );

    render(<OnlineShopifySyncPage />);

    expect(
      await screen.findByText(/NOT resolved — presses will publish nothing/i),
    ).toBeTruthy();
  });

  it('says nothing when the channel IS resolved', async () => {
    render(<OnlineShopifySyncPage />);
    await waitFor(() => expect(pushApi.getStatus).toHaveBeenCalled());
    expect(screen.queryByText(/presses will publish nothing/i)).toBeNull();
  });
});


describe('the safety-cap number belongs to products only', () => {
  it('names the product cap when the products sweep stops early', async () => {
    (pushApi.pushAllPending as any).mockResolvedValue({
      ...sweep({ pushed: 25, failed: 0, noop: 0 }, 25),
      limit_reached: true,
    });

    await pressEntity(0); // Products

    const info = toastCalls.filter((t) => t.kind === 'info').map((t) => t.msg).join(' | ');
    expect(info).toMatch(/safety cap of 25 products/i);
  });

  it('does NOT quote the 25-product cap for collections (they stop at 100)', async () => {
    (pushApi.pushAllPending as any).mockResolvedValue({
      ...sweep({ pushed: 100, failed: 0, noop: 0 }, 100),
      limit_reached: true,
    });

    await pressEntity(1); // Collections

    const info = toastCalls.filter((t) => t.kind === 'info').map((t) => t.msg).join(' | ');
    expect(info).not.toMatch(/25/);
    expect(info).toMatch(/run again to continue/i);
  });
});
