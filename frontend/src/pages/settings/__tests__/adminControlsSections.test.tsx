// ============================================================================
// Wave 6 B21 - the four admin editors at their own URLs: one smoke test per
// section, the route gates, and the legacy ?tab= door
// ============================================================================
// The old AdminControlPanel hid store modules / role permissions / discount
// limits / operational rules behind a second tab layer on /settings/system
// and had no test. Every test drives the REAL settingsRoutes table, so the
// URL-to-section mapping and the per-section role gate under test are the
// ones the app ships. Fixtures use non-default values so a section rendering
// its code defaults (its load wired to the wrong key) fails here.

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
const mockGetStores = vi.fn();

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

vi.mock('../../../services/api/stores', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/api/stores')>();
  return {
    ...actual,
    adminStoreApi: { ...actual.adminStoreApi, getStores: (...a: unknown[]) => mockGetStores(...a) },
  };
});

import { ToastProvider } from '../../../context/ToastContext';
import { settingsRoutes } from '../../../routes/settingsRoutes';

// settingsRoutes lazy-loads the layout and the section chunk, compiled on
// demand under vitest - the same allowance as the other real-route tests.
const FIND = { timeout: 20000 };
vi.setConfig({ testTimeout: 30000 });

const DISCOUNT_ROW = { roleId: 'SALES_STAFF', roleName: 'ZZ Floor Staff', maxDiscountPercent: 7, requiresApproval: true, approvalThreshold: 3 };

beforeEach(() => {
  currentRoles = [];
  mockGetStores.mockReset().mockResolvedValue({ stores: [{ store_id: 'ZZ-S1', store_name: 'ZZ Ranchi Main' }] });
  mockGetAdminControls.mockReset().mockResolvedValue({
    store_modules: { 'ZZ-S1': { pos: false, clinical: true } },
    role_permissions: { STORE_MANAGER: { void_orders: true } },
    discount_limits: [DISCOUNT_ROW],
    operational_rules: { low_stock_threshold: 42 },
  });
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
          </Routes>
        </Suspense>
      </MemoryRouter>
    </ToastProvider>,
  );
}

/** The section's own rail link is the active one - one URL per section. */
async function expectActiveLink(name: RegExp) {
  expect(await screen.findByRole('link', { name }, FIND)).toHaveAttribute('aria-current', 'page');
}

/** Click the section's Save and return the ONE payload it sent. */
async function save(label: RegExp) {
  fireEvent.click(screen.getByRole('button', { name: label }));
  await waitFor(() => expect(mockUpdateAdminControls).toHaveBeenCalledTimes(1));
  return mockUpdateAdminControls.mock.calls[0][0] as Record<string, unknown>;
}

describe('each admin editor renders at its own URL and saves only its own key', () => {
  it('modules: the store x module grid', async () => {
    renderRoute('/settings/modules', ['SUPERADMIN']);
    expect(await screen.findByText('ZZ Ranchi Main', undefined, FIND)).toBeInTheDocument();
    expect(mockGetStores).toHaveBeenCalledTimes(1);
    await expectActiveLink(/^Store Modules$/);
    const payload = await save(/^Save Store Modules$/);
    expect(Object.keys(payload)).toEqual(['store_modules']);
    // The stored row, not the all-on default (both mocks are pre-resolved, so
    // the store list lands first and the stored modules are merged over it).
    expect(payload.store_modules).toEqual({ 'ZZ-S1': { pos: false, clinical: true } });
  });

  it('permissions: the permission x role matrix with the stored grants', async () => {
    renderRoute('/settings/permissions', ['SUPERADMIN']);
    expect(await screen.findByText('void orders', undefined, FIND)).toBeInTheDocument();
    // Defaults light 16 permissions x {SUPERADMIN, ADMIN} = 32 dots; the stored
    // STORE_MANAGER void_orders grant is the 33rd. Wait for it before saving.
    await waitFor(() => expect(screen.getAllByTitle('Enabled - click to disable')).toHaveLength(33), FIND);
    await expectActiveLink(/^Role Permissions$/);
    const payload = await save(/^Save Role Permissions$/);
    expect(Object.keys(payload)).toEqual(['role_permissions']);
    const grants = payload.role_permissions as Record<string, Record<string, boolean>>;
    expect(grants.STORE_MANAGER).toEqual({ void_orders: true }); // the stored row, not the default
    expect(grants.SUPERADMIN.create_orders).toBe(true);
  });

  it('discount-caps: the per-role cap cards with the stored values', async () => {
    renderRoute('/settings/discount-caps', ['SUPERADMIN']);
    expect(await screen.findByText('ZZ Floor Staff', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByDisplayValue('7')).toBeInTheDocument();
    await expectActiveLink(/^Discount Limits$/);
    const payload = await save(/^Save Discount Limits$/);
    expect(payload).toEqual({ discount_limits: [DISCOUNT_ROW] });
  });

  it('rules: the rule list by category with the stored values, inert security rules hidden', async () => {
    renderRoute('/settings/rules', ['SUPERADMIN']);
    // The labels are code defaults and paint first; the stored 42 is the load.
    expect(await screen.findByDisplayValue('42', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByText('Low Stock Alert Threshold')).toBeInTheDocument();
    expect(screen.getByText('Billing Rules')).toBeInTheDocument();
    expect(screen.queryByText('Session Timeout (minutes)')).toBeNull();
    await expectActiveLink(/^Operational Rules$/);
    const payload = await save(/^Save Operational Rules$/);
    expect(Object.keys(payload)).toEqual(['operational_rules']);
    expect((payload.operational_rules as Record<string, unknown>).low_stock_threshold).toBe(42);
  });
});

const FOUR = ['modules', 'permissions', 'discount-caps', 'rules'];
const FOUR_LABELS = [/^Store Modules$/, /^Role Permissions$/, /^Discount Limits$/, /^Operational Rules$/];

describe('the gates: SUPERADMIN only, matching the backend', () => {
  it.each(FOUR.flatMap((section) => [['STORE_MANAGER', section], ['ADMIN', section]]))(
    '%s is refused /settings/%s',
    async (role, section) => {
      renderRoute(`/settings/${section}`, [role]);
      expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
    },
  );

  // Every ['SUPERADMIN']-only row is strict, not just the four new ones.
  it.each(['agents', 'feature-toggles', 'shopify-live-sync'])('ADMIN is refused /settings/%s, SUPERADMIN is admitted', async (section) => {
    const denied = renderRoute(`/settings/${section}`, ['ADMIN']);
    expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
    denied.unmount();
    renderRoute(`/settings/${section}`, ['SUPERADMIN']);
    await waitFor(() => expect(screen.queryByText('ZZ-DENIED')).toBeNull(), FIND);
    expect(await screen.findByRole('link', { name: /^System$/ }, FIND)).toBeInTheDocument();
  });

  it('a legacy /settings?tab=rules link lands on the rules section', async () => {
    renderRoute('/settings?tab=rules', ['SUPERADMIN']);
    expect(await screen.findByText('Low Stock Alert Threshold', undefined, FIND)).toBeInTheDocument();
  });
});

describe('rail visibility', () => {
  it('SUPERADMIN sees all four admin-control entries', async () => {
    renderRoute('/settings/system', ['SUPERADMIN']);
    await screen.findByRole('link', { name: /^System$/ }, FIND);
    for (const label of FOUR_LABELS) expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
  });

  it('ADMIN sees System but none of the four', async () => {
    renderRoute('/settings/system', ['ADMIN']);
    await screen.findByRole('link', { name: /^System$/ }, FIND);
    for (const label of FOUR_LABELS) expect(screen.queryByRole('link', { name: label })).toBeNull();
  });

  it('STORE_MANAGER sees none of the four (nor System)', async () => {
    renderRoute('/settings/profile', ['STORE_MANAGER']);
    await screen.findByRole('link', { name: /^My Profile$/ }, FIND);
    for (const label of FOUR_LABELS) expect(screen.queryByRole('link', { name: label })).toBeNull();
    expect(screen.queryByRole('link', { name: /^System$/ })).toBeNull();
  });
});

describe('/settings/system no longer mounts the panel', () => {
  it('SUPERADMIN: no editor and no load of admin-controls, just a link row to the four pages', async () => {
    renderRoute('/settings/system', ['SUPERADMIN']);
    const row = await screen.findByTestId('admin-controls-links', undefined, FIND);
    expect(row.querySelectorAll('a')).toHaveLength(4);
    expect(row.querySelector('a[href="/settings/discount-caps"]')).not.toBeNull();
    expect(screen.queryByRole('button', { name: /^Save (Store Modules|Role Permissions|Discount Limits|Operational Rules)$/ })).toBeNull();
    expect(mockGetAdminControls).not.toHaveBeenCalled();
  });

  it('ADMIN: no link row', async () => {
    renderRoute('/settings/system', ['ADMIN']);
    // Wait for the System body itself, so the absence below is not just "not rendered yet".
    expect(await screen.findByText('Backup Database', undefined, FIND)).toBeInTheDocument();
    expect(screen.queryByTestId('admin-controls-links')).toBeNull();
  });
});

describe('a failed load is shown, not swallowed', () => {
  it.each(FOUR)('/settings/%s shows an error alert when GET admin-controls fails', async (section) => {
    mockGetAdminControls.mockReset().mockRejectedValue(new Error('403'));
    renderRoute(`/settings/${section}`, ['SUPERADMIN']);
    expect(await screen.findByRole('alert', undefined, FIND)).toHaveTextContent(/Could not load the saved settings/);
  });

  it('no alert when the load succeeds', async () => {
    renderRoute('/settings/discount-caps', ['SUPERADMIN']);
    await screen.findByText('ZZ Floor Staff', undefined, FIND);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('unsaved edits are guarded', () => {
  it('leaving with an unsaved edit asks first; cancel keeps you on the page', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderRoute('/settings/discount-caps', ['SUPERADMIN']);
    fireEvent.change(await screen.findByDisplayValue('7', undefined, FIND), { target: { value: '9' } });
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('link', { name: /^Role Permissions$/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByDisplayValue('9')).toBeInTheDocument(); // still here, edit intact
    confirmSpy.mockRestore();
  });

  it('no prompt before any edit, and none after a successful save', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderRoute('/settings/discount-caps', ['SUPERADMIN']);
    fireEvent.change(await screen.findByDisplayValue('7', undefined, FIND), { target: { value: '9' } });
    await save(/^Save Discount Limits$/);
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).toBeNull());
    fireEvent.click(screen.getByRole('link', { name: /^Role Permissions$/ }));
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('a failed save keeps the page dirty', async () => {
    mockUpdateAdminControls.mockReset().mockRejectedValue(new Error('403'));
    renderRoute('/settings/discount-caps', ['SUPERADMIN']);
    fireEvent.change(await screen.findByDisplayValue('7', undefined, FIND), { target: { value: '9' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save Discount Limits$/ }));
    await waitFor(() => expect(mockUpdateAdminControls).toHaveBeenCalled());
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });

  it('an edit made while a save is in flight stays dirty', async () => {
    let finish!: () => void;
    mockUpdateAdminControls.mockReset().mockReturnValue(new Promise<void>((r) => { finish = () => r(); }));
    renderRoute('/settings/discount-caps', ['SUPERADMIN']);
    const input = await screen.findByDisplayValue('7', undefined, FIND);
    fireEvent.change(input, { target: { value: '9' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save Discount Limits$/ }));
    await waitFor(() => expect(mockUpdateAdminControls).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByDisplayValue('9'), { target: { value: '11' } }); // mid-flight
    finish();
    await waitFor(() => expect(screen.getByRole('button', { name: /^Save Discount Limits$/ })).not.toBeDisabled());
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });

  it('does not prompt for a link to the current page, mailto: or tel:', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderRoute('/settings/discount-caps', ['SUPERADMIN']);
    fireEvent.change(await screen.findByDisplayValue('7', undefined, FIND), { target: { value: '9' } });
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
    fireEvent.click(screen.getByRole('link', { name: /^Role Permissions$/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    links.forEach((a) => a.remove());
    confirmSpy.mockRestore();
  });

  it('beforeunload is cancelled while dirty; no listener after a save or after unmount', async () => {
    const add = vi.spyOn(window, 'addEventListener');
    const remove = vi.spyOn(window, 'removeEventListener');
    const fire = () => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; };
    const view = renderRoute('/settings/discount-caps', ['SUPERADMIN']);
    const input = await screen.findByDisplayValue('7', undefined, FIND);
    expect(fire()).toBe(false); // clean: no prompt
    fireEvent.change(input, { target: { value: '9' } });
    expect(fire()).toBe(true); // dirty: cancelled
    await save(/^Save Discount Limits$/);
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).toBeNull());
    expect(fire()).toBe(false); // listener gone after the save
    fireEvent.change(screen.getByDisplayValue('9'), { target: { value: '10' } });
    expect(fire()).toBe(true);
    view.unmount();
    expect(fire()).toBe(false); // none left after unmount
    const live = (spy: typeof add) => spy.mock.calls.filter((c) => c[0] === 'beforeunload').length;
    expect(live(add)).toBe(live(remove)); // every add was removed
    add.mockRestore();
    remove.mockRestore();
  });
});
