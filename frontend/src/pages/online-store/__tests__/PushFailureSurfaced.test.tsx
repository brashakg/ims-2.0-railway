// ============================================================================
// IMS 2.0 - a failed push must SAY WHY, on the screen the owner pressed
// ============================================================================
// Prod 2026-09-05: "Push" on /online-store/shopify and "Send to website" on
// /online-store/products both ran, six presses were refused by Shopify
// (publishablePublish: no write_publications scope) and the owner learnt it
// from the audit collection, not the screen. The backend now returns a stable
// `code` + a plain-language `error`; these tests pin that BOTH screens render
// that message (and the code) instead of a bare count or a generic "failed".
// The REAL pages are driven (api modules mocked) so an unwired field fails.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastCalls: { kind: string; msg: string }[] = [];

const SCOPE_MSG =
  "Saved on Shopify but not made visible: the IMS app has no write_publications access. " +
  "Re-approve the app's permissions in Shopify, then press again.";

const DENIED = {
  mode: 'LIVE' as const,
  entity: 'product',
  action: 'create',
  target_id: 'P1',
  ok: false,
  shopify_id: 'gid://shopify/Product/900',
  error: SCOPE_MSG,
  code: 'PUBLISH_SCOPE_MISSING',
  reason: 'publish_withheld',
};

vi.mock('../../../services/api/onlineStore', () => ({
  onlineStoreApi: { getSummary: vi.fn() },
  pushApi: {
    getStatus: vi.fn(),
    pushAllPending: vi.fn(),
    getHistory: vi.fn(),
    pushProduct: vi.fn(),
    takeDownProduct: vi.fn(),
  },
  syncHealthApi: {
    getSyncHealth: vi.fn(),
    getParity: vi.fn(),
    getDrift: vi.fn(),
  },
}));

vi.mock('../../../services/api/catalog', () => ({
  catalogProductsApi: { list: vi.fn() },
}));

// The REAL formatPushResult (the line under test); only the banner itself is
// stubbed, it fetches on mount.
vi.mock('../../../components/online-store/OnlineStoreSyncBanner', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../components/online-store/OnlineStoreSyncBanner')
  >();
  return {
    ...actual,
    __esModule: true,
    default: () => null,
    SyncChip: () => null,
  };
});

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

import OnlineProductsPage from '../OnlineProductsPage';
import OnlineShopifySyncPage from '../OnlineShopifySyncPage';
import { formatPushResult } from '../../../components/online-store/OnlineStoreSyncBanner';
import { onlineStoreApi, pushApi, syncHealthApi } from '../../../services/api/onlineStore';
import { catalogProductsApi } from '../../../services/api/catalog';
import { buildApiError } from '../../../services/api/client';

const LIVE_MODE = {
  mode: 'LIVE' as const,
  is_live: true,
  writes_enabled: true,
  dispatch_mode: 'live',
  creds_present: true,
  online_store_publication_id: 'gid://shopify/Publication/1',
  online_store_publication_source: 'pinned',
};

beforeEach(() => {
  toastCalls.length = 0;
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  (onlineStoreApi.getSummary as any).mockResolvedValue({});
  (catalogProductsApi.list as any).mockResolvedValue({
    products: [
      {
        id: 'P1',
        sku: 'RB2140',
        title: 'Ray-Ban RB2140',
        brand: 'Ray-Ban',
        mrp: 5000,
        ecom: { status: 'DRAFT', locally_modified: true },
      },
    ],
    total: 1,
  });
  (pushApi.getStatus as any).mockResolvedValue({
    mode: LIVE_MODE,
    db_connected: true,
    counts: { products: { staged: 1, pushed: 0, pending: 1 } },
    status_reason: null,
  });
  (pushApi.getHistory as any).mockResolvedValue({ entries: [], db_connected: true });
  (syncHealthApi.getSyncHealth as any).mockResolvedValue({ unavailable: true });
  (syncHealthApi.getParity as any).mockResolvedValue({ unavailable: true });
  (syncHealthApi.getDrift as any).mockResolvedValue({ unavailable: true });
});

describe('formatPushResult', () => {
  it('renders the plain-language message and the stable code', () => {
    const line = formatPushResult('Ray-Ban RB2140', DENIED);
    expect(line).toContain(SCOPE_MSG);
    expect(line).toContain('[PUBLISH_SCOPE_MISSING]');
    expect(line).not.toMatch(/graphql/i);
  });
});

describe('/online-store/products "Send to website"', () => {
  it('toasts the server message and code when the publish is refused', async () => {
    (pushApi.pushProduct as any).mockResolvedValue(DENIED);
    render(<OnlineProductsPage />);

    const btn = await screen.findByRole('button', { name: /Send to website/i });
    await userEvent.click(btn);
    await waitFor(() => expect(pushApi.pushProduct).toHaveBeenCalledWith('P1'));

    await waitFor(() => expect(toastCalls.length).toBeGreaterThan(0));
    const [t] = toastCalls;
    expect(t.kind).toBe('error');
    expect(t.msg).toContain('Ray-Ban RB2140');
    expect(t.msg).toContain(SCOPE_MSG);
    expect(t.msg).toContain('[PUBLISH_SCOPE_MISSING]');
  });
});

describe('/online-store/shopify "Push"', () => {
  it('puts the first failed row\'s message on the toast and in the results list', async () => {
    (pushApi.pushAllPending as any).mockResolvedValue({
      mode: LIVE_MODE,
      db_connected: true,
      pushed_count: 0,
      limit_reached: false,
      batch_cap: 25,
      offset: 0,
      next_offset: null,
      eligible_total: null,
      summary: { products: { pushed: 0, failed: 0, noop: 0, publish_withheld: 1 } },
      results: [DENIED],
    });
    render(<OnlineShopifySyncPage />);
    await waitFor(() => expect(pushApi.getStatus).toHaveBeenCalled());
    const buttons = await screen.findAllByRole('button', { name: /^Push$/ });
    await userEvent.click(buttons[0]);
    await waitFor(() => expect(pushApi.pushAllPending).toHaveBeenCalled());

    const warn = await waitFor(() => {
      const w = toastCalls.find((t) => t.kind === 'warning');
      expect(w).toBeTruthy();
      return w!;
    });
    expect(warn.msg).toContain(SCOPE_MSG);
    expect(warn.msg).toContain('[PUBLISH_SCOPE_MISSING]');
    // ...and the inline row state says the same.
    expect(await screen.findByText(new RegExp('PUBLISH_SCOPE_MISSING'))).toBeTruthy();
  });

  // Prod 2026-09-05 22:24 UTC: the sweep ran ~11 s on the server (6 live, 4
  // refused, all audited) while the screen showed "Network error. Please check
  // your internet connection and try again." A false failure on a storefront
  // action invites the second press. The rejection is built by the REAL client
  // transform so a hand-written message cannot make this pass.
  it('a timed-out sweep says the push is still running, not "check your internet"', async () => {
    (pushApi.pushAllPending as any).mockRejectedValue(
      buildApiError({
        message: 'timeout of 180000ms exceeded',
        code: 'ECONNABORTED',
        config: { url: '/online-store/push/all-pending?entities=products&limit=100' },
        response: undefined,
      } as any),
    );
    render(<OnlineShopifySyncPage />);
    await waitFor(() => expect(pushApi.getStatus).toHaveBeenCalled());
    const buttons = await screen.findAllByRole('button', { name: /^Push$/ });
    await userEvent.click(buttons[0]);
    await waitFor(() => expect(pushApi.pushAllPending).toHaveBeenCalled());

    const err = await waitFor(() => {
      const t = toastCalls.find((c) => c.kind === 'error');
      expect(t).toBeTruthy();
      return t!;
    });
    expect(err.msg).toMatch(/still running on the server/);
    expect(err.msg).toMatch(/do not press again/);
    expect(err.msg).not.toMatch(/internet connection/);
  });
});
