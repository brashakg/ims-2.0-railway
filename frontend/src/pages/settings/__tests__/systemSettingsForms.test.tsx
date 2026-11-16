// ============================================================================
// Settings > System forms (audit L1-F15, L1-F16, and the shared load guard)
// ============================================================================
// L1-F15: choosing "Custom..." for the auto-logout time snapped straight back
//         to the saved preset and no custom box appeared.
// L1-F16: `25.9,bogus,50xyz` was saved as [25, 50] (parseInt cut each token
//         short and the rest were dropped) with a success message.
// Guard:  a failed read left code defaults on screen with Save enabled, on
//         all three cards (auto-logout, target ticker, website page types).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { unlabelledControls } from '../../../test/unlabelledControls';

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ hasRole: () => true }) }));
vi.mock('react-router-dom', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => <a href={to}>{children}</a>,
}));

const getSystemSettings = vi.fn();
const updateSystemSettings = vi.fn();
const getAllPolicies = vi.fn();
const updateTickerSettings = vi.fn();
vi.mock('../../../services/api/settings', () => ({
  settingsApi: {
    getSystemSettings: () => getSystemSettings(),
    updateSystemSettings: (s: Record<string, unknown>) => updateSystemSettings(s),
  },
}));
vi.mock('../../../services/api', () => ({
  adminSystemApi: { getSystemStatus: vi.fn(), createBackup: vi.fn() },
  policiesApi: { getAll: (...a: unknown[]) => getAllPolicies(...a) },
}));
vi.mock('../../../services/api/finance', () => ({
  financeApi: { updateTickerSettings: (p: unknown) => updateTickerSettings(p) },
}));

import { AutoLogoutSettings } from '../../../components/settings/AutoLogoutSettings';
import { TargetTickerSettings, WebPageTypesSetting } from '../SettingsSystem';

const STORED = { auto_logout_enabled: true, auto_logout_minutes: 15, auto_logout_warn_seconds: 60, web_page_types_enabled: true };
const TICKER = {
  policies: {
    'ticker.milestone_pcts': { value: [10, 40, 90] },
    'ticker.refresh_seconds': { value: 120 },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  getSystemSettings.mockResolvedValue({ ...STORED });
  updateSystemSettings.mockImplementation(async (s) => ({ settings: s }));
  getAllPolicies.mockResolvedValue(TICKER);
  updateTickerSettings.mockImplementation(async (p: { milestone_pcts: number[]; refresh_seconds: number }) => ({ ...p, saved: true }));
});

describe('Auto-logout: Custom interval (L1-F15)', () => {
  const minutesSelect = () => screen.getByLabelText('Log out after (minutes)') as HTMLSelectElement;
  const ready = async () => {
    render(<AutoLogoutSettings />);
    const save = await screen.findByRole('button', { name: /save auto-logout settings/i });
    await waitFor(() => expect(save).toBeEnabled());
    return save;
  };

  it('choosing Custom shows an editable box, and a custom value is saved', async () => {
    const save = await ready();
    expect(minutesSelect()).toHaveValue('15');
    expect(screen.queryByLabelText('Custom log-out time in minutes')).toBeNull();

    fireEvent.change(minutesSelect(), { target: { value: 'custom' } });
    expect(minutesSelect()).toHaveValue('custom'); // used to snap back to 15
    const box = screen.getByLabelText('Custom log-out time in minutes');
    expect(box).toHaveValue(15);

    fireEvent.change(box, { target: { value: '' } }); // can be cleared to retype
    fireEvent.change(box, { target: { value: '47' } });
    fireEvent.click(save);
    await waitFor(() => expect(updateSystemSettings).toHaveBeenCalledTimes(1));
    expect(updateSystemSettings).toHaveBeenCalledWith({
      auto_logout_enabled: true, auto_logout_minutes: 47, auto_logout_warn_seconds: 60,
    });
    expect(minutesSelect()).toHaveValue('custom');
    expect(screen.getByLabelText('Custom log-out time in minutes')).toHaveValue(47);
  });

  it('a stored custom value reloads as Custom, and switching back to a preset works', async () => {
    getSystemSettings.mockResolvedValue({ ...STORED, auto_logout_minutes: 47 });
    const save = await ready();
    expect(minutesSelect()).toHaveValue('custom');
    expect(screen.getByLabelText('Custom log-out time in minutes')).toHaveValue(47);

    fireEvent.change(minutesSelect(), { target: { value: '30' } });
    expect(minutesSelect()).toHaveValue('30');
    expect(screen.queryByLabelText('Custom log-out time in minutes')).toBeNull();
    fireEvent.click(save);
    await waitFor(() => expect(updateSystemSettings).toHaveBeenCalledTimes(1));
    expect(updateSystemSettings.mock.calls[0][0].auto_logout_minutes).toBe(30);
  });

  it.each([['0'], ['481'], ['']])('refuses the custom value "%s" (1-480 only)', async (bad) => {
    const save = await ready();
    fireEvent.change(minutesSelect(), { target: { value: 'custom' } });
    fireEvent.change(screen.getByLabelText('Custom log-out time in minutes'), { target: { value: bad } });
    fireEvent.click(save);
    expect(toast.error).toHaveBeenCalledWith('Log-out time must be between 1 and 480 minutes');
    expect(updateSystemSettings).not.toHaveBeenCalled();
  });

  it('the warning must stay shorter than the log-out time', async () => {
    const save = await ready();
    fireEvent.change(minutesSelect(), { target: { value: 'custom' } });
    fireEvent.change(screen.getByLabelText('Custom log-out time in minutes'), { target: { value: '1' } });
    fireEvent.click(save); // 60 s warning is not shorter than 1 minute
    expect(toast.error).toHaveBeenCalledWith('Warning time must be shorter than the log-out time');
    expect(updateSystemSettings).not.toHaveBeenCalled();
  });

  it('a failed read locks the card: no defaults can be saved; Retry loads the stored policy', async () => {
    getSystemSettings.mockRejectedValueOnce(new Error('500'));
    getSystemSettings.mockResolvedValue({ auto_logout_enabled: false, auto_logout_minutes: 30, auto_logout_warn_seconds: 45 });
    render(<AutoLogoutSettings />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/could not load the saved auto-logout settings/i);
    const save = screen.getByRole('button', { name: /save auto-logout settings/i });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(updateSystemSettings).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(save).toBeEnabled());
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('checkbox', { name: /enable idle auto-logout/i })).not.toBeChecked();
    expect(minutesSelect()).toHaveValue('30');
    expect(screen.getByLabelText('Warn before (seconds)')).toHaveValue(45);
    expect(unlabelledControls()).toEqual([]);
  });
});

describe('Target ticker: strict thresholds (L1-F16)', () => {
  const ready = async () => {
    render(<TargetTickerSettings />);
    const save = await screen.findByRole('button', { name: /save target ticker settings/i });
    await waitFor(() => expect(save).toBeEnabled());
    return save;
  };
  const box = () => screen.getByLabelText('Milestone thresholds (%)');

  it.each([
    ['25.9,bogus,50xyz'],
    ['25.9'],
    ['25,,50'],
    ['25,50xyz'],
    ['0,50'],
    ['50,101'],
    ['-5,50'],
    ['25 50'],
    [''],
  ])('refuses "%s", saves nothing and keeps the text as typed', async (typed) => {
    const save = await ready();
    fireEvent.change(box(), { target: { value: typed } });
    fireEvent.click(save);

    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error.mock.calls[0][0]).toMatch(/whole numbers between 1 and 100/i);
    expect(updateTickerSettings).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(box()).toHaveValue(typed);
  });

  it('saves an explicitly valid list exactly as typed', async () => {
    const save = await ready();
    expect(box()).toHaveValue('10,40,90'); // the stored list, not 25,50,75,100
    fireEvent.change(box(), { target: { value: ' 25, 50 ,100' } });
    fireEvent.click(save);
    await waitFor(() => expect(updateTickerSettings).toHaveBeenCalledTimes(1));
    expect(updateTickerSettings).toHaveBeenCalledWith({ milestone_pcts: [25, 50, 100], refresh_seconds: 120 });
    expect(toast.success).toHaveBeenCalled();
  });

  it('a failed read locks the card instead of offering the defaults to save', async () => {
    getAllPolicies.mockRejectedValueOnce(new Error('500'));
    render(<TargetTickerSettings />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/could not load the saved target ticker settings/i);
    const save = screen.getByRole('button', { name: /save target ticker settings/i });
    expect(save).toBeDisabled();
    expect(box()).toBeDisabled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(save).toBeEnabled());
    expect(box()).toHaveValue('10,40,90');
    expect(screen.getByLabelText('Refresh every (seconds)')).toHaveValue(120);
    expect(unlabelledControls()).toEqual([]);
  });
});

describe('Website page types: a failed read is not "off"', () => {
  it('locks the box and offers Retry', async () => {
    getSystemSettings.mockRejectedValueOnce(new Error('500'));
    render(<WebPageTypesSetting />);
    const alert = await screen.findByRole('alert');
    const boxEl = screen.getByRole('checkbox', { name: /pre-order and preview only pages/i });
    expect(boxEl).toBeDisabled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(boxEl).toBeEnabled());
    expect(boxEl).toBeChecked(); // the stored value (on), never a guessed "off"
    expect(updateSystemSettings).not.toHaveBeenCalled();
  });
});
