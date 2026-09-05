// ============================================================================
// POS customer panel: the tiles open a panel ON the till, and nothing navigates
// ============================================================================
// Owner (2026-09-05): tapping Family Rx mid-sale used to send the counter to a
// page that asked them to search the customer again, and a cashier was
// bounced by that page's route gate. The panel replaces the navigation.
//
// Rendered for REAL: PosWidgets -> CustomerPanel against the real posStore,
// with only the network doors mocked. Each test asserts the OBSERVABLE
// outcome (dialog present / absent, what the store holds, which door was
// called with what) and reverting the behaviour under test fails it:
//   - make the tile navigate again        -> navigate spy is called
//   - drop the Escape listener            -> dialog stays
//   - "Bill for" via setCustomer          -> cart / prescription reset
//   - gate the section on role            -> no member rows for a cashier
//   - lose the sheet variant              -> data-variant / grab handle gone
//   - skip the queue read before add      -> addToQueue called twice
//
// Fixture fields are the ones production WRITES: family members come from
// backend prescriptions.family_prescriptions (patient_id/name/relation/dob/
// latest.expiry_date/latest.is_valid/prescriptions[]), queue rows from
// clinical.get_queue's _convert_to_camel (patientId/status/tokenNumber).

// jsdom's localStorage in this runner has no working setItem; the repo's
// established answer is a complete Map-backed stand-in installed before the
// import graph touches storage (same helper as generalCounterCompleteSale).
(() => {
  const m = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => { m.set(k, String(v)); },
    removeItem: (k: string) => { m.delete(k); },
    clear: () => { m.clear(); },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', { value: ls, configurable: true, writable: true });
})();

import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { usePOSStore } from '../../../../stores/posStore';

// ---- Doors -------------------------------------------------------------------
const navigateSpy = vi.fn();
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => navigateSpy,
}));

let roles: string[] = ['CASHIER'];
vi.mock('../../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-meena', name: 'Meena', activeStoreId: 'BV-RAN-01', roles },
    hasRole: () => false,
  }),
}));

const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../../../context/ToastContext', () => ({ useToast: () => toast }));

const getFamilyRx = vi.fn();
vi.mock('../../../../services/api/sales', async (orig) => ({
  ...(await orig<typeof import('../../../../services/api/sales')>()),
  prescriptionApi: { getFamilyRx: (...a: unknown[]) => getFamilyRx(...a) },
  orderApi: { getOrders: vi.fn().mockResolvedValue({ orders: [] }) },
}));
vi.mock('../../../../services/api/customers', () => ({
  customerApi: { getCreditSummary: vi.fn().mockResolvedValue({ ar_outstanding: 0, credit_limit: 0, ar_available: null, limit_exceeded: false }) },
}));
const getQueue = vi.fn();
const addToQueue = vi.fn();
vi.mock('../../../../services/api/clinical', () => ({
  clinicalApi: {
    getQueue: (...a: unknown[]) => getQueue(...a),
    addToQueue: (...a: unknown[]) => addToQueue(...a),
  },
}));
const sendRxReminder = vi.fn();
vi.mock('../../../../services/api/marketing', () => ({
  marketingApi: { sendRxReminder: (...a: unknown[]) => sendRxReminder(...a) },
}));
vi.mock('../../../../services/api/promotions', () => ({
  promotionsApi: { listRules: vi.fn().mockResolvedValue({ rules: [], total: 0 }) },
}));
vi.mock('../../../../services/api/incentive', () => ({
  incentiveApi: { getMyDay: vi.fn().mockResolvedValue({ user_id: 'u-meena', store_id: 'BV-RAN-01', date: '2026-09-05', sales_today: 12490, bills_today: 2 }) },
}));
vi.mock('../../../../services/api/loyalty', () => ({
  loyaltyApi: {
    getAccount: vi.fn().mockResolvedValue({ account: { balance_points: 1240, tier: 'GOLD' }, expiring_soon_points: 0 }),
    getLedger: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 8, skip: 0 }),
  },
}));

import { PosWidgets } from '../PosWidgets';

// ---- Fixtures ----------------------------------------------------------------
const rx = (id: string, patientId: string, expiry: string, valid: boolean) => ({
  prescription_id: id,
  patient_id: patientId,
  test_date: '2026-03-12',
  expiry_date: expiry,
  is_valid: valid,
  right_eye: { sphere: '-2.25', cylinder: '-0.75', axis: 180 },
  left_eye: { sphere: '-2.00', cylinder: '-0.50', axis: 175 },
});
const inMonths = (n: number) => {
  const d = new Date();
  d.setMonth(d.getMonth() + n);
  return d.toISOString();
};
// Inside the 60-day recall window (owner mockup: "Expires in 2 months" AND
// "due within 60 days" for the same member) -- 2 calendar months is 61 days.
const inDays = (n: number) => new Date(Date.now() + n * 24 * 60 * 60 * 1000).toISOString();
const FAMILY = {
  customer_id: 'c1',
  customer_name: 'Rahul Sharma',
  member_count: 3,
  total_prescriptions: 3,
  members: [
    { patient_id: 'p-rahul', name: 'Rahul Sharma', relation: 'Self', dob: null, prescription_count: 1, valid_count: 1,
      latest: rx('rx-rahul', 'p-rahul', inMonths(10), true), prescriptions: [rx('rx-rahul', 'p-rahul', inMonths(10), true)] },
    { patient_id: 'p-anita', name: 'Anita Sharma', relation: 'Spouse', dob: null, prescription_count: 1, valid_count: 0,
      latest: rx('rx-anita', 'p-anita', inMonths(-12), false), prescriptions: [rx('rx-anita', 'p-anita', inMonths(-12), false)] },
    { patient_id: 'p-aarav', name: 'Aarav Sharma', relation: 'Son', dob: '2017-01-21', prescription_count: 1, valid_count: 1,
      latest: rx('rx-aarav', 'p-aarav', inDays(55), true), prescriptions: [rx('rx-aarav', 'p-aarav', inDays(55), true)] },
  ],
};

const CART = [
  { id: 'line-1', product_id: 'prod-1', name: 'Ray-Ban Aviator', price: 7990, quantity: 1, discount: 0, total: 7990 },
  { id: 'line-2', product_id: 'prod-2', name: 'Zeiss lens pair', price: 4500, quantity: 1, discount: 0, total: 4500 },
] as any[];

function seedBill() {
  usePOSStore.setState({
    store_id: 'BV-RAN-01',
    customer: {
      id: 'c1',
      name: 'Rahul Sharma',
      phone: '9835540122',
      patients: [
        { patient_id: 'p-rahul', name: 'Rahul Sharma', relation: 'Self', is_primary: true },
        { patient_id: 'p-anita', name: 'Anita Sharma', relation: 'Spouse' },
        { patient_id: 'p-aarav', name: 'Aarav Sharma', relation: 'Son', dob: '2017-01-21' },
      ],
    } as any,
    patient: { id: 'p-rahul', customerId: 'c1', name: 'Rahul Sharma', relation: 'Self', isPrimary: true } as any,
    prescription: { id: 'rx-rahul', patientId: 'p-rahul' } as any,
    cart: CART,
  } as any);
}

let phoneWidth = false;
function installMatchMedia() {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: query.includes('max-width') ? phoneWidth : !phoneWidth,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    }),
  });
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PosWidgets />
    </QueryClientProvider>,
  );
}

const dialog = () => screen.queryByRole('dialog', { name: 'Customer panel' });
// The tile lists member names too; member ROWS live inside the dialog.
const panel = () => within(dialog()!);
const familyTile = () => screen.getByRole('button', { name: /^Family Rx/ });

beforeEach(() => {
  roles = ['CASHIER'];
  phoneWidth = false;
  installMatchMedia();
  navigateSpy.mockReset();
  getFamilyRx.mockReset().mockResolvedValue(FAMILY);
  getQueue.mockReset().mockResolvedValue({ queue: [] });
  addToQueue.mockReset().mockResolvedValue({ id: 'q-1', tokenNumber: 'T004', status: 'WAITING' });
  sendRxReminder.mockReset().mockResolvedValue({ message: 'Rx reminder sent' });
  Object.values(toast).forEach((f) => f.mockReset());
  usePOSStore.getState().resetTransaction();
  seedBill();
});

describe('POS customer panel', () => {
  it('the Family Rx tile opens the panel on the till and navigates nowhere', async () => {
    mount();
    expect(dialog()).toBeNull();
    fireEvent.click(familyTile());
    expect(dialog()).not.toBeNull();
    expect(screen.getByRole('tab', { name: 'Family Rx' })).toHaveAttribute('aria-selected', 'true');
    // The tapped tile stays lit while its section is open.
    expect(familyTile()).toHaveAttribute('aria-pressed', 'true');
    expect(navigateSpy).not.toHaveBeenCalled();
    // The bill is still there behind the scrim: the tiles are not unmounted.
    expect(screen.getByRole('button', { name: /^My day/ })).toBeInTheDocument();
  });

  it('every tile lands on its own section, and the tab strip moves between them without leaving', () => {
    mount();
    fireEvent.click(screen.getByRole('button', { name: /^Outstanding/ }));
    expect(screen.getByRole('tab', { name: 'Dues' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: 'My day' }));
    expect(screen.getByRole('tab', { name: 'My day' })).toHaveAttribute('aria-selected', 'true');
    expect(dialog()).not.toBeNull();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('Escape returns to the bill; so does tapping outside', () => {
    mount();
    fireEvent.click(familyTile());
    expect(dialog()).not.toBeNull();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(dialog()).toBeNull();
    expect(familyTile()).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(familyTile());
    fireEvent.click(screen.getByTestId('customer-panel-scrim'));
    expect(dialog()).toBeNull();
  });

  it('"Open full page" carries the customer in the link', () => {
    mount();
    fireEvent.click(familyTile());
    fireEvent.click(screen.getByRole('button', { name: /Open full page/ }));
    expect(navigateSpy).toHaveBeenCalledWith('/clinical/family-rx?customer=c1');
  });

  it('"Bill for <member>" keeps the cart and changes the person through the store setters', async () => {
    mount();
    fireEvent.click(familyTile());
    await panel().findByText('Aarav Sharma');
    const setPatient = vi.spyOn(usePOSStore.getState(), 'setPatient');
    const setPrescription = vi.spyOn(usePOSStore.getState(), 'setPrescription');
    const setCustomer = vi.spyOn(usePOSStore.getState(), 'setCustomer');

    fireEvent.click(screen.getByRole('button', { name: 'Bill for Aarav' }));

    // Decision 1: the SAME setters the customer search uses, member id carried.
    expect(setPatient).toHaveBeenCalledWith(expect.objectContaining({ id: 'p-aarav', customerId: 'c1', name: 'Aarav Sharma' }));
    expect(setPrescription).toHaveBeenCalledWith(expect.objectContaining({ id: 'rx-aarav', patientId: 'p-aarav' }));
    // NEVER setCustomer: that resets patient + prescription and would drop the pick.
    expect(setCustomer).not.toHaveBeenCalled();

    const s = usePOSStore.getState();
    expect(s.patient?.id).toBe('p-aarav');
    expect(s.prescription?.id).toBe('rx-aarav');
    expect(s.customer?.id).toBe('c1');
    expect(s.cart).toHaveLength(2);
    // No confirm; the panel closes back to the bill.
    expect(dialog()).toBeNull();
  });

  it('"Bill for" a member whose only Rx is expired switches the person and clears the Rx (never loads an expired one)', async () => {
    mount();
    fireEvent.click(familyTile());
    await panel().findByText('Anita Sharma');
    fireEvent.click(screen.getByRole('button', { name: 'Bill for Anita' }));
    const s = usePOSStore.getState();
    expect(s.patient?.id).toBe('p-anita');
    expect(s.prescription).toBeNull();
    expect(s.cart).toHaveLength(2);
  });

  it('a CASHIER sees the Family Rx section render (no bounce, no navigation)', async () => {
    roles = ['CASHIER'];
    mount();
    fireEvent.click(familyTile());
    expect(await panel().findByText('Anita Sharma')).toBeInTheDocument();
    expect(panel().getByText('Aarav Sharma')).toBeInTheDocument();
    // The member on the bill is marked, not offered as "Bill for".
    expect(screen.getByText('On this bill')).toBeInTheDocument();
    expect(screen.getByText(/1 expired · 1 due within 60 days/)).toBeInTheDocument();
    expect(navigateSpy).not.toHaveBeenCalled();
    expect(getFamilyRx).toHaveBeenCalledWith('c1');
  });

  it('phone width renders the bottom-sheet variant; desktop the slide-over', () => {
    phoneWidth = true;
    installMatchMedia();
    const view = mount();
    fireEvent.click(familyTile());
    const sheet = dialog()!;
    expect(sheet).toHaveAttribute('data-variant', 'sheet');
    expect(screen.getByTestId('customer-panel-grab')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back to bill' })).toHaveClass('w-full');
    view.unmount();

    phoneWidth = false;
    installMatchMedia();
    mount();
    fireEvent.click(familyTile());
    expect(dialog()).toHaveAttribute('data-variant', 'slide-over');
    expect(screen.queryByTestId('customer-panel-grab')).toBeNull();
  });

  it('"Book eye test" adds the member to today\'s queue through the existing door, once', async () => {
    mount();
    fireEvent.click(familyTile());
    const anita = (await panel().findByText('Anita Sharma')).closest('li')!;
    fireEvent.click(within(anita).getByRole('button', { name: 'Book eye test' }));
    await waitFor(() => expect(addToQueue).toHaveBeenCalledTimes(1));
    expect(addToQueue).toHaveBeenCalledWith(expect.objectContaining({
      storeId: 'BV-RAN-01', customerId: 'c1', patientId: 'p-anita', patientName: 'Anita Sharma', customerPhone: '9835540122',
    }));
    expect(toast.success).toHaveBeenCalled();
    // The bill stays open: the panel is still up, nothing navigated.
    expect(dialog()).not.toBeNull();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('"Book eye test" says so instead of queuing a member already waiting today', async () => {
    getQueue.mockResolvedValue({ queue: [{ id: 'q-9', patientId: 'p-anita', status: 'WAITING', tokenNumber: 'T002' }] });
    mount();
    fireEvent.click(familyTile());
    const anita = (await panel().findByText('Anita Sharma')).closest('li')!;
    fireEvent.click(within(anita).getByRole('button', { name: 'Book eye test' }));
    await waitFor(() => expect(toast.info).toHaveBeenCalled());
    expect(String(toast.info.mock.calls[0][0])).toMatch(/already in today's queue/);
    expect(addToQueue).not.toHaveBeenCalled();
  });

  it('"Send reminder on WhatsApp" queues through the existing Rx-expiry recall door', async () => {
    mount();
    fireEvent.click(familyTile());
    await panel().findByText('Anita Sharma');
    fireEvent.click(screen.getByRole('button', { name: 'Send reminder on WhatsApp' }));
    await waitFor(() => expect(sendRxReminder).toHaveBeenCalledWith('c1'));
    expect(String(toast.success.mock.calls[0][0])).toMatch(/queued/i);
  });

  it('with no customer on the bill the panel still opens (sections show their empty hint)', async () => {
    await act(async () => { usePOSStore.getState().setCustomer(null); });
    mount();
    fireEvent.click(familyTile());
    expect(dialog()).not.toBeNull();
    expect(screen.getByText(/Pick a customer on the bill/)).toBeInTheDocument();
    expect(getFamilyRx).not.toHaveBeenCalled();
  });
});
