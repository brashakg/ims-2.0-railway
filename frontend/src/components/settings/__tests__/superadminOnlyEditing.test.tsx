// L1-F12 (audit 2026-11-04): an ADMIN was offered editable TDS rates and
// Loyalty settings, then every Save came back 403 "requires one of
// SUPERADMIN". The server writes (PUT /settings/tds-rates, PUT /loyalty/
// settings, PUT /settings/system) are SUPERADMIN-only, so the screens must ask
// the exact role, not hasRole() (which lets ADMIN satisfy any role list).
import { describe, it, expect, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';

let roles: string[] = [];
vi.mock('../../../context/AuthContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useAuth: () => ({
    user: { id: 'me', roles, activeRole: roles[0] },
    // The real hasRole: ADMIN / SUPERADMIN satisfy any requested role.
    hasRole: (want: string | string[]) =>
      roles.includes('SUPERADMIN') || roles.includes('ADMIN') || [want].flat().some((r) => roles.includes(r)),
  }),
}));
vi.mock('../../../context/ToastContext', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useToast: () => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../services/api/settings', () => ({
  settingsApi: {
    getTdsRates: () => Promise.resolve({ rates: { '194J': 10 }, defaults: { '194J': 10 }, overrides: {} }),
    updateTdsRates: vi.fn(),
    getSystemSettings: () => Promise.resolve({ auto_logout_enabled: true, auto_logout_minutes: 15, auto_logout_warning_seconds: 60 }),
    updateSystemSettings: vi.fn(),
  },
}));
vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: {
    getSettings: () => Promise.resolve({
      enabled: true, points_per_rupee: 0.01, category_multipliers: { FRAME: 1 }, min_order_for_earn: 0,
      expiry_days: 365, redeem_rupee_per_point: 1, min_redeem_points: 100, max_redeem_pct_of_order: 20,
      tier_thresholds: { SILVER: 0 }, tier_multipliers: { SILVER: 1 },
    }),
    updateSettings: vi.fn(),
  },
}));

import { TdsRatesSection } from '../TdsRatesSection';
import { LoyaltySettingsSection } from '../LoyaltySettings';
import { AutoLogoutSettings } from '../AutoLogoutSettings';

async function editable(node: JSX.Element, role: string) {
  roles = [role];
  const { container, unmount } = render(node);
  // Let the settings GET resolve so a 'loading' disable is not mistaken for read-only.
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
  const inputs = [...container.querySelectorAll('input')];
  const enabled = inputs.filter((i) => !i.disabled).length;
  unmount();
  return { total: inputs.length, enabled };
}

describe('L1-F12: SUPERADMIN-only settings are read-only for an ADMIN', () => {
  it.each([
    ['TDS rates', <TdsRatesSection key="t" />],
    ['Loyalty engine', <LoyaltySettingsSection key="l" />],
    ['Auto-logout', <AutoLogoutSettings key="a" />],
  ])('%s: ADMIN sees every control disabled, SUPERADMIN can edit', async (_name, node) => {
    const admin = await editable(node, 'ADMIN');
    expect(admin.total).toBeGreaterThan(0);
    expect(admin.enabled).toBe(0);
    const superadmin = await editable(node, 'SUPERADMIN');
    expect(superadmin.enabled).toBeGreaterThan(0);
  });

  it('TDS: an ADMIN gets the read-only note and no Save button', async () => {
    roles = ['ADMIN'];
    render(<TdsRatesSection />);
    expect(await screen.findByText(/only a Superadmin can edit tax rates/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Save Rates/ })).toBeNull();
  });
});
