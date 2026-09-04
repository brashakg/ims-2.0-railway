// ============================================================================
// PATIENT SAFETY: reading a STORED Rx back at the counter
// ============================================================================
// The blank-vs-zero conflation on the READ side. The legacy till's attach
// mapper read `parseFloat(a || b || c || '0')`, which fabricated a confident
// plano 0.00 for an Rx that recorded no sphere / cylinder / add, and its
// chooser printed '0.00' for anything falsy, so absence and plano printed
// IDENTICALLY at the exact moment a staff member picks which prescription to
// dispense.
//
// The till is BillingSurface (the legacy wizard was retired 2026-09-04). Its
// read-back path is PrescriptionSelectModal -> mapRx (services/api/sales.ts)
// -> store.setPrescription, and its CONTRACT is different from the old
// mapper's: the backend's strings ride through untouched ('-1.25', '0.00'),
// and a power the Rx never recorded is simply ABSENT (undefined). Same rule,
// pinned on the new contract: a recorded zero is kept as a recorded zero, an
// absent power stays absent, and nothing on this path ever manufactures a 0.
//
// AXIS gets the same treatment. `{axis || '-'}` on the picker row printed a
// RECORDED axis of 0 as "not recorded" -- the zero-vs-blank defect this repo
// has now fixed on four screens. Caught on the new till by the cases below.
//
// This file drives the REAL door: Rx card -> picker rows (what staff read) ->
// tap -> the prescription that lands on the sale.

import { render, screen, act, fireEvent, waitFor, within } from '@testing-library/react';
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

const H = vi.hoisted(() => ({ getFamilyRx: vi.fn() }));

// Hoisted to a STABLE object on purpose. Returning a fresh `{ user: {...} }`
// from useAuth on every render changes the identity the surface's effects
// depend on, and the component re-enters setStoreId forever ("Maximum update
// depth exceeded") -- a test-harness fault that looks exactly like a product bug.
const MOCK_USER = {
  id: 'u1', name: 'Asha Kumari', roles: ['STORE_MANAGER'], activeRole: 'STORE_MANAGER',
  activeStoreId: 'BV-BOK-01', storeIds: ['BV-BOK-01'], discountCap: 20,
};
const MOCK_AUTH = {
  user: MOCK_USER,
  // CustomerCardWithLoyalty gates its edit door on hasRole.
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
      getPrescriptions: () => Promise.resolve({ prescriptions: [] }),
      getFamilyRx: H.getFamilyRx,
      createPrescription: () => Promise.resolve({ prescription_id: 'RX-NEW' }),
    },
    workshopApi: { createJob: noop, updateFittingDetails: noop },
    adminStoreApi: { listStores: noop, getStoreUsers: () => Promise.resolve([]), getStaff: () => Promise.resolve([]) },
    inventoryApi: { searchByBarcode: noop },
    loyaltyApi: { redeem: noop, getBalance: noop },
    storeApi: { getStore: () => Promise.resolve({ store_id: 'BV-BOK-01' }) },
  };
});

// Leaves of the surface that fetch through DIRECT module imports, not the
// barrel: loyalty (customer card + loyalty tender), customers (search bar +
// store-credit tender), handoffs (Rx picker). Inert -- none is under test.
vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: {
    getAccount: () => Promise.resolve({
      account: { balance_points: 0, tier: 'BRONZE' }, settings: {}, expiring_soon_points: 0,
    }),
  },
}));
vi.mock('../../../services/api/customers', () => ({
  customerApi: {
    getCustomers: () => Promise.resolve([]),
    getCustomer: () => Promise.resolve(null),
    createCustomer: () => Promise.resolve({}),
    getStoreCreditLedger: () => Promise.resolve({ balance: 0 }),
  },
  customersApi: {},
}));
vi.mock('../../../services/api/handoffs', () => ({
  handoffsApi: { listClinicalInbox: () => Promise.resolve({ handoffs: [] }) },
}));

vi.mock('../../../services/api/walkouts', () => ({
  walkoutsApi: { walkinsPosIncrement: () => Promise.resolve({ total: 1 }) },
}));

vi.mock('../../../services/api/settings', () => ({
  policiesApi: { getOne: () => Promise.resolve({ value: 12 }) },
}));

// Off-assertion strips that fetch through modules the barrel mock does not cover.
vi.mock('../../../pages/pos/next/PosWidgets', () => ({ PosWidgets: () => null }));
vi.mock('../../../pages/pos/next/SaleCompleteScreen', () => ({ default: () => null }));

import { MemoryRouter } from 'react-router-dom';
import { BillingSurface } from '../../../pages/pos/next/BillingSurface';
import { usePOSStore } from '../../../stores/posStore';
import { ToastProvider } from '../../../context/ToastContext';
import { hasRecordedPower } from '../../../utils/rxPowerValue';

const SLOW = 20000;

/** A stored prescription in the BACKEND wire shape (snake_case, string
 *  powers, int|null axis), dated recently so the validity filter keeps it. */
function storedRx(right: Record<string, unknown>, left: Record<string, unknown>) {
  return {
    prescription_id: 'RX-STORED-1',
    test_date: new Date().toISOString(),
    validity_months: 12,
    source: 'TESTED_AT_STORE',
    optometrist_name: 'Dr Rao',
    right_eye: right,
    left_eye: left,
  };
}

/** Open the picker with `rx` on file. Resolves once the Rx row has painted. */
async function openPickerWith(rx: Record<string, unknown>) {
  H.getFamilyRx.mockResolvedValue({
    members: [{
      patient_id: 'p1', name: 'Asha', relation: 'SELF',
      prescription_count: 1, valid_count: 1, prescriptions: [rx],
    }],
  });
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.setSalesperson('sp1', 'Sales Person');
    usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as any });
  });
  render(
    <MemoryRouter>
      <ToastProvider>
        <BillingSurface />
      </ToastProvider>
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /Choose or add/ }, { timeout: SLOW }));
  await screen.findByText('Valid', {}, { timeout: SLOW });
}

/** The picker row's eye block (label + values) for eye 0 = right, 1 = left,
 *  as one string: "SPH-2.00CYL-1.25AXIS90ADD+0.00". */
function eyeRowText(index: number): string {
  const axisLabel = screen.getAllByText('AXIS')[index];
  return (axisLabel.closest('div.flex') as HTMLElement).textContent ?? '';
}

/** Tap the row: what lands on the sale. */
async function attachAndReadStore(): Promise<any> {
  fireEvent.click(screen.getByText('Valid').closest('button')!);
  await waitFor(() => expect(usePOSStore.getState().prescription).toBeTruthy(), { timeout: SLOW });
  return usePOSStore.getState().prescription;
}

beforeEach(() => {
  localStorage.clear();
  H.getFamilyRx.mockReset();
  act(() => usePOSStore.getState().resetTransaction());
});

describe('the picker row tells absence apart from plano', () => {
  it('shows a dash - never "+0.00" - for a power the Rx did not record', async () => {
    // THE REQUIREMENT. A picker that printed a confident plano here, for a
    // prescription that measured nothing, is what the old chooser did.
    await openPickerWith(storedRx({ cyl: '-1.25', axis: 90 }, {}));

    expect(eyeRowText(0)).toContain('SPH-CYL-1.25');
    expect(eyeRowText(1)).toContain('SPH-CYL-AXIS-');
    for (const i of [0, 1]) expect(eyeRowText(i)).not.toContain('+0.00');
  }, SLOW);

  it('shows +0.00 for a power recorded AS zero, on BOTH eyes', async () => {
    await openPickerWith(storedRx({ sph: '0.00' }, { sph: 0 }));

    // THE REQUIREMENT: a measured plano is shown as one...
    expect(eyeRowText(0)).toContain('SPH+0.00');
    expect(eyeRowText(1)).toContain('SPH+0.00');
    // ...and the two states are not the same string.
    expect(eyeRowText(0)).not.toContain('SPH-CYL');
  }, SLOW);

  it('shows a RECORDED axis of 0 as 0 - and an absent axis as a dash - on BOTH eyes', async () => {
    // The fix this file pins on the new till: `{axis || '-'}` printed a
    // recorded 0 as "not recorded". 0 is a real meridian.
    await openPickerWith(storedRx({ sph: '-2.00', cyl: '-1.25', axis: 0 }, { sph: '-1.00', cyl: '-0.75', axis: 0 }));
    expect(eyeRowText(0)).toContain('AXIS0');
    expect(eyeRowText(1)).toContain('AXIS0');
  }, SLOW);

  it('shows a dash for an axis the Rx never recorded, on BOTH eyes', async () => {
    // The control: without it a picker that printed 0 for EVERY axis would
    // pass the case above.
    await openPickerWith(storedRx({ sph: '-2.00', cyl: '-1.25', axis: null }, { sph: '-1.00', cyl: '-0.75' }));
    expect(eyeRowText(0)).toContain('AXIS-');
    expect(eyeRowText(0)).not.toContain('AXIS0');
    expect(eyeRowText(1)).toContain('AXIS-');
    expect(eyeRowText(1)).not.toContain('AXIS0');
  }, SLOW);
});

describe('attaching puts the RECORDED prescription on the sale', () => {
  it('attaches nothing at all - not a fabricated 0 - for powers the Rx never recorded', async () => {
    // THE REQUIREMENT. This is the copy the till's Rx card prints and the
    // lab job is keyed to, so an invented plano here is a wrong lens.
    await openPickerWith(storedRx({ cyl: '-1.25', axis: 90 }, {}));
    const rx = await attachAndReadStore();

    expect(hasRecordedPower(rx.rightEye.sphere)).toBe(false);
    expect(hasRecordedPower(rx.rightEye.add)).toBe(false);
    expect(hasRecordedPower(rx.leftEye.sphere)).toBe(false);
    expect(hasRecordedPower(rx.leftEye.cylinder)).toBe(false);
    expect(hasRecordedPower(rx.leftEye.add)).toBe(false);
    for (const eye of [rx.rightEye, rx.leftEye]) {
      for (const f of ['sphere', 'cylinder', 'add'] as const) {
        expect(eye[f]).not.toBe(0);
        expect(eye[f]).not.toBe('0');
        expect(eye[f]).not.toBe('0.00');
      }
    }
    // ...and what WAS recorded still attaches, exactly as the backend holds it.
    expect(Number(rx.rightEye.cylinder)).toBe(-1.25);
    expect(rx.rightEye.axis).toBe(90);
  }, SLOW);

  it('attaches a recorded 0 as a recorded zero, on BOTH eyes', async () => {
    await openPickerWith(
      storedRx(
        { sph: '0.00', cyl: '0.00', add: 0, axis: 0 },
        { sph: 0, cyl: '0.00', add: '0.00', axis: 0 },
      ),
    );
    const rx = await attachAndReadStore();

    // THE REQUIREMENT: not one of these may be re-badged as "not recorded".
    for (const eye of [rx.rightEye, rx.leftEye]) {
      for (const f of ['sphere', 'cylinder', 'add'] as const) {
        expect(hasRecordedPower(eye[f])).toBe(true);
        expect(Number(eye[f])).toBe(0);
      }
      expect(eye.axis).toBe(0);
    }
  }, SLOW);

  it('reads the backend key for each power and stops at a recorded 0 there', async () => {
    // The wire carries `sph` / `cyl` / `add`; the store wants sphere /
    // cylinder / add. The mapping must not treat a plano `sph` of 0 as a
    // reason to keep looking, and must not lose it.
    await openPickerWith(storedRx({ sph: 0, cyl: 0 }, { sph: 0, cyl: 0 }));
    const rx = await attachAndReadStore();

    expect(rx.rightEye.sphere).toBe(0);
    expect(rx.rightEye.cylinder).toBe(0);
    expect(rx.leftEye.sphere).toBe(0);
    expect(rx.leftEye.cylinder).toBe(0);
  }, SLOW);

  it('the Rx card on the till then prints the plano as 0.00 and the absent power as a dash', async () => {
    // What staff read on the bill after attaching: one eye measured plano,
    // the other eye's sphere never measured. The two must not print alike.
    await openPickerWith(storedRx({ sph: '0.00', cyl: '-0.50', axis: 180 }, { cyl: '-0.50', axis: 180 }));
    await attachAndReadStore();

    // The Rx card's power table is the only <table> on the till.
    const table = await screen.findByRole('table', {}, { timeout: SLOW });
    const rows = within(table).getAllByRole('row').slice(1); // header row first
    expect(rows).toHaveLength(2);
    const [right, left] = rows.map((r) => within(r).getAllByRole('cell').map((c) => c.textContent));
    // R: SPH 0.00 (recorded plano), L: SPH — (never measured).
    expect(right[1]).toBe('0.00');
    expect(left[1]).toBe('—');
  }, SLOW);
});
