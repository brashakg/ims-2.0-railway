// ============================================================================
// PATIENT SAFETY: a blank power must not leave the counter as a "0"
// ============================================================================
// SITE 1 of the blank-vs-zero conflation. POSLayout built the create payload as
//
//     sph: String(rxData.sph_od || 0), cyl: ..., add: ...
//
// so an empty SPH / CYL / ADD box was persisted as the STRING "0" -- a positive
// clinical claim that this patient needs no correction, has no astigmatism and
// needs no reading add. Nobody made that claim. (`pd` on the very same line
// already did the right thing: `String(rxData.pd_od || '')`.)
//
// This file does NOT stub PrescriptionForm. It drives the real inputs the way a
// staff member does and asserts on what `prescriptionApi.createPrescription`
// actually receives -- the wire bytes -- plus the local echo POSLayout writes
// into the POS store for the Rx panel to read back. A helper-level test of
// `powerOrNull` alone (utils/__tests__/rxPowerValue.test.ts) proves the parser;
// only this proves POS still calls it.
//
// BOTH EYES, EVERY FIELD. The axis fix last round shipped with the left eye
// unpinned, so per-eye asymmetry is the specific failure this file watches for.

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

const H = vi.hoisted(() => ({ createPrescription: vi.fn() }));

const MOCK_USER = {
  id: 'u1',
  name: 'Asha Kumari',
  roles: ['STORE_MANAGER'],
  activeRole: 'STORE_MANAGER',
  activeStoreId: 'BV-BOK-01',
  storeIds: ['BV-BOK-01'],
  discountCap: 20,
};
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: MOCK_USER }) }));

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
      createPrescription: H.createPrescription,
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

// Mounting the REAL PrescriptionForm inside the real POSLayout is expensive,
// and this box is shared with other crews. Generous waits on purpose: a slow
// machine must never be reported as a missing safety behaviour.
const SLOW = 20000;

function renderPOS() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <POSLayout />
      </ToastProvider>
    </MemoryRouter>,
  );
}

/**
 * Open the REAL New Prescription form, as a staff member would.
 *
 * The await before opening the form is load-bearing: the Rx step fetches the
 * customer's prescriptions in an effect, and a promise settling AFTER the form
 * mounts remounts it and wipes the typed values.
 */
async function openRealRxForm() {
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.setSalesperson('sp1', 'Sales Person');
    s.setSaleType('prescription_order');
    usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as any });
  });
  renderPOS();
  act(() => usePOSStore.getState().setStep('products'));
  fireEvent.click(screen.getByRole('button', { name: 'Use last exam' }));
  await screen.findByText(/No prescriptions found/, {}, { timeout: SLOW });
  fireEvent.click(screen.getByText('New Prescription'));
}

/** RxPowerInput normalises on blur, so type then blur like a real user. */
function typePower(label: string, value: string) {
  const el = screen.getByLabelText(label);
  fireEvent.change(el, { target: { value } });
  fireEvent.blur(el);
}

async function submitAndReadPayload(): Promise<any> {
  fireEvent.click(screen.getByRole('button', { name: /Add to Order/ }));
  await waitFor(() => expect(H.createPrescription).toHaveBeenCalledTimes(1), { timeout: SLOW });
  return H.createPrescription.mock.calls[0][0];
}

beforeEach(() => {
  localStorage.clear();
  H.createPrescription.mockReset();
  H.createPrescription.mockResolvedValue({ prescription_id: 'RX-PLANO-1' });
  act(() => usePOSStore.getState().resetTransaction());
});

describe('a BLANK power is never persisted as a claim of zero', () => {
  it('sends null for every unrecorded SPH / CYL / ADD, on BOTH eyes', async () => {
    await openRealRxForm();
    // One power typed anywhere so the form has something to save. Every other
    // power box is left exactly as staff left it: empty.
    typePower('Right eye sphere', '-2.00');

    const body = await submitAndReadPayload();

    // THE REQUIREMENT, asserted first and per field so nothing shadows it.
    // Each of these was the string "0" before the fix.
    expect(body.right_eye.cyl).toBeNull();
    expect(body.right_eye.add).toBeNull();
    expect(body.left_eye.sph).toBeNull();
    expect(body.left_eye.cyl).toBeNull();
    expect(body.left_eye.add).toBeNull();

    // Said the other way round, because "0" is the exact wrong answer and a
    // future regression could pick a different falsy stand-in.
    for (const eye of [body.right_eye, body.left_eye]) {
      for (const field of ['sph', 'cyl', 'add'] as const) {
        expect(eye[field]).not.toBe('0');
        expect(eye[field]).not.toBe(0);
      }
    }

    // ...and the power that WAS recorded still travels, so a payload that
    // nulled everything would not pass. PrescriptionForm holds Rx fields as
    // NUMBERS (`parseFloat` on change, `undefined` for empty), so the wire
    // carries the number's own string form, not the input's display text.
    expect(body.right_eye.sph).toBe('-2');
  }, SLOW);

  it('nulls the RIGHT eye too when only the LEFT eye was examined', async () => {
    // The mirror image of the case above. A fix applied to one eye only would
    // pass that test and fail this one.
    await openRealRxForm();
    typePower('Left eye sphere', '-1.00');

    const body = await submitAndReadPayload();

    expect(body.right_eye.sph).toBeNull();
    expect(body.right_eye.cyl).toBeNull();
    expect(body.right_eye.add).toBeNull();
    expect(body.left_eye.sph).toBe('-1');
  }, SLOW);

  it('echoes a blank power into the POS store as null, not as a fabricated 0', async () => {
    // The local echo POSLayout writes for the Rx panel to read back carried
    // BOTH halves of the bug: `sph || 0` invented a plano for a blank while
    // `cyl || null` / `add || null` deleted a recorded one.
    await openRealRxForm();
    typePower('Right eye sphere', '-2.00');
    await submitAndReadPayload();

    await waitFor(() => expect(usePOSStore.getState().prescription).toBeTruthy(), { timeout: SLOW });
    const rx: any = usePOSStore.getState().prescription;

    // THE REQUIREMENT: an unrecorded sphere is null on BOTH eyes -- it was 0.
    expect(rx.leftEye.sphere).toBeNull();
    expect(rx.leftEye.sphere).not.toBe(0);
    expect(rx.rightEye.sphere).toBe(-2);
  }, SLOW);
});

// A plano entered at the counter is SPH 0 / CYL 0. It is NOT "ADD 0": the
// canonical Rx limits make a near add plus-only from +0.75 (constants/rxLimits
// RX_LIMITS.add), so this form rejects a zero ADD outright and the state never
// reaches the payload. That is a deliberate clinical rule, not the conflation
// under repair -- a zero ADD still has to survive on the paths that DO carry
// one (a stored/imported Rx, the clinical exam card, the patient portal), which
// is where the sibling suites pin it.
function typePlanoBothEyes() {
  for (const eye of ['Right eye', 'Left eye']) {
    typePower(`${eye} sphere`, '0');
    typePower(`${eye} cylinder`, '0');
  }
}

describe('a RECORDED 0 survives the counter intact', () => {
  it('keeps a plano SPH and a zero CYL on BOTH eyes, beside a blank ADD', async () => {
    await openRealRxForm();
    typePlanoBothEyes();

    const body = await submitAndReadPayload();

    // THE REQUIREMENT: not one of these may be re-badged as "not recorded".
    for (const eye of [body.right_eye, body.left_eye]) {
      for (const field of ['sph', 'cyl'] as const) {
        expect(eye[field]).not.toBeNull();
        expect(eye[field]).not.toBeUndefined();
        expect(Number(eye[field]) === 0).toBe(true);
      }
    }
    // The value the counter actually persists for a plano.
    expect(body.right_eye.sph).toBe('0');
    expect(body.left_eye.cyl).toBe('0');

    // BOTH STATES IN ONE EYE, which is the whole point of the ticket: the
    // measured zeros above are kept, and the ADD nobody recorded stays absent.
    expect(body.right_eye.add).toBeNull();
    expect(body.left_eye.add).toBeNull();

    // HONESTY ABOUT THIS TEST'S POWER. Reverting the site-1 fix does NOT turn
    // the sph/cyl assertions red, and that is a fact about the DATA, not a
    // coverage hole: PrescriptionForm has already parsed the plano to the
    // NUMBER 0, and the old `String(rxData.sph_od || 0)` also answered "0" for
    // it. Site 1's wire payload only ever corrupted the BLANK direction -- and
    // the `add` assertions two lines up DO fail on a revert. The zero
    // direction proper is discriminated by the store echo below and by
    // utils/__tests__/rxPowerValue.test.ts.
  }, SLOW);

  it('echoes a recorded 0 into the POS store as numeric 0, not null', async () => {
    // The direction the echo used to destroy: `cyl || null` / `add || null`.
    await openRealRxForm();
    typePlanoBothEyes();
    await submitAndReadPayload();

    await waitFor(() => expect(usePOSStore.getState().prescription).toBeTruthy(), { timeout: SLOW });
    const rx: any = usePOSStore.getState().prescription;

    // THE REQUIREMENT, both eyes. CYLINDER is the discriminating field: the old
    // echo read `cylinder: rxData.cyl_od || null`, so a measured ZERO
    // astigmatism was deleted and the Rx panel showed it as never measured.
    // (SPHERE was `sph || 0`, which happens to answer 0 for a plano too, so it
    // is a guard here; the blank case above is what discriminates sphere.)
    for (const eye of [rx.rightEye, rx.leftEye]) {
      expect(eye.cylinder).toBe(0);
      expect(eye.cylinder).not.toBeNull();
      expect(eye.sphere).toBe(0);
      // ...and the ADD nobody recorded is still absent, in the same eye.
      expect(eye.add).toBeNull();
    }
  }, SLOW);
});
