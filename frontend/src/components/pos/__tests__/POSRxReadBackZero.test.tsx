// ============================================================================
// PATIENT SAFETY: reading a STORED Rx back at the counter
// ============================================================================
// The sibling sweep for the blank-vs-zero conflation turned up two more copies
// of it in POSLayout, on the READ side rather than the write side:
//
//   1. `attachRx` -- the function that attaches a stored prescription to a LIVE
//      SALE, so what it invents here is what the lab grinds. It read
//      `parseFloat(a || b || c || '0')`, which fabricated a confident plano
//      0.00 for an Rx that recorded no sphere / cylinder / add, and ALSO
//      skipped past a recorded 0 in the first key alias to whatever the next
//      alias happened to hold.
//
//   2. The chooser row's `fmtPower`, which answered `'0.00'` for anything
//      falsy: `if (!n || isNaN(n)) return '0.00'`. Absence and plano therefore
//      printed IDENTICALLY at the exact moment a staff member picks which
//      prescription to dispense.
//
// Both are now driven by utils/rxPowerValue. This file drives the real POS
// prescription step with a stored Rx and asserts on the rendered chooser row
// and on the prescription that lands in the POS store.

import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

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

const H = vi.hoisted(() => ({ getPrescriptions: vi.fn() }));

// Hoisted to a STABLE object on purpose. Returning a fresh `{ user: {...} }`
// from useAuth on every render changes the identity POSLayout's effects depend
// on, and the component re-enters setStoreId forever ("Maximum update depth
// exceeded") -- a test-harness fault that looks exactly like a product bug.
const MOCK_USER = {
  id: 'u1', name: 'Asha Kumari', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER',
  activeStoreId: 'BV-BOK-01', storeIds: ['BV-BOK-01'], discountCap: 20,
};
const MOCK_AUTH = {
  user: MOCK_USER,
  // CustomerCardWithLoyalty (inside POSLayout) gates its edit door on hasRole.
  hasRole: (r: string | string[]) => [r].flat().some((x) => MOCK_USER.roles.includes(x)),
};
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => MOCK_AUTH }));

vi.mock('../../../hooks/usePOSQueries', () => ({
  useProducts: () => ({ data: [], isLoading: false }),
  useCustomerSearch: () => ({ data: [], isLoading: false }),
  useCustomer: () => ({ data: null }),
  useStores: () => ({ data: [], isLoading: false }),
}));

vi.mock('../../../constants/gstRuntime', () => ({
  resolveGstRate: () => 5,
  isInclusivePricing: () => true,
  loadHsnRates: vi.fn(),
  loadPricingMode: vi.fn(),
}));

vi.mock('../../../services/api', () => {
  const noop = () => Promise.resolve([]);
  return {
    customerApi: { search: noop, getCustomer: noop },
    orderApi: { createOrder: noop, addPayment: noop },
    prescriptionApi: {
      getPrescriptions: H.getPrescriptions,
      createPrescription: () => Promise.resolve({ prescription_id: 'RX-NEW' }),
    },
    workshopApi: { createJob: noop, updateFittingDetails: noop },
    adminStoreApi: { listStores: noop, getStoreUsers: () => Promise.resolve([]), getStaff: () => Promise.resolve([]) },
    inventoryApi: { searchByBarcode: noop },
    loyaltyApi: { redeem: noop, getBalance: noop },
    storeApi: { getStore: () => Promise.resolve({ store_id: 'BV-BOK-01' }) },
  };
});

vi.mock('../../../services/api/walkouts', () => ({
  walkoutsApi: { walkinsPosIncrement: () => Promise.resolve({ total: 1 }) },
}));

import { MemoryRouter } from 'react-router-dom';
import { POSLayout } from '../POSLayout';
import { usePOSStore } from '../../../stores/posStore';
import { ToastProvider } from '../../../context/ToastContext';

const SLOW = 20000;

/** A stored prescription, dated recently so the validity filter keeps it. */
function storedRx(right: Record<string, unknown>, left: Record<string, unknown>) {
  return {
    prescription_id: 'RX-STORED-1',
    testDate: new Date().toISOString(),
    validity_months: 12,
    source: 'TESTED_AT_STORE',
    optometrist_name: 'Dr Rao',
    rightEye: right,
    leftEye: left,
  };
}

/** Open the POS prescription step with `rx` on file, and return the chooser row. */
async function openChooserWith(rx: Record<string, unknown>): Promise<HTMLElement> {
  H.getPrescriptions.mockResolvedValue({ prescriptions: [rx] });
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.setSalesperson('sp1', 'Sales Person');
    s.setSaleType('prescription_order');
    usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as any });
  });
  render(
    <MemoryRouter>
      <ToastProvider>
        <POSLayout />
      </ToastProvider>
    </MemoryRouter>,
  );
  act(() => usePOSStore.getState().setStep('products'));
  fireEvent.click(screen.getByRole('button', { name: 'Use last exam' }));
  return await screen.findByText(/^R: /, {}, { timeout: SLOW });
}

async function attachAndReadStore(): Promise<any> {
  fireEvent.click(screen.getByRole('button', { name: 'Attach' }));
  await waitFor(() => expect(usePOSStore.getState().prescription).toBeTruthy(), { timeout: SLOW });
  return usePOSStore.getState().prescription;
}

beforeEach(() => {
  localStorage.clear();
  H.getPrescriptions.mockReset();
  act(() => usePOSStore.getState().resetTransaction());
});

describe('the chooser row tells absence apart from plano', () => {
  it('shows a dash - never "0.00" - for an Rx that recorded no powers', async () => {
    // THE REQUIREMENT. `if (!n || isNaN(n)) return '0.00'` printed a confident
    // plano here for a prescription that measured nothing at all, at the exact
    // moment a staff member decides which Rx to dispense.
    const row = await openChooserWith(storedRx({ axis: 90, cyl: '-1.25' }, {}));

    expect(row.textContent).toContain('R: -/');
    expect(row.textContent).not.toContain('R: 0.00');
  }, SLOW);

  it('shows +0.00 for a power recorded AS zero, on BOTH eyes', async () => {
    const row = await openChooserWith(storedRx({ sph: '0.00' }, { sph: 0 }));

    // THE REQUIREMENT: a measured plano is shown as one...
    expect(row.textContent).toContain('R: +0.00');
    expect(row.textContent).toContain('L: +0.00');
    // ...and the two states are not the same string.
    expect(row.textContent).not.toBe('-');
  }, SLOW);

  it('does not skip a recorded 0 in the canonical key for its alias', async () => {
    // `re.sph || re.sphere` fell straight past a plano `sph` to the legacy
    // `sphere` alias, reporting a strong prescription for a plano eye.
    const row = await openChooserWith(storedRx({ sph: 0, sphere: -3 }, { sph: 0, sphere: -3 }));

    expect(row.textContent).toContain('R: +0.00');
    expect(row.textContent).not.toContain('-3.00');
  }, SLOW);
});

describe('attachRx puts the RECORDED prescription on the sale', () => {
  it('attaches null - not a fabricated 0 - for powers the Rx never recorded', async () => {
    // THE REQUIREMENT. This is the value that reaches the lens suggestions and
    // the lab, so an invented plano here is a wrong lens.
    await openChooserWith(storedRx({ cyl: '-1.25', axis: 90 }, {}));
    const rx = await attachAndReadStore();

    expect(rx.rightEye.sphere).toBeNull();
    expect(rx.rightEye.add).toBeNull();
    expect(rx.leftEye.sphere).toBeNull();
    expect(rx.leftEye.cylinder).toBeNull();
    expect(rx.leftEye.add).toBeNull();
    for (const eye of [rx.rightEye, rx.leftEye]) {
      for (const f of ['sphere', 'cylinder', 'add'] as const) {
        expect(eye[f]).not.toBe(0);
      }
    }
    // ...and what WAS recorded still attaches.
    expect(rx.rightEye.cylinder).toBe(-1.25);
  }, SLOW);

  it('attaches a recorded 0 as numeric 0, on BOTH eyes', async () => {
    await openChooserWith(
      storedRx(
        { sph: '0.00', cyl: '0.00', add: 0 },
        { sph: 0, cyl: '0.00', add: '0.00' },
      ),
    );
    const rx = await attachAndReadStore();

    // THE REQUIREMENT: not one of these may be re-badged as "not recorded".
    for (const eye of [rx.rightEye, rx.leftEye]) {
      expect(eye.sphere).toBe(0);
      expect(eye.cylinder).toBe(0);
      expect(eye.add).toBe(0);
      expect(eye.sphere).not.toBeNull();
      expect(eye.cylinder).not.toBeNull();
      expect(eye.add).not.toBeNull();
    }
  }, SLOW);

  it('stops at a recorded 0 instead of falling through to a snake_case alias', async () => {
    // The alias chain is still wanted -- it just must not treat a plano as a
    // reason to keep looking. Here the camelCase key holds the plano and the
    // snake_case one holds a stale strong power.
    await openChooserWith({
      ...storedRx({ sph: 0 }, { sph: 0 }),
      right_eye: { sph: -4 },
      left_eye: { sph: -4 },
    });
    const rx = await attachAndReadStore();

    expect(rx.rightEye.sphere).toBe(0);
    expect(rx.leftEye.sphere).toBe(0);
    expect(rx.rightEye.sphere).not.toBe(-4);
  }, SLOW);

  it('still falls through a BLANK alias to the value that was recorded', async () => {
    // The control for the test above: the fall-through must survive the fix.
    await openChooserWith({
      ...storedRx({ sph: '' }, { sph: '' }),
      right_eye: { sph: '-4.00' },
      left_eye: { sph: '-4.00' },
    });
    const rx = await attachAndReadStore();

    expect(rx.rightEye.sphere).toBe(-4);
    expect(rx.leftEye.sphere).toBe(-4);
  }, SLOW);
});
