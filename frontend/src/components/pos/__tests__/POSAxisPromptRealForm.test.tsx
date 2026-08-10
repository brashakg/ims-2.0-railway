// ============================================================================
// PATIENT SAFETY: the counter axis prompt is REACHABLE through the real form
// ============================================================================
// The owner chose a blocking prompt over a hard block, deliberately, so a sale
// is never stalled waiting on Clinical. The first cut of that prompt never
// rendered: PrescriptionForm.validateBeforeSubmit ran validateEyePair, which
// rejects "cylinder with no axis" with a transient toast and returns BEFORE
// onSubmit fires. What staff actually met was the pre-existing hard block.
//
// This file deliberately does NOT stub PrescriptionForm. It drives the real
// inputs the way a staff member does -- type SPH and CYL, leave AXIS blank,
// click the submit button -- and asserts the modal opens. It is the inverse of
// the probe that caught the dead modal, so it fails if the wiring is ever
// removed (e.g. if `deferAxisPrompt` is dropped from the POSLayout call site).
//
// The sibling POSAxisPrompt.test.tsx stubs the form on purpose, to test
// POSLayout's gate in isolation for callers that are not this form. The two are
// complementary: this one proves the door opens, that one proves what is behind
// it. Neither alone is sufficient.

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
const MOCK_AUTH = { user: MOCK_USER };
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
      createPrescription: H.createPrescription,
    },
    workshopApi: { createJob: noop, updateFittingDetails: noop },
    adminStoreApi: { listStores: noop, getStoreUsers: () => Promise.resolve([]), getStaff: () => Promise.resolve([]) },
    inventoryApi: { searchByBarcode: noop },
    loyaltyApi: { redeem: noop, getBalance: noop },
  };
});

vi.mock('../../../services/api/walkouts', () => ({
  walkoutsApi: { walkinsPosIncrement: () => Promise.resolve({ total: 1 }) },
}));

import { MemoryRouter } from 'react-router-dom';
import { POSLayout } from '../POSLayout';
import { usePOSStore } from '../../../stores/posStore';
import { ToastProvider } from '../../../context/ToastContext';
import { AXIS_SOURCE_COUNTER } from '../../../utils/rxAxisEntry';

const PROMPT_TITLE = /Axis needed before this prescription can be saved/;

// This suite mounts the REAL PrescriptionForm (a large component) inside the
// real POSLayout, so a mount here costs far more than the stubbed sibling's.
// Under a full parallel suite run that occasionally overran RTL's 5s default
// and failed ~1 run in 10. The waits below are generous on purpose: a slow box
// must not be reported as a missing safety prompt.
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
 * The await before opening the form is load-bearing, not decoration. The Rx
 * step fetches the customer's prescriptions in an effect; if that promise
 * settles AFTER the form is mounted, the resulting re-render remounts
 * PrescriptionForm and its useState-held values are wiped, so the typed CYL
 * vanishes and no prompt is due. That made this suite fail about one run in
 * four. Waiting for the fetch to settle first makes it deterministic.
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
  // The Rx fetch has settled once the empty-state notice is on screen.
  await screen.findByText(/No prescriptions found/, {}, { timeout: SLOW });
  fireEvent.click(screen.getByText('New Prescription'));
}

/** RxPowerInput normalises on blur, so type then blur like a real user. */
function typePower(label: string, value: string) {
  const el = screen.getByLabelText(label);
  fireEvent.change(el, { target: { value } });
  fireEvent.blur(el);
}

beforeEach(() => {
  localStorage.clear();
  H.createPrescription.mockReset();
  H.createPrescription.mockResolvedValue({ prescription_id: 'RX-REAL-1' });
  act(() => usePOSStore.getState().resetTransaction());
});

describe('the real PrescriptionForm routes a missing axis into the prompt', () => {
  it('opens the modal for a toric RIGHT eye with a blank axis', async () => {
    await openRealRxForm();
    typePower('Right eye sphere', '-2.00');
    typePower('Right eye cylinder', '-1.25');
    // AXIS deliberately left blank.
    fireEvent.click(screen.getByRole('button', { name: /Add to Order/ }));

    // The prompt the owner asked for actually renders...
    expect(await screen.findByText(PROMPT_TITLE, {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.getByText(/Right eye \(OD\) has cylinder -1\.25 but no axis/)).toBeInTheDocument();
    // ...and nothing is saved until it is answered.
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('opens the modal for a toric LEFT eye with a blank axis', async () => {
    await openRealRxForm();
    typePower('Left eye sphere', '-1.00');
    typePower('Left eye cylinder', '-0.75');
    fireEvent.click(screen.getByRole('button', { name: /Add to Order/ }));

    expect(await screen.findByText(PROMPT_TITLE, {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.getByText(/Left eye \(OS\) has cylinder -0\.75 but no axis/)).toBeInTheDocument();
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('collects the axis and saves it with per-eye counter provenance', async () => {
    await openRealRxForm();
    typePower('Right eye sphere', '-2.00');
    typePower('Right eye cylinder', '-1.25');
    fireEvent.click(screen.getByRole('button', { name: /Add to Order/ }));
    await screen.findByText(PROMPT_TITLE, {}, { timeout: SLOW });

    fireEvent.change(screen.getByLabelText('Right eye (OD) axis'), { target: { value: '85' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save axis and continue' }));

    await waitFor(() => expect(H.createPrescription).toHaveBeenCalledTimes(1), { timeout: SLOW });
    const body = H.createPrescription.mock.calls[0][0];
    expect(body.right_eye.axis).toBe(85);
    expect(body.right_eye.axis_source).toBe(AXIS_SOURCE_COUNTER);
    // Provenance never travels in the patient-visible remarks.
    expect(String(body.remarks ?? '')).not.toContain(AXIS_SOURCE_COUNTER);
  });
});

describe('deferring the axis relaxes NOTHING else in the real form', () => {
  it('still hard-blocks an axis entered with no cylinder', async () => {
    await openRealRxForm();
    typePower('Right eye sphere', '-2.00');
    typePower('Right eye axis', '90');
    // CYL deliberately blank -- the mirror-image pairing failure.
    fireEvent.click(screen.getByRole('button', { name: /Add to Order/ }));

    // No prompt: this is not a missing axis, it is a missing cylinder.
    await waitFor(() => expect(screen.queryByText(PROMPT_TITLE)).not.toBeInTheDocument());
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('still hard-blocks an out-of-range power on the same eye as the missing axis', async () => {
    await openRealRxForm();
    // SPH well outside the canonical -25..+25 range, plus a toric with no axis.
    typePower('Right eye sphere', '-40.00');
    typePower('Right eye cylinder', '-1.25');
    fireEvent.click(screen.getByRole('button', { name: /Add to Order/ }));

    // The eye has a SECOND problem, so it is rejected outright -- the prompt
    // must not swallow an unrelated clinical error.
    await waitFor(() => expect(screen.queryByText(PROMPT_TITLE)).not.toBeInTheDocument());
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('saves a clean non-toric Rx without any prompt', async () => {
    await openRealRxForm();
    typePower('Right eye sphere', '-2.00');
    fireEvent.click(screen.getByRole('button', { name: /Add to Order/ }));

    await waitFor(() => expect(H.createPrescription).toHaveBeenCalledTimes(1), { timeout: SLOW });
    expect(screen.queryByText(PROMPT_TITLE)).not.toBeInTheDocument();
    const body = H.createPrescription.mock.calls[0][0];
    expect(body.right_eye.axis).toBeNull();
    expect(body.right_eye.axis_source).toBeUndefined();
  });
});
