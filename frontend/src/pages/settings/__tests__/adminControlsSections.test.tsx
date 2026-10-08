// ============================================================================
// Settings > Operational Rules after the owner rulings of 2026-10-08
// ============================================================================
// Store Modules, Role Permissions and Discount Limits are gone (each duplicated
// a rule IMS enforces elsewhere), and so is every operational rule except the
// default credit limit (live) and round-off (left for its own job). Every test
// drives the REAL settingsRoutes table, so the URL-to-section mapping and the
// role gates under test are the ones the app ships. Fixtures use non-default
// values so a page rendering its code defaults fails here.

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

let currentRoles: string[] = [];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', roles: currentRoles, activeRole: currentRoles[0], activeStoreId: 'ZZ-S1' },
    isAuthenticated: true,
    isLoading: false,
    // Real ProtectedRoute semantics: SUPERADMIN/ADMIN pass every gate.
    hasRole: (roles: string[]) =>
      currentRoles.includes('SUPERADMIN') || currentRoles.includes('ADMIN')
      || roles.some((r) => currentRoles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

const mockGetAdminControls = vi.fn();
const mockUpdateAdminControls = vi.fn();

vi.mock('../../../services/api/settings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/settings')>();
  return {
    ...actual,
    settingsApi: {
      ...actual.settingsApi,
      getAdminControls: (...a: unknown[]) => mockGetAdminControls(...a),
      updateAdminControls: (...a: unknown[]) => mockUpdateAdminControls(...a),
    },
  };
});

import { ToastProvider } from '../../../context/ToastContext';
import { settingsRoutes } from '../../../routes/settingsRoutes';
import { SETTINGS_SECTIONS } from '../settingsSections';

// settingsRoutes lazy-loads the layout and the section chunk, compiled on
// demand under vitest - the same allowance as the other real-route tests.
const FIND = { timeout: 20000 };
vi.setConfig({ testTimeout: 30000 });

const STORED = { operational_rules: { auto_round_off: false, default_credit_limit: 90000 } };

beforeEach(() => {
  currentRoles = [];
  mockGetAdminControls.mockReset().mockResolvedValue(STORED);
  mockUpdateAdminControls.mockReset().mockResolvedValue({});
});

function renderRoute(path: string, roles: string[]) {
  currentRoles = roles;
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[path]}>
        <Suspense fallback={null}>
          <Routes>
            {settingsRoutes}
            <Route path="/unauthorized" element={<div>ZZ-DENIED</div>} />
            <Route path="*" element={<div>ZZ-NO-SUCH-PAGE</div>} />
          </Routes>
        </Suspense>
      </MemoryRouter>
    </ToastProvider>,
  );
}

/** Click Save and return the ONE payload it sent. */
async function save() {
  fireEvent.click(screen.getByRole('button', { name: /^Save Operational Rules$/ }));
  await waitFor(() => expect(mockUpdateAdminControls).toHaveBeenCalledTimes(1));
  return mockUpdateAdminControls.mock.calls[0][0] as Record<string, unknown>;
}

const REMOVED = ['modules', 'permissions', 'discount-caps'];
const REMOVED_LABELS = [/^Store Modules$/, /^Role Permissions$/, /^Discount Limits$/];
const REMOVED_RULES = [
  'Require Customer for All Sales', 'Credit Above Limit Needs Approval', 'Allow Negative Stock Billing',
  'Low Stock Alert Threshold', 'Auto-Generate Reorder POs', 'Geo-fence Radius (meters)',
  'Late Arrival Threshold (minutes)', 'Require Rx for Lens Orders', 'Prescription Validity (days)',
  'Session Timeout (minutes)', 'Force Password Change (days)', 'Require 2FA for Admin Roles',
];

describe('/settings/rules: the default credit limit and round-off, nothing else', () => {
  it.each(['SUPERADMIN', 'ADMIN'])('%s sees the stored values and saves exactly the two rules', async (role) => {
    renderRoute('/settings/rules', [role]);
    expect(await screen.findByDisplayValue('90000', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Auto Round-off/ })).toHaveAttribute('aria-pressed', 'false');
    expect(await screen.findByRole('link', { name: /^Operational Rules$/ }, FIND)).toHaveAttribute('aria-current', 'page');
    expect(await save()).toEqual({ operational_rules: { auto_round_off: false, default_credit_limit: 90000 } });
  });

  it('an edited default credit limit is what is saved', async () => {
    renderRoute('/settings/rules', ['ADMIN']);
    fireEvent.change(await screen.findByDisplayValue('90000', undefined, FIND), { target: { value: '120000' } });
    expect(await save()).toEqual({ operational_rules: { auto_round_off: false, default_credit_limit: 120000 } });
  });

  it('none of the removed rules is shown', async () => {
    renderRoute('/settings/rules', ['SUPERADMIN']);
    await screen.findByDisplayValue('90000', undefined, FIND);
    for (const label of REMOVED_RULES) expect(screen.queryByText(label)).toBeNull();
  });

  it('STORE_MANAGER is refused', async () => {
    renderRoute('/settings/rules', ['STORE_MANAGER']);
    expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
  });

  it('a legacy /settings?tab=rules link lands on the rules page', async () => {
    renderRoute('/settings?tab=rules', ['SUPERADMIN']);
    expect(await screen.findByDisplayValue('90000', undefined, FIND)).toBeInTheDocument();
  });

  it('a failed load is shown, not swallowed', async () => {
    mockGetAdminControls.mockReset().mockRejectedValue(new Error('403'));
    renderRoute('/settings/rules', ['SUPERADMIN']);
    expect(await screen.findByRole('alert', undefined, FIND)).toHaveTextContent(/Could not load the saved settings/);
  });
});

describe('the removed screens are gone from routes and nav', () => {
  it('no settings section is left for them', () => {
    const ids = SETTINGS_SECTIONS.map((s) => s.id as string);
    for (const id of REMOVED) expect(ids).not.toContain(id);
  });

  it.each(REMOVED)('/settings/%s is no page at all', async (section) => {
    renderRoute(`/settings/${section}`, ['SUPERADMIN']);
    expect(await screen.findByText('ZZ-NO-SUCH-PAGE', undefined, FIND)).toBeInTheDocument();
    expect(mockGetAdminControls).not.toHaveBeenCalled();
  });

  it('a legacy ?tab= link to a removed screen lands on My Profile', async () => {
    renderRoute('/settings?tab=discount-caps', ['SUPERADMIN']);
    expect(await screen.findByRole('link', { name: /^My Profile$/ }, FIND)).toHaveAttribute('aria-current', 'page');
  });

  it.each(['SUPERADMIN', 'ADMIN'])('%s: the rail shows Operational Rules and none of the removed three', async (role) => {
    renderRoute('/settings/system', [role]);
    await screen.findByRole('link', { name: /^System$/ }, FIND);
    const rail = within(screen.getByRole('navigation'));
    expect(rail.getByRole('link', { name: /^Operational Rules$/ })).toBeInTheDocument();
    for (const label of REMOVED_LABELS) expect(screen.queryByRole('link', { name: label })).toBeNull();
  });

  it('STORE_MANAGER sees neither Operational Rules nor System', async () => {
    renderRoute('/settings/profile', ['STORE_MANAGER']);
    await screen.findByRole('link', { name: /^My Profile$/ }, FIND);
    expect(screen.queryByRole('link', { name: /^Operational Rules$/ })).toBeNull();
    expect(screen.queryByRole('link', { name: /^System$/ })).toBeNull();
  });

  it.each(['SUPERADMIN', 'ADMIN'])('%s: /settings/system links only to Operational Rules and loads nothing', async (role) => {
    renderRoute('/settings/system', [role]);
    const row = await screen.findByTestId('admin-controls-links', undefined, FIND);
    const links = Array.from(row.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(links).toEqual(['/settings/rules']);
    expect(mockGetAdminControls).not.toHaveBeenCalled();
  });

  it('Feature Toggles no longer lists the eye-test and workshop switches', async () => {
    renderRoute('/settings/feature-toggles', ['SUPERADMIN']);
    expect(await screen.findByText('POS Quick Sale', undefined, FIND)).toBeInTheDocument();
    expect(screen.queryByText('Eye Test Module')).toBeNull();
    expect(screen.queryByText('Workshop Module')).toBeNull();
  });

  // Every remaining ['SUPERADMIN']-only row is strict.
  it.each(['agents', 'feature-toggles', 'shopify-live-sync'])('ADMIN is refused /settings/%s, SUPERADMIN is admitted', async (section) => {
    const denied = renderRoute(`/settings/${section}`, ['ADMIN']);
    expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
    denied.unmount();
    renderRoute(`/settings/${section}`, ['SUPERADMIN']);
    await waitFor(() => expect(screen.queryByText('ZZ-DENIED')).toBeNull(), FIND);
    expect(await screen.findByRole('link', { name: /^System$/ }, FIND)).toBeInTheDocument();
  });
});

describe('unsaved edits are guarded', () => {
  it('leaving with an unsaved edit asks first; cancel keeps you on the page', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderRoute('/settings/rules', ['SUPERADMIN']);
    fireEvent.change(await screen.findByDisplayValue('90000', undefined, FIND), { target: { value: '95000' } });
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: /^System$/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByDisplayValue('95000')).toBeInTheDocument(); // still here, edit intact
    confirmSpy.mockRestore();
  });

  it('no prompt before any edit, and none after a successful save', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderRoute('/settings/rules', ['SUPERADMIN']);
    fireEvent.change(await screen.findByDisplayValue('90000', undefined, FIND), { target: { value: '95000' } });
    await save();
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).toBeNull());
    fireEvent.click(screen.getByRole('link', { name: /^System$/ }));
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('a failed save keeps the page dirty', async () => {
    mockUpdateAdminControls.mockReset().mockRejectedValue(new Error('403'));
    renderRoute('/settings/rules', ['SUPERADMIN']);
    fireEvent.change(await screen.findByDisplayValue('90000', undefined, FIND), { target: { value: '95000' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save Operational Rules$/ }));
    await waitFor(() => expect(mockUpdateAdminControls).toHaveBeenCalled());
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });

  it('an edit made while a save is in flight stays dirty', async () => {
    let finish!: () => void;
    mockUpdateAdminControls.mockReset().mockReturnValue(new Promise<void>((r) => { finish = () => r(); }));
    renderRoute('/settings/rules', ['SUPERADMIN']);
    const input = await screen.findByDisplayValue('90000', undefined, FIND);
    fireEvent.change(input, { target: { value: '95000' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save Operational Rules$/ }));
    await waitFor(() => expect(mockUpdateAdminControls).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByDisplayValue('95000'), { target: { value: '96000' } }); // mid-flight
    finish();
    await waitFor(() => expect(screen.getByRole('button', { name: /^Save Operational Rules$/ })).not.toBeDisabled());
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });

  it('does not prompt for a link to the current page, mailto: or tel:', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderRoute('/settings/rules', ['SUPERADMIN']);
    fireEvent.change(await screen.findByDisplayValue('90000', undefined, FIND), { target: { value: '95000' } });
    const links = ['/', 'mailto:a@b.co', 'tel:+911234567890'].map((href) => {
      const a = document.createElement('a');
      a.href = href === '/' ? window.location.pathname : href;
      a.textContent = href;
      document.body.appendChild(a);
      return a;
    });
    // links to ANOTHER page that still must not prompt: new tab, download
    const blank = document.createElement('a');
    blank.href = '/settings/roles'; blank.target = '_blank'; blank.textContent = 'blank';
    const dl = document.createElement('a');
    dl.href = '/settings/roles'; dl.setAttribute('download', 'x.csv'); dl.textContent = 'dl';
    const other = document.createElement('a');
    other.href = '/settings/roles'; other.textContent = 'other';
    for (const a of [blank, dl, other]) { document.body.appendChild(a); links.push(a); }
    // jsdom does not navigate on click; a prevented default would show a prompt.
    for (const a of [...links.slice(0, 3), blank, dl]) fireEvent.click(a);
    // ctrl / cmd / shift / alt-click and middle-click open elsewhere, no prompt
    for (const init of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
      fireEvent.click(other, init);
    }
    expect(confirmSpy).not.toHaveBeenCalled();
    // control: a link to another page still prompts
    fireEvent.click(screen.getByRole('link', { name: /^System$/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    links.forEach((a) => a.remove());
    confirmSpy.mockRestore();
  });

  it('beforeunload is cancelled while dirty; no listener after a save or after unmount', async () => {
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const fire = () => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; };
    const view = renderRoute('/settings/rules', ['SUPERADMIN']);
    const input = await screen.findByDisplayValue('90000', undefined, FIND);
    expect(fire()).toBe(false); // clean: no prompt
    fireEvent.change(input, { target: { value: '95000' } });
    expect(fire()).toBe(true); // dirty: cancelled
    await save();
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).toBeNull());
    expect(fire()).toBe(false); // listener gone after the save
    fireEvent.change(screen.getByDisplayValue('95000'), { target: { value: '97000' } });
    expect(fire()).toBe(true);
    view.unmount();
    expect(fire()).toBe(false); // none left after unmount
    const live = (spy: typeof add) => spy.mock.calls.filter((c) => c[0] === 'beforeunload').length;
    expect(live(add)).toBe(live(remove)); // every add was removed
    add.mockRestore();
    remove.mockRestore();
  });
});
