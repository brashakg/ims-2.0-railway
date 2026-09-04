// ============================================================================
// IMS 2.0 - StoreSelectPage (post-login store picker)
// ----------------------------------------------------------------------------
// Behavioural tests against the DOM. The store fixture mirrors PROD's six
// stores (verified 2026-09-04): a uuid-id shop, two coded shops, one WizOpt
// shop and the two ONLINE stores. Each case fails if its behaviour reverts:
// re-add the ellipsis, flatten the groups, drop the hotkeys, and it goes red.
// ============================================================================

import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { render, screen, within, fireEvent, waitFor, act } from '@testing-library/react';

/** Deterministic in-memory localStorage: the runner's global is Node's
 *  non-functional built-in (no valid --localstorage-file), same workaround as
 *  IdleLogoutWatcher.test.tsx. */
beforeAll(() => {
  const store = new Map<string, string>();
  const mock: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => store.delete(k),
    setItem: (k: string, v: string) => store.set(k, String(v)),
  };
  Object.defineProperty(window, 'localStorage', { configurable: true, value: mock });
});

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const orig = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...orig,
    useNavigate: () => navigateSpy,
    useLocation: () => ({ state: { from: '/pos/new' } }),
  };
});

const setActiveStore = vi.fn();
const logout = vi.fn(() => Promise.resolve());
let mockUser: Record<string, unknown>;
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: mockUser, setActiveStore, logout }),
}));

const getStores = vi.fn();
vi.mock('../../../services/api', () => ({
  storeApi: { getStores: (...a: unknown[]) => getStores(...a) },
}));

const getSummary = vi.fn();
vi.mock('../../../services/api/onlineStore', () => ({
  onlineStoreApi: { getSummary: (...a: unknown[]) => getSummary(...a) },
}));

import { StoreSelectPage } from '../StoreSelectPage';

// GET /stores as prod returns it (snake_case, active_only).
const RAW = [
  { store_id: '4dc49c44-08a1-46e1-85fb-8b7eca55f560', store_name: 'GANGADHAM- PUNE', brand: 'BETTER_VISION', store_type: 'RETAIL', city: 'PUNE' },
  { store_id: 'BV-BOK-02', store_code: 'BV-BOK-02', store_name: 'Sec 4 Bokaro', brand: 'BETTER_VISION', store_type: 'RETAIL', city: 'Bokaro Steel City' },
  { store_id: 'BV-DHN-02', store_code: 'BV-DHN-02', store_name: 'HIRAPUR-DHN', brand: 'BETTER_VISION', store_type: 'RETAIL', city: 'DHANBAD' },
  { store_id: 'BV-ONLINE-01', store_code: 'BV-ONLINE-01', store_name: 'Better Vision Online', brand: 'BETTER_VISION', store_type: 'ONLINE' },
  { store_id: 'WIZ-DHN-01', store_code: 'WIZ-DHN-01', store_name: 'Saraidhela- WizOpt', brand: 'WIZOPT', store_type: 'RETAIL', city: 'Dhanbad' },
  { store_id: 'WO-ONLINE-01', store_code: 'WO-ONLINE-01', store_name: 'WizOpt Online', brand: 'WIZOPT', store_type: 'ONLINE' },
];

// /online-store/summary.storefronts as the backend really shapes it. PROD has
// exactly ONE registered storefront today (the BV seed) — there is no WizOpt
// row, which is precisely why WO-ONLINE-01 must read Dark from its ABSENCE.
const POSTURES = [
  { storefront_id: 'BV', name: 'Better Vision Online', brand: 'BETTER_VISION', is_default: true, is_live: true },
];
const POSTURES_WITH_WIZOPT = [
  ...POSTURES,
  { storefront_id: 'WIZOPT', name: 'WizOpt Online', brand: 'WIZOPT', is_default: false, is_live: false },
];

const admin = (over: Record<string, unknown> = {}) => ({
  id: 'u1',
  name: 'Avinash',
  roles: ['SUPERADMIN'],
  activeRole: 'SUPERADMIN',
  storeIds: [],
  activeStoreId: 'BV-BOK-02',
  ...over,
});

/** The grid is up AND its passive effects (the window keydown listener) have
 *  flushed: findBy* polls with the act environment off, so under CPU load a
 *  one-shot keyDown fired straight after it could land before the listener. */
const heading = async () => {
  const h1 = await screen.findByRole('heading', { name: 'Choose your store' });
  await act(async () => {});
  return h1;
};

beforeEach(() => {
  vi.clearAllMocks();
  mockUser = admin();
  getStores.mockResolvedValue({ stores: RAW, total: RAW.length });
  getSummary.mockResolvedValue({ storefronts: null });
  localStorage.removeItem('ims_last_store:u1');
});

describe('StoreSelectPage - auto-proceed / retry (unchanged behaviour)', () => {
  it('auto-proceeds when exactly one store is accessible', async () => {
    mockUser = admin({ roles: ['AREA_MANAGER'], activeRole: 'AREA_MANAGER', storeIds: ['BV-DHN-02'], activeStoreId: '' });
    render(<StoreSelectPage />);
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/pos/new', { replace: true }));
    expect(setActiveStore).toHaveBeenCalledWith('BV-DHN-02');
    expect(screen.queryByRole('heading', { name: 'Choose your store' })).toBeNull();
  });

  it('shows the empty state after a failed load and "Try again" re-fetches', async () => {
    mockUser = admin({ activeStoreId: '' });
    getStores.mockRejectedValueOnce(new Error('network'));
    render(<StoreSelectPage />);
    await screen.findByRole('heading', { name: 'No store available' });
    expect(navigateSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await heading();
    expect(getStores).toHaveBeenCalledTimes(2);
  });
});

describe('StoreSelectPage - grouping', () => {
  it('groups shops by the brand they trade as, online stores in their own section', async () => {
    render(<StoreSelectPage />);
    await heading();

    const bv = within(screen.getByRole('listbox', { name: 'Better Vision' }));
    expect(bv.getAllByRole('option').map((o) => o.textContent)).toEqual([
      expect.stringContaining('GANGADHAM- PUNE'),
      expect.stringContaining('HIRAPUR-DHN'),
      expect.stringContaining('Sec 4 Bokaro'),
    ]);
    expect(bv.queryByText('Better Vision Online')).toBeNull();

    const wz = within(screen.getByRole('listbox', { name: 'WizOpt' }));
    expect(wz.getAllByRole('option')).toHaveLength(1);
    expect(wz.getByText('Saraidhela- WizOpt')).toBeInTheDocument();

    const web = within(screen.getByRole('listbox', { name: 'Online stores' }));
    expect(web.getAllByRole('option')).toHaveLength(2);
    expect(web.getByText('Better Vision Online')).toBeInTheDocument();
    expect(web.getByText('WizOpt Online')).toBeInTheDocument();

    expect(screen.getByText('3 shops')).toBeInTheDocument();
    expect(screen.getByText('1 shop')).toBeInTheDocument();
    expect(screen.getByText('no till, no walk-ins')).toBeInTheDocument();
    expect(screen.getByText(/you have access to 6\./)).toBeInTheDocument();
  });

  it('online cards carry the storefront live/dark posture, joined by brand', async () => {
    getSummary.mockResolvedValue({ storefronts: POSTURES_WITH_WIZOPT });
    render(<StoreSelectPage />);
    await heading();

    const web = within(screen.getByRole('listbox', { name: 'Online stores' }));
    const bvOnline = await web.findByRole('option', { name: /Better Vision Online/ });
    expect(within(bvOnline).getByText('Selling')).toBeInTheDocument();
    const woOnline = web.getByRole('option', { name: /WizOpt Online/ });
    expect(within(woOnline).getByText('Dark')).toBeInTheDocument();
    expect(within(woOnline).queryByText('Selling')).toBeNull();
    // Shops never get a posture chip.
    expect(screen.getAllByText('Selling')).toHaveLength(1);
  });

  it('an online store with NO registered storefront reads Dark, not the default storefront', async () => {
    // THE PROD CASE (2026-09-04): the registry holds only the BV seed, so the
    // summary returns one row. Falling back to the default row here would put
    // a green "Selling" on WO-ONLINE-01, which is dark — a lie on the screen.
    getSummary.mockResolvedValue({ storefronts: POSTURES });
    render(<StoreSelectPage />);
    await heading();

    const web = within(screen.getByRole('listbox', { name: 'Online stores' }));
    const woOnline = await web.findByRole('option', { name: /WizOpt Online/ });
    expect(within(woOnline).getByText('Dark')).toBeInTheDocument();
    expect(within(woOnline).queryByText('Selling')).toBeNull();
    expect(
      within(web.getByRole('option', { name: /Better Vision Online/ })).getByText('Selling'),
    ).toBeInTheDocument();
  });

  it('claims nothing when the backend is older than the brand field (deploy window)', async () => {
    // Rows arrive with no `brand` -> nothing to join on. Silence beats calling
    // BV-ONLINE-01 dark while it is selling.
    getSummary.mockResolvedValue({
      storefronts: [{ storefront_id: 'BV', name: 'Better Vision Online', is_default: true, is_live: true }],
    });
    render(<StoreSelectPage />);
    await heading();
    await waitFor(() => expect(getSummary).toHaveBeenCalled());
    expect(screen.queryByText('Selling')).toBeNull();
    expect(screen.queryByText('Dark')).toBeNull();
    expect(screen.getAllByText('Online')).toHaveLength(2);
  });

  it('shows no live/dark claim when the posture is not readable by this role', async () => {
    render(<StoreSelectPage />);
    await heading();
    await waitFor(() => expect(getSummary).toHaveBeenCalled());
    expect(screen.queryByText('Selling')).toBeNull();
    expect(screen.queryByText('Dark')).toBeNull();
    expect(screen.getAllByText('Online')).toHaveLength(2);
  });
});

describe('StoreSelectPage - names wrap, never truncate', () => {
  it('renders the full store name with no ellipsis styling', async () => {
    render(<StoreSelectPage />);
    await heading();
    for (const name of ['GANGADHAM- PUNE', 'Saraidhela- WizOpt', 'Better Vision Online']) {
      const el = screen.getByText(name);
      expect(el.textContent).toBe(name);
      expect(el.style.whiteSpace).not.toBe('nowrap');
      expect(el.style.textOverflow).not.toBe('ellipsis');
      expect(el.style.overflow).not.toBe('hidden');
      expect(el.closest('[role="option"]')).not.toBeNull();
    }
  });
});

describe('StoreSelectPage - keyboard', () => {
  it('digit N picks the Nth visible card via setActiveStore', async () => {
    render(<StoreSelectPage />);
    await heading();
    // Visible order: Better Vision (GANGADHAM, HIRAPUR, Sec 4), WizOpt, Online.
    const third = screen.getByRole('option', { name: /Sec 4 Bokaro/ });
    expect(within(third).getByText('3')).toBeInTheDocument();
    const fourth = screen.getByRole('option', { name: /Saraidhela- WizOpt/ });
    expect(within(fourth).getByText('4')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: '4' });
    expect(setActiveStore).toHaveBeenCalledWith('WIZ-DHN-01');
    expect(navigateSpy).toHaveBeenCalledWith('/pos/new', { replace: true });
  });

  it('Enter continues where you were (last store persisted per user)', async () => {
    localStorage.setItem('ims_last_store:u1', 'BV-DHN-02');
    render(<StoreSelectPage />);
    await heading();
    const resume = screen.getByRole('button', { name: 'Continue at HIRAPUR-DHN' });
    expect(within(resume).getByText('Where you were')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'Enter' });
    expect(setActiveStore).toHaveBeenCalledWith('BV-DHN-02');
    expect(localStorage.getItem('ims_last_store:u1')).toBe('BV-DHN-02');
  });

  it('falls back to the active store for "Where you were"', async () => {
    render(<StoreSelectPage />);
    await heading();
    expect(screen.getByRole('button', { name: 'Continue at Sec 4 Bokaro' })).toBeInTheDocument();
  });

  it('typing filters; digits are not stolen mid-word; Enter picks the single match', async () => {
    render(<StoreSelectPage />);
    await heading();
    const input = screen.getByRole('searchbox', { name: 'Type to filter stores' });

    fireEvent.change(input, { target: { value: 'sec ' } });
    fireEvent.keyDown(input, { key: '4' });
    expect(setActiveStore).not.toHaveBeenCalled();
    expect(screen.getByText('Sec 4 Bokaro')).toBeInTheDocument();
    expect(screen.queryByText('HIRAPUR-DHN')).toBeNull();

    fireEvent.change(input, { target: { value: 'sarai' } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(setActiveStore).toHaveBeenCalledWith('WIZ-DHN-01');
  });

  it('picks the card on click and remembers it for next time', async () => {
    render(<StoreSelectPage />);
    await heading();
    fireEvent.click(screen.getByRole('option', { name: /GANGADHAM- PUNE/ }));
    expect(setActiveStore).toHaveBeenCalledWith('4dc49c44-08a1-46e1-85fb-8b7eca55f560');
    expect(localStorage.getItem('ims_last_store:u1')).toBe('4dc49c44-08a1-46e1-85fb-8b7eca55f560');
  });
});
