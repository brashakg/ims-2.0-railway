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
import { render, screen } from '@testing-library/react';

const toastCalls: { kind: string; msg: string }[] = [];

vi.mock('../../../services/api/onlineStore', () => ({
  pushApi: {
    getStatus: vi.fn(),
    getLocations: vi.fn().mockResolvedValue({ mode: 'SIMULATED', reason: null, locations: [] }),
    pushAllPending: vi.fn(),
    getHistory: vi.fn(),
  },
  syncHealthApi: {
    getSyncHealth: vi.fn(),
    getParity: vi.fn(),
    getDrift: vi.fn(),
  },
}));

// The banner itself is stubbed out, but `pushToastLevel` is kept REAL: the
// sweep panel decides its per-row tick with it, and a hand-rolled copy in the
// mock would be exactly the second implementation this page just stopped
// carrying.
vi.mock('../../../components/online-store/OnlineStoreSyncBanner', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
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

beforeEach(() => {
  toastCalls.length = 0;
  vi.clearAllMocks();
  (pushApi.getStatus as any).mockResolvedValue(status());
  (pushApi.getHistory as any).mockResolvedValue({ entries: [], db_connected: true });
  (syncHealthApi.getSyncHealth as any).mockResolvedValue({ unavailable: true });
  (syncHealthApi.getParity as any).mockResolvedValue({ unavailable: true });
  (syncHealthApi.getDrift as any).mockResolvedValue({ unavailable: true });
});

describe('sync diagnostics use matching units and real fields', () => {
  it('shows colour/size coverage, website pages, and each missing reason', async () => {
    vi.mocked(syncHealthApi.getParity).mockResolvedValue({ parity: {
      entities: { catalog_products: { total: 633, pushed: 469, missing: 164 } },
      colours_sizes: { checked: true, total: 633, online: 610, website_pages: 469, missing: 23,
        missing_items: [
          { sku: 'RB-RED', reason: 'family push refused - OPTION_MISSING', code: 'OPTION_MISSING' },
          { sku: 'OLD-FRAME', reason: 'archived' },
          { sku: 'NEW-FRAME', reason: 'not queued' },
        ] },
    } });
    render(<OnlineShopifySyncPage />);
    expect(await screen.findByText(/610 of 633 colours.sizes online.*469 website pages/)).toBeTruthy();
    expect(screen.getByText('23 colours/sizes not online')).toBeTruthy();
    expect(screen.getByText(/family push refused - OPTION_MISSING/)).toBeTruthy();
    expect(screen.getByText(/archived/)).toBeTruthy();
    expect(screen.getByText(/not queued/)).toBeTruthy();
    expect(screen.queryByText('469 / 633')).toBeNull();
  });

  it('names content differences and incomplete comparisons', async () => {
    vi.mocked(syncHealthApi.getDrift).mockResolvedValue({ checked: true,
      counts: { scanned: 2, drifted: 1, unknown: 1 },
      drifted: [{ sku: 'RB-RED', fields: ['title', 'price: RB-RED'] }],
    });
    render(<OnlineShopifySyncPage />);
    expect(await screen.findByText('RB-RED: title, price: RB-RED')).toBeTruthy();
    expect(screen.getByText('Could not compare')).toBeTruthy();
    expect(screen.getByText(/timestamps alone do not trigger an alarm/)).toBeTruthy();
  });

  it('does not substitute page totals when colour mappings are unavailable', async () => {
    vi.mocked(syncHealthApi.getParity).mockResolvedValue({ parity: {
      entities: { catalog_products: { total: 633, pushed: 469, missing: 164 } },
      colours_sizes: { checked: false, reason: 'Colour/size mappings could not be read' },
    } });
    vi.mocked(syncHealthApi.getDrift).mockResolvedValue({ checked: true,
      counts: { scanned: 1, drifted: 0, unknown: 0 }, drifted: [] });
    render(<OnlineShopifySyncPage />);
    expect(await screen.findByText('Colour/size mappings could not be read')).toBeTruthy();
    expect(screen.queryByText('469 / 633')).toBeNull();
    expect(screen.queryByText('Could not compare')).toBeNull();
  });

  // Review of #1275: the slow Shopify drift read must not hold the other tiles.
  it('shows parity while the drift read is still running', async () => {
    vi.mocked(syncHealthApi.getDrift).mockReturnValue(new Promise(() => {}));
    vi.mocked(syncHealthApi.getParity).mockResolvedValue({ parity: {
      colours_sizes: { checked: true, total: 5, online: 5, website_pages: 3, missing: 0, missing_items: [] },
    } });
    render(<OnlineShopifySyncPage />);
    expect(await screen.findByText(/5 of 5 colours.sizes online/)).toBeTruthy();
    // ...but Refresh stays disabled: a second press would start another slow
    // Shopify read over the one still running (review of #1275).
    expect((screen.getByRole('button', { name: /Refresh/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('keeps the raw counts, the denominator and why a page was not compared', async () => {
    vi.mocked(syncHealthApi.getParity).mockResolvedValue({ parity: {
      entities: { ecom_collections: { total: 12, pushed: 9, missing: 3 } },
      colours_sizes: { checked: true, total: 5, online: 5, website_pages: 3, missing: 0, missing_items: [] },
    } });
    vi.mocked(syncHealthApi.getDrift).mockResolvedValue({ checked: true,
      counts: { scanned: 49, candidates: 469, drifted: 0, unknown: 1 }, drifted: [],
      unknown: [{ sku: 'RB-BLK', reason: 'separate listing for a colour/size of RB-GLD' }] });
    render(<OnlineShopifySyncPage />);
    expect(await screen.findByText('49 of 469 pages')).toBeTruthy();
    expect(screen.getByText('9 / 12')).toBeTruthy();
    expect(screen.getByText(/separate listing for a colour\/size of RB-GLD/)).toBeTruthy();
  });
});
