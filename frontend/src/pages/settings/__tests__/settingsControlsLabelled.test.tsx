// ============================================================================
// Settings: every control is tied to a label and every action is a named,
// keyboard-reachable button (audit L1-F09)
// ============================================================================
// The audit's tablet census found visible inputs / selects / textareas with no
// label relationship on 16 settings routes, and Lens Master's seven edit /
// delete actions were bare 12px SVGs (no role, no tab stop, no name). This
// file re-runs that census on the generated rows and lists; the other
// settings forms run it in their own tests (settingsLoadGuard,
// systemSettingsForms, ApprovalPinStatusUnknown, HsnClearDescription).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { unlabelledControls, unnamedButtons } from '../../../test/unlabelledControls';

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'me', name: 'Owner', roles: ['SUPERADMIN'], activeRole: 'SUPERADMIN', storeIds: ['S1'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../services/api/auth', () => ({ authApi: {} }));
vi.mock('../../../components/permissions/PermissionDeltaEditor', () => ({ PermissionDeltaEditor: () => null }));
vi.mock('../../../components/permissions/UserPermissionsPanel', () => ({ UserPermissionsPanel: () => null }));

const updateLensBrand = vi.fn();
const deleteLensBrand = vi.fn();
const updatePreferences = vi.fn();
vi.mock('../../../services/api', () => ({
  adminLensApi: {
    getLensBrands: async () => ({ brands: [{ id: 'b1', name: 'Essilor' }] }),
    getLensIndices: async () => ({ indices: [{ id: 'i1', value: '1.56', name: 'Mid' }] }),
    getLensCoatings: async () => ({ coatings: [{ id: 'c1', name: 'Blue Cut', price: 500 }] }),
    getLensAddons: async () => ({ addons: [{ id: 'a1', name: 'Tint', price: 200 }] }),
    updateLensBrand: (...a: unknown[]) => updateLensBrand(...a),
    deleteLensBrand: (...a: unknown[]) => deleteLensBrand(...a),
    listLensPricingRanges: async () => ({
      ranges: [{
        range_id: 'r1', brand_id: 'b1', index_id: 'i1', category: 'SINGLE_VISION', parameter: 'sphere',
        min_value: -2, max_value: 2, base_price: 900, is_active: true,
      }],
      total: 1,
    }),
  },
  policiesApi: {
    getRegistry: async () => {
      const policies = [
        { key: 'k.bool', type: 'bool', default: false, scopes: ['global'], write_roles: ['SUPERADMIN'], group: 'G', label: 'A switch' },
        { key: 'k.money', type: 'money_paisa', default: 0, scopes: ['global'], write_roles: ['SUPERADMIN'], group: 'G', label: 'A rupee limit' },
        { key: 'k.enum', type: 'enum', default: 'a', scopes: ['global'], write_roles: ['SUPERADMIN'], group: 'G', label: 'A choice', enum: ['a', 'b'] },
        { key: 'k.text', type: 'string', default: '', scopes: ['global'], write_roles: ['SUPERADMIN'], group: 'G', label: 'A text rule' },
      ];
      return { policies, groups: { G: policies } };
    },
    getAll: async () => ({ scope: 'global', policies: {} }),
  },
  adminUserApi: { getUsers: async () => ({ users: [] }) },
  adminStoreApi: { getStores: async () => ({ stores: [] }) },
  settingsApi: {
    getProfile: async () => ({ full_name: 'Owner', email: 'o@example.com', phone: '9876543210' }),
    getPreferences: async () => ({ email_notifications: false, sms_notifications: true }),
    updatePreferences: (p: unknown) => updatePreferences(p),
  },
}));
vi.mock('../../../services/api/settings', () => ({
  settingsApi: {
    getTdsRates: async () => ({ rates: { '194C_IND': 1, '194J': 10 }, defaults: { '194C_IND': 1, '194J': 10 } }),
  },
}));
vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: {
    getSettings: async () => ({
      enabled: true, points_per_rupee: 1, category_multipliers: { FRAME: 2 }, min_order_for_earn: 0, expiry_days: 365,
      redeem_rupee_per_point: 1, min_redeem_points: 100, max_redeem_pct_of_order: 20,
      tier_thresholds: { GOLD: 5000 }, tier_multipliers: { GOLD: 1.5 },
    }),
  },
}));

import { LensMasterSection } from '../SettingsLens';
import { UserManagementSection } from '../SettingsAuth';
import { ProfileSection } from '../SettingsProfile';
import { TdsRatesSection } from '../../../components/settings/TdsRatesSection';
import { LoyaltySettingsSection } from '../../../components/settings/LoyaltySettings';
import { PolicySchemaForm } from '../../../components/settings/PolicySchemaForm';
import { LensRangePricingSection } from '../../../components/settings/LensRangePricing';

beforeEach(() => {
  vi.clearAllMocks();
  updateLensBrand.mockResolvedValue({});
  deleteLensBrand.mockResolvedValue({});
  updatePreferences.mockResolvedValue({});
});

describe('Lens Master row actions are real buttons (L1-F09)', () => {
  it('names all seven actions and makes them keyboard reachable', async () => {
    render(<LensMasterSection />);
    const names = [
      'Rename lens brand Essilor', 'Delete lens brand Essilor', 'Delete lens index 1.56',
      'Rename coating Blue Cut', 'Delete coating Blue Cut', 'Rename add-on Tint', 'Delete add-on Tint',
    ];
    await screen.findByRole('button', { name: names[0] });
    for (const name of names) {
      const b = screen.getByRole('button', { name });
      expect(b.tagName).toBe('BUTTON'); // native: focusable, Enter/Space activate it
      expect(b).not.toHaveAttribute('tabindex', '-1');
    }
    // No clickable bare SVG is left.
    expect(document.querySelectorAll('svg.cursor-pointer').length).toBe(0);
    expect(unnamedButtons()).toBe(0);
  });

  it('the buttons still do their job', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue('Essilor Pro');
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<LensMasterSection />);
    fireEvent.click(await screen.findByRole('button', { name: 'Rename lens brand Essilor' }));
    await waitFor(() => expect(updateLensBrand).toHaveBeenCalledWith('b1', { name: 'Essilor Pro' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete lens brand Essilor' }));
    await waitFor(() => expect(deleteLensBrand).toHaveBeenCalledWith('b1'));
  });
});

describe('Generated settings rows carry a name (L1-F09)', () => {
  it('TDS rates: one named rate box per section', async () => {
    render(<TdsRatesSection />);
    expect(await screen.findByLabelText(/194J — Professional services rate/)).toHaveValue(10);
    expect(unlabelledControls()).toEqual([]);
  });

  it('Loyalty: number fields are tied to their labels, list rows are named, the switch is a switch', async () => {
    render(<LoyaltySettingsSection />);
    const sw = await screen.findByRole('switch', { name: 'Programme enabled' });
    expect(sw).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('Value for FRAME')).toHaveValue(2);
    expect(unlabelledControls()).toEqual([]);
    expect(unnamedButtons()).toBe(0);
  });

  it('Policy matrix: every generated control and Save button is named after its rule', async () => {
    render(<PolicySchemaForm />);
    expect(await screen.findByRole('checkbox', { name: 'A switch' })).toBeInTheDocument();
    expect(screen.getByLabelText('A rupee limit')).toBeInTheDocument();
    expect(screen.getByLabelText('A choice')).toBeInTheDocument();
    expect(screen.getByLabelText('A text rule')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save A rupee limit' })).toBeInTheDocument();
    expect(unlabelledControls()).toEqual([]);
  });

  it('Lens pricing: filters, row boxes and the add row are named', async () => {
    render(<LensRangePricingSection />);
    expect(await screen.findByLabelText('Base price')).toHaveValue(900);
    expect(screen.getByLabelText('Filter by brand')).toBeInTheDocument();
    expect(unlabelledControls()).toEqual([]);
  });

  it('Users: the search box and the three filters are named', async () => {
    render(<UserManagementSection />);
    expect(await screen.findByLabelText('Search users')).toBeInTheDocument();
    expect(screen.getByLabelText('Filter by role')).toBeInTheDocument();
    expect(screen.getByLabelText('Filter by store')).toBeInTheDocument();
    expect(screen.getByLabelText('Filter by status')).toBeInTheDocument();
    expect(unlabelledControls()).toEqual([]);
  });

  it('My Profile: fields are tied to labels and each preference switch saves only its own key', async () => {
    render(<ProfileSection />);
    expect(await screen.findByLabelText('Email')).toHaveValue('o@example.com');
    expect(unlabelledControls()).toEqual([]);

    const email = screen.getByRole('switch', { name: 'Email Notifications' });
    await waitFor(() => expect(email).toHaveAttribute('aria-checked', 'false'));
    fireEvent.click(email);
    await waitFor(() => expect(updatePreferences).toHaveBeenCalledWith({ email_notifications: true }));
  });
});
