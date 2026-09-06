// ============================================================================
// IMS 2.0 - the Live sync card on /online-store/shopify (owner ruling 2026-09-06)
// ============================================================================
// The card must show what the last run DID (updated / failed / awaiting first
// publish), name each failure with its code and message, say when the next
// IST slot is, and the button must call the SAME backend door the schedule
// runs. Driven through the REAL page with the api module mocked -- a test of
// a card component alone would pass even if nobody wired it into the screen.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastCalls: { kind: string; msg: string }[] = [];

vi.mock('../../../services/api/onlineStore', () => ({
  pushApi: {
    getStatus: vi.fn(),
    pushAllPending: vi.fn(),
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

const DARK_MODE = { mode: 'SIMULATED' as const, is_live: false, writes_enabled: false, dispatch_mode: 'off', creds_present: true };

const LAST_RUN = {
  run_id: 'r1',
  trigger: 'scheduled',
  actor: 'scheduler:shopify-live-sync',
  slot: '2026-09-07 01:00',
  started_at: '2026-09-06T19:30:00+00:00', // 01:00 IST
  finished_at: '2026-09-06T19:31:00+00:00',
  status: 'done',
  mode: 'LIVE',
  selected: 5,
  attempted: 5,
  pushed_ok: 3,
  failed: 2,
  refused_no_photo: 0,
  publish_withheld: 0,
  awaiting_first_publish: 4,
  failures: [
    { product_id: 'P9', sku: 'RB-3025', name: 'Aviator', code: 'PUBLISH_SCOPE_MISSING', error: 'Shopify said no' },
    { product_id: 'P10', sku: 'OO-9208', name: 'Radar', code: null, error: 'timeout' },
  ],
};

function status(liveSync: Record<string, unknown> | null) {
  return {
    mode: DARK_MODE,
    db_connected: true,
    counts: {
      products: { staged: 9, pushed: 5, pending: 9 },
      collections: { total: 0, pushed: 0, pending: 0 },
      menus: { total: 0, pushed: 0, pending: 0 },
      images: { approved: 0, pushed: 0, pending: 0 },
    },
    status_reason: null,
    live_sync: liveSync,
  };
}

const SCHEDULE_ON = {
  enabled: true,
  slots: ['01:00', '09:00'],
  max_products_per_run: 200,
  last_run: LAST_RUN,
  next_slot: { slot: '2026-09-07 09:00', at: '2026-09-07T09:00:00+05:30', label: 'Mon 07 Sep 2026, 09:00 IST' },
};

describe('Live sync card', () => {
  beforeEach(() => {
    toastCalls.length = 0;
    vi.mocked(pushApi.getStatus).mockReset();
    vi.mocked(pushApi.syncLiveNow).mockReset();
    vi.mocked(pushApi.getHistory).mockResolvedValue({ entries: [], count: 0, available: true });
    vi.mocked(syncHealthApi.getSyncHealth).mockResolvedValue({ unavailable: true } as any);
    vi.mocked(syncHealthApi.getParity).mockResolvedValue({ unavailable: true } as any);
    vi.mocked(syncHealthApi.getDrift).mockResolvedValue({ unavailable: true } as any);
  });

  it('shows the last run, every failure with its code, and the next IST slot', async () => {
    vi.mocked(pushApi.getStatus).mockResolvedValue(status(SCHEDULE_ON) as any);
    render(<OnlineShopifySyncPage />);

    const card = await screen.findByTestId('live-sync-card');
    expect(card).toHaveTextContent(/Schedule ON — daily at 01:00, 09:00 IST/);
    expect(card).toHaveTextContent('Mon 07 Sep 2026, 09:00 IST');
    expect(card).toHaveTextContent(/3.*updated/);
    expect(card).toHaveTextContent(/2.*failed/);
    expect(card).toHaveTextContent(/4.*awaiting first publish/);
    expect(card).toHaveTextContent(/scheduled/);
    expect(card).toHaveTextContent('LIVE');
    // The run instant is rendered on the IST clock (01:00), not UTC (19:30).
    expect(card).toHaveTextContent(/1:00 am IST/i);
    const failures = within(card).getByRole('list', { name: /live sync failures/i });
    const rows = within(failures).getAllByRole('listitem');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('RB-3025');
    expect(rows[0]).toHaveTextContent('PUBLISH_SCOPE_MISSING');
    expect(rows[0]).toHaveTextContent('Shopify said no');
    expect(rows[1]).toHaveTextContent('timeout');
    expect(card).toHaveTextContent(/Configured in Settings > Shopify live sync/);
  });

  // Sync audit #7: a product whose price step failed is LIVE at the OLD price.
  // ok=true, so it is neither "failed" nor a clean "updated" -- it gets its own
  // count on the line and its own row in the failures list (code + message).
  it('shows a product that is live at the OLD price on its own line, with the code', async () => {
    const run = {
      ...LAST_RUN,
      failed: 0,
      pushed_ok: 3,
      price_not_synced: 1,
      failures: [
        {
          product_id: 'P11',
          sku: 'RB-2140',
          name: 'Wayfarer',
          code: 'PRICE_NOT_SYNCED',
          reason: null,
          error: 'Live on the website at the OLD price: the price change did not reach Shopify.',
        },
      ],
    };
    vi.mocked(pushApi.getStatus).mockResolvedValue(status({ ...SCHEDULE_ON, last_run: run }) as any);
    render(<OnlineShopifySyncPage />);

    const card = await screen.findByTestId('live-sync-card');
    expect(card).toHaveTextContent('1 at the OLD price');
    const failures = within(card).getByRole('list', { name: /live sync failures/i });
    const rows = within(failures).getAllByRole('listitem');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('RB-2140');
    expect(rows[0]).toHaveTextContent('PRICE_NOT_SYNCED');
    expect(rows[0]).toHaveTextContent(/OLD price/);
  });

  it('the button runs the same backend door and reloads the status', async () => {
    vi.mocked(pushApi.getStatus).mockResolvedValue(status({ ...SCHEDULE_ON, last_run: null }) as any);
    vi.mocked(pushApi.syncLiveNow).mockResolvedValue({
      run: { ...LAST_RUN, trigger: 'manual', mode: 'SIMULATED', pushed_ok: 3, failed: 0, awaiting_first_publish: 4, failures: [] },
      live_sync: SCHEDULE_ON,
    } as any);
    render(<OnlineShopifySyncPage />);
    const card = await screen.findByTestId('live-sync-card');
    expect(card).toHaveTextContent(/No live sync has run yet/);

    await userEvent.click(within(card).getByRole('button', { name: /sync live products now/i }));

    await waitFor(() => expect(pushApi.syncLiveNow).toHaveBeenCalledTimes(1));
    expect(toastCalls).toContainEqual({
      kind: 'success',
      msg: 'Live sync (dry-run (SIMULATED)): 3 updated, 0 failed, 4 awaiting first publish',
    });
    // DARK: no confirm dialog was needed; the status is re-read afterwards.
    await waitFor(() => expect(pushApi.getStatus).toHaveBeenCalledTimes(2));
  });

  it('says the schedule is OFF and shows no next slot when disabled', async () => {
    vi.mocked(pushApi.getStatus).mockResolvedValue(
      status({ ...SCHEDULE_ON, enabled: false, next_slot: null, last_run: null }) as any,
    );
    render(<OnlineShopifySyncPage />);
    const card = await screen.findByTestId('live-sync-card');
    expect(card).toHaveTextContent(/Schedule OFF — only the button below syncs/);
    expect(card).not.toHaveTextContent(/Next run/);
  });
});
