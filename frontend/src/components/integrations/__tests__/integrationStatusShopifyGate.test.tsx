// ============================================================================
// Integration status card - Shopify follows its OWN gate (audit L1-F34)
// ============================================================================
// The header badge shows the global DISPATCH_MODE. Shopify writes follow
// SHOPIFY_DISPATCH_MODE when it is set, so the two can differ in either
// direction. The card used to say, in words, that pushes only go live when
// DISPATCH_MODE=live - right next to a Shopify row reading the opposite. The
// card must (1) not state that rule for Shopify, (2) say Shopify has its own
// switch, and (3) show the gate value the Shopify row is using.

import { render, screen, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ hasRole: () => true, user: { id: 'u1', roles: ['SUPERADMIN'] } }),
}));

const getIntegrationStatus = vi.fn();
vi.mock('../../../services/api/integrations', () => ({
  getIntegrationStatus: () => getIntegrationStatus(),
}));

import { IntegrationStatusCard } from '../IntegrationStatusCard';
import type {
  IntegrationStatusItem,
  IntegrationStatusReport,
} from '../../../services/api/integrations';

const row = (over: Partial<IntegrationStatusItem>): IntegrationStatusItem => ({
  id: 'x',
  label: 'X',
  powers: '',
  source: 'env',
  dispatch_gated: true,
  configured: true,
  state: 'simulated',
  env_keys: [],
  collection: null,
  notes: '',
  ...over,
});

const report = (globalMode: string, shopifyMode: string): IntegrationStatusReport => ({
  generated_at: '2026-11-11T00:00:00Z',
  dispatch_mode: globalMode,
  test_phone_set: false,
  summary: { total: 2, configured: 2, live: 1 },
  integrations: [
    row({ id: 'msg91_whatsapp', label: 'WhatsApp (MSG91)', state: globalMode === 'live' ? 'live' : 'simulated' }),
    row({
      id: 'shopify',
      label: 'Shopify',
      state: shopifyMode === 'live' ? 'live' : 'simulated',
      gate: {
        label: 'Shopify gate',
        mode: shopifyMode,
        rule: 'SHOPIFY_DISPATCH_MODE when it is set, otherwise DISPATCH_MODE',
      },
    }),
  ],
});

describe('IntegrationStatusCard - Shopify gate', () => {
  beforeEach(() => getIntegrationStatus.mockReset());

  it.each([
    ['off', 'live'],
    ['live', 'off'],
  ])('global %s + Shopify %s: the card does not contradict the Shopify row', async (globalMode, shopifyMode) => {
    getIntegrationStatus.mockResolvedValue(report(globalMode, shopifyMode));
    render(<IntegrationStatusCard />);

    // The header still shows the global gate...
    expect(await screen.findByText(`DISPATCH_MODE: ${globalMode}`)).toBeInTheDocument();
    // ...the Shopify row shows the gate IT is using...
    const shopify = screen.getByTestId('integration-row-shopify');
    expect(within(shopify).getByText(`Shopify gate: ${shopifyMode}`)).toBeInTheDocument();
    // ...rows on the global gate show no gate of their own...
    const whatsapp = screen.getByTestId('integration-row-msg91_whatsapp');
    expect(within(whatsapp).queryByText(/gate:/i)).toBeNull();
    // ...and the card's own words name Shopify's switch instead of claiming
    // DISPATCH_MODE decides every push.
    expect(screen.getByText(/Shopify follows its own switch/i)).toBeInTheDocument();
    expect(screen.queryByText(/pushes only go live when DISPATCH_MODE=live/i)).toBeNull();
    expect(screen.getByText(`DISPATCH_MODE: ${globalMode}`).getAttribute('title')).toMatch(/Shopify/);
  });
});
