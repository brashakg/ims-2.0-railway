// ============================================================================
// The DARK banner must not drop a blocker just because its value is falsy
// ============================================================================
// darkReasons listed "dispatch mode is X (needs live)" only when
// `m.dispatch_mode` was truthy -- so DISPATCH_MODE="" (the variable exists but
// is empty, which push_mode_status reports verbatim as "") made the banner
// silently omit the not-live blocker: a false all-clear on the exact screen
// whose job is to say why pushes are dark.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../../services/api/onlineStore', () => ({
  pushApi: {
    getStatus: vi.fn(),
  },
}));

import OnlineStoreSyncBanner from '../OnlineStoreSyncBanner';
import { pushApi } from '../../../services/api/onlineStore';

function darkStatus(dispatch_mode: unknown) {
  return {
    mode: {
      mode: 'DARK',
      is_live: false,
      writes_enabled: true,
      dispatch_mode,
      creds_present: true,
    },
    db_connected: true,
    counts: {
      products: { staged: 0, pushed: 0, pending: 0 },
      collections: { total: 0, pushed: 0, pending: 0 },
      menus: { total: 0, pushed: 0, pending: 0 },
      images: { approved: 0, pushed: 0, pending: 0 },
    },
    status_reason: null,
  };
}

async function renderDark(dispatch_mode: unknown) {
  (pushApi.getStatus as ReturnType<typeof vi.fn>).mockResolvedValue(
    darkStatus(dispatch_mode),
  );
  render(<OnlineStoreSyncBanner />);
  await waitFor(() => expect(pushApi.getStatus).toHaveBeenCalled());
  await screen.findByText(/Shopify writes OFF/i);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('OnlineStoreSyncBanner dark reasons', () => {
  it('still lists the not-live blocker when dispatch_mode is an empty string', async () => {
    await renderDark('');

    expect(
      await screen.findByText(/dispatch mode is not reported \(needs "live"\)/i),
    ).toBeInTheDocument();
  });

  it('still lists the not-live blocker when dispatch_mode is missing entirely', async () => {
    await renderDark(undefined);

    expect(
      await screen.findByText(/dispatch mode is not reported \(needs "live"\)/i),
    ).toBeInTheDocument();
  });

  it('names the mode when the server reports a real not-live value', async () => {
    await renderDark('off');

    expect(
      await screen.findByText(/dispatch mode is "off" \(needs "live"\)/i),
    ).toBeInTheDocument();
  });
});
