// ============================================================================
// APP-03: the Organization store form shows the create contract and ties each
// label to its control. Before the fix: Phone / Address / City / State / PIN
// carried no required marker, an untouched Phone went to POST /stores and came
// back "phone: Field required", and no label was associated with its input
// (getByLabelText found nothing; a label click focused nothing).
// Also APP-01's hint: a storeless SUPERADMIN on Organization is pointed to the
// store picker once an active store exists.
// ============================================================================
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { list: vi.fn(), meta: vi.fn(), create: vi.fn(), update: vi.fn() },
}));
vi.mock('../../../services/api/stores', () => ({
  orgStoreApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() },
}));
vi.mock('../../../services/api/onlineStore', () => ({
  pushApi: { getLocations: vi.fn() },
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
const auth = vi.hoisted(() => ({ user: { roles: ['SUPERADMIN'], activeStoreId: 'BV-BOK-02' } as Record<string, unknown> }));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => auth }));

import OrganizationPage from '../OrganizationPage';
import { entitiesApi } from '../../../services/api/entities';
import { orgStoreApi } from '../../../services/api/stores';
import { pushApi } from '../../../services/api/onlineStore';

const ENTITY = { entity_id: 'ent_1', name: 'BV Opticals', gstins: [{ gstin: '20AAAAA0000A1Z5', state_code: '20', is_primary: true }], is_active: true };
const META = { state_codes: [{ code: '20', name: 'Jharkhand' }], entity_types: ['LLP'] };
// A legacy row with no phone / address / city / state: editing it must still save.
const LEGACY = { store_id: 'BV-OLD-01', store_code: 'BV-OLD-01', store_name: 'Old Shop', entity_id: 'ent_1', brand: 'BETTER_VISION', store_type: 'RETAIL' };

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { roles: ['SUPERADMIN'], activeStoreId: 'BV-BOK-02' };
  vi.mocked(entitiesApi.list).mockResolvedValue({ entities: [ENTITY] } as never);
  vi.mocked(entitiesApi.meta).mockResolvedValue(META as never);
  vi.mocked(orgStoreApi.list).mockResolvedValue({ stores: [LEGACY] as never, total: 1 });
  vi.mocked(orgStoreApi.create).mockResolvedValue({ store_id: 'BV-NEW-01', message: 'Store created' });
  vi.mocked(orgStoreApi.update).mockResolvedValue({});
  vi.mocked(pushApi.getLocations).mockResolvedValue({ mode: 'SIMULATED', reason: 'writes_disabled', locations: [] });
});

async function openNewStore() {
  const user = userEvent.setup();
  render(<OrganizationPage />);
  await waitFor(() => expect(screen.queryByText(/Loading organization/)).toBeNull());
  await user.click(screen.getByRole('button', { name: /^Store$/ }));
  await screen.findByText('New store under BV Opticals');
  return user;
}

const REQUIRED = ['Store code', 'Store name', 'Brand', 'State', 'City', 'PIN code', 'Phone', 'Address'];

describe('Organization store form (APP-03)', () => {
  it('every required field is marked and found by its label; optional ones are not marked', async () => {
    await openNewStore();
    for (const name of REQUIRED) {
      const ctl = screen.getByLabelText(`${name} *`);
      expect(ctl.getAttribute('aria-required')).toBe('true');
    }
    for (const name of ['WhatsApp', 'Locality', 'Landmark']) {
      expect(screen.getByLabelText(name).getAttribute('aria-required')).toBeNull();
    }
  });

  it('clicking a label focuses its control', async () => {
    const user = await openNewStore();
    for (const name of ['Phone', 'Address', 'City', 'PIN code']) {
      await user.click(screen.getByText(`${name} *`));
      expect(document.activeElement).toBe(screen.getByLabelText(`${name} *`));
    }
  });

  it('an untouched new store is refused locally, field by field, and never sent', async () => {
    const user = await openNewStore();
    await user.type(screen.getByLabelText('Store code *'), 'BV-NEW-01');
    await user.type(screen.getByLabelText('Store name *'), 'New Shop');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    for (const name of ['Phone', 'Address', 'City', 'PIN code', 'State']) {
      expect(screen.getByText(`${name} is required`)).toBeTruthy();
      expect(screen.getByLabelText(`${name} *`).getAttribute('aria-invalid')).toBe('true');
    }
    expect(toast.error).toHaveBeenCalled();
    expect(orgStoreApi.create).not.toHaveBeenCalled();
    // fixing a field clears its own message
    await user.type(screen.getByLabelText('Phone *'), '9876543210');
    expect(screen.queryByText('Phone is required')).toBeNull();
  });

  it('spaces only is still missing', async () => {
    const user = await openNewStore();
    await user.type(screen.getByLabelText('Phone *'), '   ');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    expect(screen.getByText('Phone is required')).toBeTruthy();
    expect(orgStoreApi.create).not.toHaveBeenCalled();
  });

  it('a complete new store is sent with every required field', async () => {
    const user = await openNewStore();
    await user.type(screen.getByLabelText('Store code *'), 'BV-NEW-01');
    await user.type(screen.getByLabelText('Store name *'), 'New Shop');
    await user.selectOptions(screen.getByLabelText('State *'), '20');
    await user.type(screen.getByLabelText('City *'), 'Bokaro');
    await user.type(screen.getByLabelText('PIN code *'), '827004');
    await user.type(screen.getByLabelText('Phone *'), '9876543210');
    await user.type(screen.getByLabelText('Address *'), 'Sector 4 Market');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(orgStoreApi.create).toHaveBeenCalledTimes(1));
    expect(vi.mocked(orgStoreApi.create).mock.calls[0][0]).toMatchObject({
      store_code: 'BV-NEW-01', store_name: 'New Shop', brand: 'BETTER_VISION', entity_id: 'ent_1',
      state_code: '20', state: 'Jharkhand', city: 'Bokaro', pincode: '827004', phone: '9876543210',
      address: 'Sector 4 Market',
    });
  });

  it('editing a legacy store with blank phone / address still saves (edit keeps the old rule)', async () => {
    const user = userEvent.setup();
    render(<OrganizationPage />);
    await waitFor(() => expect(screen.queryByText(/Loading organization/)).toBeNull());
    await user.click(screen.getByText('BV Opticals'));
    const row = (await screen.findByText('Old Shop')).closest('li') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /edit/i }));
    await screen.findByText('Edit Old Shop');
    expect(screen.getByLabelText('Phone').getAttribute('aria-required')).toBeNull();
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(orgStoreApi.update).toHaveBeenCalledTimes(1));
  });

  it('the entity form ties its labels too', async () => {
    const user = userEvent.setup();
    render(<OrganizationPage />);
    await waitFor(() => expect(screen.queryByText(/Loading organization/)).toBeNull());
    await user.click(screen.getByRole('button', { name: /Add entity/ }));
    expect(screen.getByLabelText('Display name *').getAttribute('aria-required')).toBe('true');
    for (const name of ['Legal name', 'PAN', 'Registered phone', 'Registered address']) {
      expect(screen.getByLabelText(name)).toBeTruthy();
    }
  });
});

describe('Organization choose-your-store hint (APP-01)', () => {
  it('a storeless owner is pointed to the picker once an active store exists', async () => {
    auth.user = { roles: ['SUPERADMIN'], activeStoreId: '' };
    render(<OrganizationPage />);
    const link = await screen.findByRole('link', { name: 'Choose your store' });
    expect(link.getAttribute('href')).toBe('/select-store');
  });

  it('no hint while a store is chosen, or before any active store exists', async () => {
    render(<OrganizationPage />);
    await waitFor(() => expect(screen.queryByText(/Loading organization/)).toBeNull());
    expect(screen.queryByRole('link', { name: 'Choose your store' })).toBeNull();
  });

  it('no hint on an empty install (nothing to choose yet)', async () => {
    auth.user = { roles: ['SUPERADMIN'], activeStoreId: '' };
    vi.mocked(entitiesApi.list).mockResolvedValue({ entities: [] } as never);
    vi.mocked(orgStoreApi.list).mockResolvedValue({ stores: [] as never, total: 0 });
    render(<OrganizationPage />);
    await screen.findByText(/No legal entities yet/);
    expect(screen.queryByRole('link', { name: 'Choose your store' })).toBeNull();
  });
});
