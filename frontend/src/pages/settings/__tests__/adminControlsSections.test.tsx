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
    renderRoute('/settings/modules', ['ADMIN']);
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
    renderRoute('/settings/permissions', ['ADMIN']);
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
    renderRoute('/settings/discount-caps', ['ADMIN']);
    expect(await screen.findByText('ZZ Floor Staff', undefined, FIND)).toBeInTheDocument();
    expect(screen.getByDisplayValue('7')).toBeInTheDocument();
    await expectActiveLink(/^Discount Limits$/);
    const payload = await save(/^Save Discount Limits$/);
    expect(payload).toEqual({ discount_limits: [DISCOUNT_ROW] });
  });

  it('rules: the rule list by category with the stored values, inert security rules hidden', async () => {
    renderRoute('/settings/rules', ['ADMIN']);
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

describe('the gates are the System page\'s: SUPERADMIN + ADMIN only', () => {
  it.each(['modules', 'permissions', 'discount-caps', 'rules'])(
    'STORE_MANAGER is refused /settings/%s',
    async (section) => {
      renderRoute(`/settings/${section}`, ['STORE_MANAGER']);
      expect(await screen.findByText('ZZ-DENIED', undefined, FIND)).toBeInTheDocument();
    },
  );

  it('a legacy /settings?tab=rules link lands on the rules section', async () => {
    renderRoute('/settings?tab=rules', ['SUPERADMIN']);
    expect(await screen.findByText('Low Stock Alert Threshold', undefined, FIND)).toBeInTheDocument();
  });
});
