// ============================================================================
// Settings > System: no "Backup Database" button that only pretends (L1-F14)
// ============================================================================
// The button confirmed "Create a full system backup?", called
// POST /admin/system/backups (which stored a timestamp) and toasted "Backup
// created successfully". No backup was ever made. The screen now offers no
// backup action and says plainly that IMS cannot make one.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const success = vi.fn();
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ hasRole: () => true }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success, error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../services/api', () => ({
  adminSystemApi: {
    getSystemStatus: async () => ({ database: 'connected', api: 'healthy', version: '2.0.0' }),
  },
  policiesApi: { getAll: async () => ({ policies: {} }) },
}));
vi.mock('../../../services/api/finance', () => ({ financeApi: { updateTickerSettings: vi.fn() } }));
vi.mock('../../../services/api/settings', () => ({
  settingsApi: { getSystemSettings: async () => ({}), updateSystemSettings: vi.fn() },
}));

import { SystemSettingsPage } from '../SettingsSystem';
import { adminSystemApi } from '../../../services/api';
import { SETTINGS_SECTIONS } from '../settingsSections';

describe('Settings > System backups', () => {
  it('offers no backup action and says a backup is not made here', async () => {
    render(
      <MemoryRouter>
        <SystemSettingsPage />
      </MemoryRouter>,
    );
    const notice = await screen.findByTestId('backup-notice');
    expect(notice).toHaveTextContent('IMS cannot back up the database from this screen');
    expect(notice).toHaveTextContent('do not assume a backup exists');

    // No control anywhere on the page starts, or claims, a backup.
    for (const control of [...screen.queryAllByRole('button'), ...screen.queryAllByRole('link')]) {
      expect(control.textContent ?? '').not.toMatch(/backup/i);
    }
    expect(screen.queryByText(/Backup Database/)).toBeNull();
    expect(screen.queryByText(/Create full system backup/)).toBeNull();
    expect(success).not.toHaveBeenCalled();
  });

  it('the API client no longer has a create-backup call', () => {
    expect(adminSystemApi).not.toHaveProperty('createBackup');
  });

  it('the Settings menu does not advertise Backup under System', () => {
    const system = SETTINGS_SECTIONS.find((s) => s.id === 'system');
    expect(system?.description ?? '').not.toMatch(/backup/i);
  });
});
