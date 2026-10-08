// ============================================================================
// Design Queue -> Publish: the toast says in plain words what happened to the
// photo on the website. The REAL page is driven (api modules mocked), so a
// page that went back to the shared 'LIVE (noop) · gid://...' line fails here.
// ============================================================================
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const toastCalls: { kind: string; msg: string }[] = [];

vi.mock('../../../services/api/onlineStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/onlineStore')>();
  return {
    ...actual,
    imagesApi: { list: vi.fn() },
    pushApi: { pushImage: vi.fn() },
  };
});

// The REAL formatter; only the banner (it fetches on mount) is stubbed.
vi.mock('../../../components/online-store/OnlineStoreSyncBanner', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../components/online-store/OnlineStoreSyncBanner')
  >();
  return { ...actual, __esModule: true, default: () => null, SyncChip: () => null };
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

import DesignQueuePage from '../DesignQueuePage';
import { imagesApi, pushApi } from '../../../services/api/onlineStore';

const IMG = {
  id: 'I1',
  product_id: 'P1',
  product_title: 'Aviator Gold',
  url: 'https://store.example.com/P1/3f2a9c01.png',
  design_status: 'APPROVED',
};

async function press(result: Record<string, unknown>) {
  (imagesApi.list as any).mockResolvedValue([IMG]);
  (pushApi.pushImage as any).mockResolvedValue(result);
  render(<DesignQueuePage />);
  await userEvent.click(await screen.findByRole('button', { name: /publish/i }));
  await waitFor(() => expect(toastCalls.length).toBe(1));
  return toastCalls[0];
}

beforeEach(() => {
  toastCalls.length = 0;
  vi.clearAllMocks();
});

describe('Design Queue Publish toast', () => {
  it('says a photo already up is already on the website, with no Shopify id', async () => {
    const t = await press({
      mode: 'LIVE',
      entity: 'image',
      action: 'noop',
      ok: true,
      shopify_id: 'gid://shopify/MediaImage/100',
    });
    expect(t).toEqual({
      kind: 'success',
      msg: 'Image "Aviator Gold": already on the website, so nothing was sent.',
    });
  });

  it("shows only the backend's plain sentence when the swap is not finished", async () => {
    const error =
      'The new photo was sent, but IMS cannot see it finished on the website listing yet, ' +
      'so the old photo stays up. Press Publish again in a few minutes.';
    const t = await press({
      mode: 'LIVE',
      entity: 'image',
      action: 'update',
      ok: false,
      shopify_id: 'gid://shopify/MediaImage/101',
      error,
    });
    expect(t).toEqual({ kind: 'warning', msg: `Image "Aviator Gold": ${error}` });
  });

  it('re-reads the board after a LIVE press that did not finish', async () => {
    // The press can record a new photo while the old one stays up and still
    // answer ok=false: the board must re-read the row, not show a stale record.
    await press({
      mode: 'LIVE',
      entity: 'image',
      action: 'create',
      ok: false,
      shopify_id: 'gid://shopify/MediaImage/100',
      error: 'The new photo is on the website. The old one stays up.',
    });
    await waitFor(() => expect(imagesApi.list).toHaveBeenCalledTimes(2));
  });
});
