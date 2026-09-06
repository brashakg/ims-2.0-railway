// ============================================================================
// IMS 2.0 - Organization StoreModal: the per-store "Shopify location" dropdown
// (owner ruling 2026-09-06: every physical shop is a Shopify location)
// ============================================================================
// Driven through the REAL page with the api modules mocked. Pinned, each
// revert-proof (swap the exact match for a substring match, or drop the
// preselect call, and the named case goes red):
//   * an unmapped shop whose name EXACTLY equals a location name (case-
//     insensitive) opens with that location preselected, and Save sends it;
//   * a shop whose name is only a SUBSTRING of a location name opens on
//     "not mapped" -- never a hint-picker word match;
//   * a location ANOTHER shop already holds is never preselected, even on an
//     exact name match (it is offered disabled; a Save would 409);
//   * a mapped shop keeps its mapping even when the locations read is DARK
//     (the current gid is its own option) and shows the name as a badge;
//   * an ONLINE store has no badge and no dropdown.
// The useToast mock is ONE hoisted object: OrganizationPage memoises load()
// on [toast], so a per-render mock re-ran load() forever (probe: 112
// orgStoreApi.list calls in ~400 ms) and any spinner frame remounted
// StoreModal -- the cold-run "getLocations called 2 times" flake. One object
// => one load, one locations read, so toHaveBeenCalledTimes(1) is exact.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../../../services/api/entities', () => ({
  entitiesApi: { list: vi.fn(), meta: vi.fn() },
}));
vi.mock('../../../services/api/stores', () => ({
  orgStoreApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() },
}));
vi.mock('../../../services/api/onlineStore', () => ({
  pushApi: { getLocations: vi.fn() },
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

import OrganizationPage from '../OrganizationPage';
import { entitiesApi } from '../../../services/api/entities';
import { orgStoreApi } from '../../../services/api/stores';
import { pushApi } from '../../../services/api/onlineStore';

const ENTITY = { entity_id: 'ent_1', name: 'BV Opticals', gstins: [], is_active: true };
const BOKARO = 'gid://shopify/Location/58793230523';
const DHN = 'gid://shopify/Location/3';

const base = { entity_id: 'ent_1', brand: 'BETTER_VISION', store_type: 'RETAIL', pincode: '826001', phone: '9876543210' };
const STORES = [
  { ...base, store_id: 'BV-DHN-02', store_code: 'BV-DHN-02', store_name: 'HIRAPUR-DHN' },
  { ...base, store_id: 'BV-BOK-03', store_code: 'BV-BOK-03', store_name: 'Sector 4' },
  { ...base, store_id: 'BV-BOK-02', store_code: 'BV-BOK-02', store_name: 'Sec 4 Bokaro',
    shopify_location_id: BOKARO, shopify_location_name: 'Better Vision Sector 4' },
  { ...base, store_id: 'BV-RNC-01', store_code: 'BV-RNC-01', store_name: 'Ranchi Main' },
  { ...base, store_id: 'BV-ONLINE-01', store_code: 'BV-ONLINE-01', store_name: 'Web', store_type: 'ONLINE' },
];

const LOCATIONS = {
  mode: 'LIVE' as const,
  reason: null,
  locations: [
    { id: BOKARO, name: 'Better Vision Sector 4', isActive: true, city: 'Bokaro', mapped_store_id: 'BV-BOK-02', mapped_store_code: 'BV-BOK-02' },
    { id: DHN, name: 'hirapur-dhn', isActive: true, city: 'Dhanbad', mapped_store_id: null },
    { id: 'gid://shopify/Location/2', name: 'Gangadham Pune', isActive: true, mapped_store_id: null },
    // exact name match for BV-RNC-01, but ANOTHER shop already holds it
    { id: 'gid://shopify/Location/5', name: 'Ranchi Main', isActive: true, mapped_store_id: 'BV-RNC-02', mapped_store_code: 'BV-RNC-02' },
  ],
};

async function openStore(name: string) {
  const user = userEvent.setup();
  await waitFor(() => expect(screen.queryByText(/Loading organization/)).toBeNull());
  await user.click(screen.getByText('BV Opticals'));
  const row = (await screen.findByText(name)).closest('li') as HTMLElement;
  await user.click(within(row).getByRole('button', { name: /edit/i }));
  return { user, row };
}

beforeEach(() => {
  vi.mocked(entitiesApi.list).mockResolvedValue({ entities: [ENTITY] } as never);
  vi.mocked(entitiesApi.meta).mockResolvedValue({ state_codes: [], entity_types: [] } as never);
  vi.mocked(orgStoreApi.list).mockResolvedValue({ stores: STORES as never, total: STORES.length });
  vi.mocked(orgStoreApi.update).mockResolvedValue({});
  vi.mocked(pushApi.getLocations).mockResolvedValue(LOCATIONS);
});

describe('StoreModal Shopify location', () => {
  it('preselects the ONE location whose name exactly equals the store name, and Save sends it', async () => {
    render(<OrganizationPage />);
    const { user } = await openStore('HIRAPUR-DHN');
    const select = (await screen.findByLabelText('Shopify location')) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe(DHN));
    expect(pushApi.getLocations).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(orgStoreApi.update).toHaveBeenCalledTimes(1));
    const [storeId, payload] = vi.mocked(orgStoreApi.update).mock.calls[0];
    expect(storeId).toBe('BV-DHN-02');
    expect(payload).toMatchObject({ shopify_location_id: DHN, shopify_location_name: 'hirapur-dhn' });
  });

  it('never preselects on a substring / shared word', async () => {
    render(<OrganizationPage />);
    await openStore('Sector 4');
    const select = (await screen.findByLabelText('Shopify location')) as HTMLSelectElement;
    await waitFor(() => expect(pushApi.getLocations).toHaveBeenCalled());
    // the locations have arrived (Pune is listed) and still nothing is picked
    await screen.findByRole('option', { name: /Gangadham Pune/ });
    expect(select.value).toBe('');
    // a location another shop holds is offered disabled, naming that shop
    const taken = screen.getByRole('option', { name: /Better Vision Sector 4/ }) as HTMLOptionElement;
    expect(taken.disabled).toBe(true);
    expect(taken.textContent).toContain('mapped to BV-BOK-02');
  });

  it('never preselects a location another shop already holds, even on an exact name match', async () => {
    render(<OrganizationPage />);
    await openStore('Ranchi Main');
    const select = (await screen.findByLabelText('Shopify location')) as HTMLSelectElement;
    const taken = (await screen.findByRole('option', { name: /Ranchi Main/ })) as HTMLOptionElement;
    expect(taken.disabled).toBe(true);
    expect(taken.textContent).toContain('mapped to BV-RNC-02');
    expect(select.value).toBe('');
  });

  it('keeps an existing mapping when the read is DARK and shows it as a badge', async () => {
    vi.mocked(pushApi.getLocations).mockResolvedValue({ mode: 'SIMULATED', reason: 'writes_disabled', locations: [] });
    render(<OrganizationPage />);
    const { row } = await openStore('Sec 4 Bokaro');
    expect(within(row).getByTitle('Shopify location').textContent).toBe('Better Vision Sector 4');
    const select = (await screen.findByLabelText('Shopify location')) as HTMLSelectElement;
    await screen.findByText(/location list unavailable/);
    expect(select.value).toBe(BOKARO);
    expect(screen.getByRole('option', { name: 'Better Vision Sector 4' })).toBeTruthy();
  });

  it('shows "not mapped" on an unmapped shop and nothing on an ONLINE store', async () => {
    render(<OrganizationPage />);
    const { row } = await openStore('Web');
    expect(within(row).queryByTitle('Shopify location')).toBeNull();
    expect(screen.queryByLabelText('Shopify location')).toBeNull();
    const dhn = screen.getByText('HIRAPUR-DHN').closest('li') as HTMLElement;
    expect(within(dhn).getByTitle('Shopify location').textContent).toBe('not mapped');
  });
});
