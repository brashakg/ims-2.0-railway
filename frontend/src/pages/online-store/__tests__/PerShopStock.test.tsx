// ============================================================================
// IMS 2.0 - per-shop stock on /online-store/shopify (owner ruling 2026-09-06)
// ============================================================================
// Three honesty rules of the sync page, each driven through the REAL page
// with the api module mocked (a test of a helper alone would pass even if
// nobody wired it into the screen):
//   1. the "Per shop" / "On-hand unknown" lines name SHOP CODES, never the raw
//      store_id (Gangadham Pune's store_id is a UUID);
//   2. the Live sync card shows the scheduled run's STOCK verdict (the only
//      operator-visible trace of a not-ok stock pass besides the task);
//   3. a Shopify location that fulfils online orders but maps to no shop is
//      named (IMS never writes it -- it sells whatever number it holds).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastCalls: { kind: string; msg: string }[] = [];

vi.mock('../../../services/api/onlineStore', () => ({
  pushApi: {
    getStatus: vi.fn(),
    getLocations: vi.fn(),
    pushAllPending: vi.fn(),
    pushStock: vi.fn(),
    getHistory: vi.fn(),
    syncLiveNow: vi.fn(),
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
    user: { user_id: 'u1', roles: ['ADMIN'], activeStoreId: 'BV-BOK-02' },
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

const PUNE_UUID = '4dc49c44-08a1-46e1-85fb-8b7eca55f560';
const BOK = 'gid://shopify/Location/58793230523';
const PUNE_GID = 'gid://shopify/Location/76684427513';

const STORES = [
  { store_id: 'BV-BOK-02', store_code: 'BV-BOK-02', store_name: 'Sec 4 Bokaro', shopify_location_id: BOK, shopify_location_name: 'Better Vision Sector 4' },
  { store_id: PUNE_UUID, store_code: 'BV-PUN-01', store_name: 'GANGADHAM- PUNE', shopify_location_id: null, shopify_location_name: null },
];

const LIVE_MODE = {
  mode: 'LIVE' as const,
  is_live: true,
  writes_enabled: true,
  dispatch_mode: 'live',
  creds_present: true,
  online_store_publication_id: 'gid://shopify/Publication/1',
  online_store_publication_source: 'pinned',
  stores_total: 2,
  stores_mapped: 1,
  unmapped_stores: [STORES[1]],
  stores: STORES,
};

function status(liveSync: Record<string, unknown> | null = null) {
  return {
    mode: LIVE_MODE,
    db_connected: true,
    counts: {
      products: { staged: 9, pushed: 5, pending: 0 },
      collections: { total: 0, pushed: 0, pending: 0 },
      menus: { total: 0, pushed: 0, pending: 0 },
      images: { approved: 0, pushed: 0, pending: 0 },
    },
    status_reason: null,
    live_sync: liveSync,
  };
}

describe('per-shop stock on the sync page', () => {
  beforeEach(() => {
    toastCalls.length = 0;
    vi.mocked(pushApi.getStatus).mockReset();
    vi.mocked(pushApi.pushStock).mockReset();
    vi.mocked(pushApi.getLocations).mockResolvedValue({ mode: 'SIMULATED', reason: null, locations: [] });
    vi.mocked(pushApi.getHistory).mockResolvedValue({ entries: [], count: 0, available: true });
    vi.mocked(syncHealthApi.getSyncHealth).mockResolvedValue({ unavailable: true } as any);
    vi.mocked(syncHealthApi.getParity).mockResolvedValue({ unavailable: true } as any);
    vi.mocked(syncHealthApi.getDrift).mockResolvedValue({ unavailable: true } as any);
  });

  it('names shop CODES, never the raw store_id, on the per-shop and unknown lines', async () => {
    vi.mocked(pushApi.getStatus).mockResolvedValue(status() as any);
    vi.mocked(pushApi.pushStock).mockResolvedValue({
      mode: 'SIMULATED',
      entity: 'stock',
      action: 'sync',
      ok: false,
      code: 'STOCK_ONHAND_UNKNOWN',
      error: 'on-hand unknown at BV-PUN-01 -- written nowhere this pass (never as 0); the other shops were written',
      reason: 'dry_run (Preview first)',
      payload: {
        candidates: 1,
        changed: 1,
        stores_total: 2,
        stores_mapped: 2,
        unmapped_stores: [],
        unknown_stores: [PUNE_UUID],
        plan: [{ product_id: 'cat-1', quantities: { 'SKU-0': { 'BV-BOK-02': 2, [PUNE_UUID]: 1 } } }],
      },
    } as any);
    render(<OnlineShopifySyncPage />);
    const button = await screen.findByRole('button', { name: /preview stock/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    await userEvent.click(button);

    const result = await screen.findByTestId('stock-pass-result');
    await waitFor(() => expect(pushApi.pushStock).toHaveBeenCalledWith(true));
    // A SIMULATED pass prints the PLAN; a LIVE one prints what Shopify
    // ACCEPTED (round-4 P2 -- the backend replaces the plan with the
    // accepted rows), so the label must say which it is.
    expect(result).toHaveTextContent('Per shop, planned: BV-BOK-02: 2 · BV-PUN-01: 1');
    expect(result).toHaveTextContent(/On-hand unknown this pass .*: BV-PUN-01/);
    // The raw UUID appears nowhere on the result -- the owner reads shop codes.
    expect(within(result).queryByText(new RegExp(PUNE_UUID))).toBeNull();
    expect(result.textContent).not.toContain(PUNE_UUID + ':');
  });

  // Round 5 (first-push): the toast said "Stock not written" over a press that
  // WROTE. The else arm fired for every not-ok code except STORE_UNMAPPED and
  // dropped the count line entirely, so a LIVE press returning ok=false /
  // SHOPIFY_LOCATION_UNMAPPED with synced=1 told the owner nothing had reached
  // Shopify while three shops' numbers had just gone out. Restore
  // `Stock not written — ...` and this fails.
  it('a not-ok press that DID write says what it wrote, then why it is not ok', async () => {
    vi.mocked(pushApi.getStatus).mockResolvedValue(status() as any);
    vi.mocked(pushApi.pushStock).mockResolvedValue({
      mode: 'LIVE',
      entity: 'stock',
      action: 'sync',
      ok: false,
      code: 'SHOPIFY_LOCATION_UNMAPPED',
      error:
        'Shopify location(s) that fulfil online orders but map to no shop: Gangadham Pune',
      payload: { candidates: 1, changed: 1, synced: 1, failed: 0, unmapped_stores: [] },
    } as any);
    render(<OnlineShopifySyncPage />);
    // "Preview first" is on by default; the LIVE press is the one that writes.
    const preview = await screen.findByRole('checkbox');
    await userEvent.click(preview);
    const button = await screen.findByRole('button', { name: /push stock/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    await userEvent.click(button);

    await waitFor(() => expect(pushApi.pushStock).toHaveBeenCalledWith(false));
    const warn = toastCalls.find((t) => t.kind === 'warning');
    expect(warn?.msg).toContain('1 of 1 listings changed, 1 written');
    expect(warn?.msg).toContain('Gangadham Pune');
    expect(toastCalls.map((t) => t.msg).join(' ')).not.toContain('Stock not written');
  });

  it("shows the scheduled run's stock verdict on the Live sync card", async () => {
    const run = {
      run_id: 'r1',
      trigger: 'scheduled',
      slot: '2026-09-07 01:00',
      started_at: '2026-09-06T19:30:00+00:00',
      status: 'done',
      mode: 'LIVE',
      selected: 3,
      attempted: 3,
      pushed_ok: 3,
      failed: 0,
      awaiting_first_publish: 0,
      failures: [],
      stock: {
        ok: false,
        changed: 71,
        synced: 71,
        failed: 0,
        code: 'STORE_UNMAPPED',
        error: 'shops holding listed stock with no Shopify location: BV-PUN-01 -- map them on the Organization page',
      },
    };
    vi.mocked(pushApi.getStatus).mockResolvedValue(
      status({ enabled: true, slots: ['01:00', '09:00'], max_products_per_run: 200, last_run: run, next_slot: null }) as any,
    );
    render(<OnlineShopifySyncPage />);
    const card = await screen.findByTestId('live-sync-card');
    const line = within(card).getByTestId('live-sync-stock-line');
    expect(line).toHaveTextContent(/Stock pass:.*NOT ok/);
    expect(line).toHaveTextContent('71 changed');
    expect(line).toHaveTextContent('STORE_UNMAPPED');
    expect(line).toHaveTextContent('BV-PUN-01');
  });

  it('names a Shopify location that fulfils online orders but maps to no shop', async () => {
    vi.mocked(pushApi.getStatus).mockResolvedValue(status() as any);
    vi.mocked(pushApi.getLocations).mockResolvedValue({
      mode: 'LIVE',
      reason: null,
      locations: [
        { id: BOK, name: 'Better Vision Sector 4', isActive: true, fulfillsOnlineOrders: true, mapped_store_id: 'BV-BOK-02', unmapped_online_fulfilling: false },
        { id: PUNE_GID, name: 'Gangadham Pune', isActive: true, fulfillsOnlineOrders: true, mapped_store_id: null, unmapped_online_fulfilling: true },
      ],
    } as any);
    render(<OnlineShopifySyncPage />);
    const warn = await screen.findByTestId('unmapped-fulfilling-locations');
    expect(warn).toHaveTextContent('Gangadham Pune');
    expect(warn).toHaveTextContent(/maps to no shop/);
    expect(warn).not.toHaveTextContent('Better Vision Sector 4');
  });

  it('shows no such warning when every fulfilling location is mapped', async () => {
    vi.mocked(pushApi.getStatus).mockResolvedValue(status() as any);
    vi.mocked(pushApi.getLocations).mockResolvedValue({
      mode: 'LIVE',
      reason: null,
      locations: [{ id: BOK, name: 'Better Vision Sector 4', isActive: true, fulfillsOnlineOrders: true, mapped_store_id: 'BV-BOK-02', unmapped_online_fulfilling: false }],
    } as any);
    render(<OnlineShopifySyncPage />);
    await screen.findByRole('button', { name: /preview stock/i });
    expect(screen.queryByTestId('unmapped-fulfilling-locations')).toBeNull();
  });

  // Round-4 P4: the rule is the BACKEND's (shopify_push.is_stray_fulfilling,
  // stamped on every row by GET /push/locations), never re-derived here. The
  // page's old TypeScript copy read `isActive !== false` where the backend
  // reads truthy `isActive`, so a row with NO isActive field -- unmapped and
  // fulfilling -- was reported here and called fine by the verdict. Restore the
  // local derivation and this row is warned about again -> this fails.
  it('never re-derives the rule: an unmapped row the backend cleared is not warned about', async () => {
    vi.mocked(pushApi.getStatus).mockResolvedValue(status() as any);
    vi.mocked(pushApi.getLocations).mockResolvedValue({
      mode: 'LIVE',
      reason: null,
      locations: [
        { id: PUNE_GID, name: 'Gangadham Pune', fulfillsOnlineOrders: true, mapped_store_id: null, unmapped_online_fulfilling: false },
      ],
    } as any);
    render(<OnlineShopifySyncPage />);
    await screen.findByRole('button', { name: /preview stock/i });
    expect(screen.queryByTestId('unmapped-fulfilling-locations')).toBeNull();
  });
});
