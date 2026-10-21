// ============================================================================
// Settings > System: each save sends ONLY its own keys (review of #1244)
// ============================================================================
// The server merges a PUT /settings/system into the record. A save that sent
// the whole record read at page load wrote stale sibling keys back -- the
// auto-logout save silently re-enabled website page types (without the stock
// pass that switching them off runs). Fails if either save spreads the record.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ hasRole: () => true }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const STORED = {
  web_page_types_enabled: true,
  maintenance_mode: false,
  auto_logout_enabled: true,
  auto_logout_minutes: 15,
  auto_logout_warn_seconds: 60,
};
const getSystemSettings = vi.fn(async () => ({ ...STORED }));
const updateSystemSettings = vi.fn(async (s: Record<string, unknown>) => ({ settings: s }));
vi.mock('../../../services/api/settings', () => ({
  settingsApi: {
    getSystemSettings: () => getSystemSettings(),
    updateSystemSettings: (s: Record<string, unknown>) => updateSystemSettings(s),
  },
}));

import { AutoLogoutSettings } from '../../../components/settings/AutoLogoutSettings';
import { WebPageTypesSetting } from '../SettingsSystem';

beforeEach(() => updateSystemSettings.mockClear());

describe('Settings > System saves', () => {
  it('auto-logout sends only its three keys', async () => {
    render(<AutoLogoutSettings />);
    const save = await screen.findByRole('button', { name: /Save Auto-Logout Settings/ });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(updateSystemSettings).toHaveBeenCalledTimes(1));
    expect(Object.keys(updateSystemSettings.mock.calls[0][0]).sort()).toEqual(
      ['auto_logout_enabled', 'auto_logout_minutes', 'auto_logout_warn_seconds'],
    );
  });

  it('the website page types switch sends only its key', async () => {
    render(<WebPageTypesSetting />);
    const box = await screen.findByRole('checkbox', { name: /Pre-order and Preview only pages/ });
    await waitFor(() => expect(box).toBeChecked());
    fireEvent.click(box);
    await waitFor(() => expect(updateSystemSettings).toHaveBeenCalledTimes(1));
    expect(updateSystemSettings.mock.calls[0][0]).toEqual({ web_page_types_enabled: false });
  });
});
