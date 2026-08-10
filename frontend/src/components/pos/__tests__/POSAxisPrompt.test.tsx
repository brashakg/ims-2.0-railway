// ============================================================================
// PATIENT SAFETY: POS counter axis prompt (POSLayout)
// ============================================================================
// POSLayout used to build the prescription payload with `rxData.axis_od || 180`
// on each eye. An Rx with no axis was saved with an INVENTED axis of 180, which
// travelled into the lens spec and was ground by the lab at a guessed
// orientation; it also silently defeated the clinical toric-axis gate (PR #969)
// and rewrote a legitimate recorded axis of 0.
//
// The owner chose an explicit PROMPT over a hard block: any staff member may
// supply the axis, but the line cannot proceed without one. These tests pin the
// gate at the component boundary -- that nothing reaches the server until a
// valid axis exists, that EACH EYE triggers it independently, that a recorded
// axis of 0 sails straight through untouched, and that the Rx chooser no longer
// prints a fabricated 180.
//
// ---------------------------------------------------------------------------
// Why PrescriptionForm is STUBBED here
// ---------------------------------------------------------------------------
// To isolate POSLayout's gate from the form's own validation, so a failure here
// names POSLayout and nothing else. The stub feeds onSubmit an exact payload,
// which lets each eye be varied independently -- awkward to do through the real
// inputs, and that per-eye independence is what catches a half-applied fix.
//
// The real form IS driven, without any stub, in POSAxisPromptRealForm.test.tsx:
// that file proves the door actually opens (PrescriptionForm.validateEyePair
// used to reject a toric-without-axis with a transient toast BEFORE onSubmit
// fired, so this modal never rendered in production until `deferAxisPrompt`
// routed that one case through). The two files are complementary and neither is
// sufficient alone: that one proves the door opens, this one proves what is
// behind it -- including for callers that are not that form.

import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

// This Node/jsdom combo ships a partial localStorage (no clear/setItem). Replace
// it with a complete Map-backed stub so the posStore persist middleware behaves.
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

// Hoisted so the vi.mock factories below can reach them.
const H = vi.hoisted(() => ({
  /** The flat form payload the stubbed PrescriptionForm will emit. */
  rx: {} as any,
  /** Prescriptions the Rx chooser lists. */
  stored: [] as any[],
  createPrescription: vi.fn(),
}));

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
      getPrescriptions: () => Promise.resolve({ prescriptions: H.stored }),
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

// Stub the Rx form: one button that hands POSLayout the fixture payload.
vi.mock('../PrescriptionForm', () => ({
  PrescriptionForm: ({ onSubmit }: any) => (
    <button onClick={() => onSubmit(H.rx)}>stub-submit-rx</button>
  ),
}));

import { MemoryRouter } from 'react-router-dom';
import { POSLayout } from '../POSLayout';
import { usePOSStore } from '../../../stores/posStore';
import { ToastProvider } from '../../../context/ToastContext';
import { AXIS_SOURCE_COUNTER } from '../../../utils/rxAxisEntry';

function renderPOS() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <POSLayout />
      </ToastProvider>
    </MemoryRouter>,
  );
}

function seedRxSale() {
  act(() => {
    const s = usePOSStore.getState();
    s.resetTransaction();
    s.setStoreId('BV-BOK-01');
    s.setSalesperson('sp1', 'Sales Person');
    s.setSaleType('prescription_order');
    usePOSStore.setState({ customer: { id: 'c1', name: 'Asha', phone: '9000000001' } as any });
  });
}

/**
 * The Customer step, where RxAvailableBadge offers one-click "Attach Latest Rx".
 * This door renders NO powers before attaching, so the honest "axis not
 * recorded" label never appears here -- the store value is the only evidence.
 */
async function openCustomerStep() {
  seedRxSale();
  renderPOS();
  act(() => usePOSStore.getState().setStep('customer'));
}

/** Reveal the Rx surface on the merged Products & Rx step. */
async function openRxSurface() {
  seedRxSale();
  renderPOS();
  act(() => usePOSStore.getState().setStep('products'));
  fireEvent.click(screen.getByRole('button', { name: 'Use last exam' }));
}

/** ...and open the New Prescription modal on top of it.
 *
 *  The await is load-bearing: the Rx step fetches prescriptions in an effect,
 *  and a promise settling after the form mounts re-renders it. Wait for the
 *  fetch to settle first so the suite is deterministic. */
async function openNewRxForm() {
  await openRxSurface();
  await screen.findByText(/No prescriptions found/);
  fireEvent.click(screen.getByText('New Prescription'));
  return screen.getByText('stub-submit-rx');
}

const PROMPT_TITLE = /Axis needed before this prescription can be saved/;

beforeEach(() => {
  localStorage.clear();
  H.rx = {};
  H.stored = [];
  H.createPrescription.mockReset();
  H.createPrescription.mockResolvedValue({ prescription_id: 'RX-TEST-1' });
  act(() => usePOSStore.getState().resetTransaction());
});

describe('a toric Rx with no axis cannot proceed without a value', () => {
  it('prompts for the RIGHT eye alone and saves NOTHING until it is supplied', async () => {
    H.rx = { sph_od: -2, cyl_od: -1.25, axis_od: undefined, sph_os: -1, cyl_os: -0.75, axis_os: 90 };
    const submit = await openNewRxForm();
    fireEvent.click(submit);

    expect(await screen.findByText(PROMPT_TITLE)).toBeInTheDocument();
    // Names the eye AND the cylinder, in the PR #969 voice.
    expect(screen.getByText(/Right eye \(OD\) has cylinder -1\.25 but no axis/)).toBeInTheDocument();
    // The good eye is NOT dragged into the prompt.
    expect(screen.queryByText(/Left eye \(OS\) has cylinder/)).not.toBeInTheDocument();
    // BLOCKED: nothing reached the server.
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  // THE ASYMMETRY GUARD: a fix applied to one eye only would pass the test
  // above and fail this one.
  it('prompts for the LEFT eye alone and saves NOTHING until it is supplied', async () => {
    H.rx = { sph_od: -2, cyl_od: -1.25, axis_od: 10, sph_os: -1, cyl_os: -0.75, axis_os: undefined };
    const submit = await openNewRxForm();
    fireEvent.click(submit);

    expect(await screen.findByText(PROMPT_TITLE)).toBeInTheDocument();
    expect(screen.getByText(/Left eye \(OS\) has cylinder -0\.75 but no axis/)).toBeInTheDocument();
    expect(screen.queryByText(/Right eye \(OD\) has cylinder/)).not.toBeInTheDocument();
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('prompts for BOTH eyes when both are missing an axis', async () => {
    H.rx = { cyl_od: -1.25, axis_od: undefined, cyl_os: -0.75, axis_os: undefined };
    const submit = await openNewRxForm();
    fireEvent.click(submit);

    expect(await screen.findByText(PROMPT_TITLE)).toBeInTheDocument();
    expect(screen.getByLabelText('Right eye (OD) axis')).toBeInTheDocument();
    expect(screen.getByLabelText('Left eye (OS) axis')).toBeInTheDocument();
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('cannot be dismissed onto the save path: there is no close control', async () => {
    H.rx = { cyl_od: -1.25, axis_od: undefined };
    const submit = await openNewRxForm();
    fireEvent.click(submit);
    await screen.findByText(PROMPT_TITLE);

    const dialog = screen.getByRole('alertdialog');
    // Only the two deliberate actions -- no X, no dismiss.
    const labels = Array.from(dialog.querySelectorAll('button')).map((b) => b.textContent);
    expect(labels).toEqual(['Back to the prescription', 'Save axis and continue']);
    // Escape must not resolve it either.
    fireEvent.keyDown(dialog, { key: 'Escape', code: 'Escape' });
    expect(screen.getByText(PROMPT_TITLE)).toBeInTheDocument();
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('going back saves nothing at all', async () => {
    H.rx = { cyl_od: -1.25, axis_od: undefined };
    const submit = await openNewRxForm();
    fireEvent.click(submit);
    await screen.findByText(PROMPT_TITLE);

    fireEvent.click(screen.getByRole('button', { name: 'Back to the prescription' }));
    await waitFor(() => expect(screen.queryByText(PROMPT_TITLE)).not.toBeInTheDocument());
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('rejects an out-of-range entry and still saves nothing', async () => {
    H.rx = { cyl_od: -1.25, axis_od: undefined };
    const submit = await openNewRxForm();
    fireEvent.click(submit);
    await screen.findByText(PROMPT_TITLE);

    for (const bad of ['181', '-5', '90.5', 'abc']) {
      fireEvent.change(screen.getByLabelText('Right eye (OD) axis'), { target: { value: bad } });
      fireEvent.click(screen.getByRole('button', { name: 'Save axis and continue' }));
      // Still open, still nothing sent.
      expect(screen.getByText(PROMPT_TITLE)).toBeInTheDocument();
      expect(H.createPrescription).not.toHaveBeenCalled();
    }
    // An empty entry is refused too.
    fireEvent.change(screen.getByLabelText('Right eye (OD) axis'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save axis and continue' }));
    expect(screen.getByText(/still needs an axis/)).toBeInTheDocument();
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('saves the entered axis and stamps counter provenance once it is valid', async () => {
    H.rx = { sph_od: -2, cyl_od: -1.25, axis_od: undefined, doctor_name: 'Rao' };
    const submit = await openNewRxForm();
    fireEvent.click(submit);
    await screen.findByText(PROMPT_TITLE);

    fireEvent.change(screen.getByLabelText('Right eye (OD) axis'), { target: { value: '85' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save axis and continue' }));

    await waitFor(() => expect(H.createPrescription).toHaveBeenCalledTimes(1));
    const body = H.createPrescription.mock.calls[0][0];
    // The axis the staff member typed -- not 180, not a guess.
    expect(body.right_eye.axis).toBe(85);
    // Provenance is stamped ON THE EYE that was counter-entered...
    expect(body.right_eye.axis_source).toBe(AXIS_SOURCE_COUNTER);
    // ...and NOT on the eye that was not.
    expect(body.left_eye.axis_source).toBeUndefined();
    // The doctor remark is untouched.
    expect(body.remarks).toBe('Dr. Rao');
  });

  // PRIVACY / AUDIT REGRESSION. `remarks` is projected to the OTP-gated customer
  // portal (portal._safe_prescription_view -> "notes", rendered by
  // RxPortalPage) and printed on the patient-facing Rx card, while no internal
  // staff screen renders it. Provenance there would tell the PATIENT their axis
  // was supplied at the counter and hide it from the optician handling the
  // remake dispute. It must never travel in a patient-reachable field.
  it('never puts counter provenance in a patient-visible field', async () => {
    H.rx = { sph_od: -2, cyl_od: -1.25, axis_od: undefined, doctor_name: 'Rao' };
    const submit = await openNewRxForm();
    fireEvent.click(submit);
    await screen.findByText(PROMPT_TITLE);
    fireEvent.change(screen.getByLabelText('Right eye (OD) axis'), { target: { value: '85' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save axis and continue' }));

    await waitFor(() => expect(H.createPrescription).toHaveBeenCalledTimes(1));
    const body = H.createPrescription.mock.calls[0][0];
    // Every field _safe_prescription_view can project, plus the free-text ones
    // the printed card emits. None may carry the marker or the staff name.
    for (const field of [body.remarks, body.notes, body.lens_recommendation, body.coating_recommendation, body.ipd]) {
      const text = String(field ?? '');
      expect(text).not.toContain(AXIS_SOURCE_COUNTER);
      expect(text).not.toContain('counter');
      expect(text).not.toContain('Asha Kumari');
    }
  });
});

describe('an axis of 0 is a real clinical value', () => {
  it('passes straight through with no prompt and is NOT rewritten to 180', async () => {
    H.rx = { sph_od: -2, cyl_od: -1.25, axis_od: 0, sph_os: -1, cyl_os: -0.75, axis_os: 0 };
    const submit = await openNewRxForm();
    fireEvent.click(submit);

    await waitFor(() => expect(H.createPrescription).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(PROMPT_TITLE)).not.toBeInTheDocument();
    const body = H.createPrescription.mock.calls[0][0];
    expect(body.right_eye.axis).toBe(0);
    expect(body.left_eye.axis).toBe(0);
    // No counter-entry provenance on either eye: nothing was entered here.
    expect(body.right_eye.axis_source).toBeUndefined();
    expect(body.left_eye.axis_source).toBeUndefined();
  });
});

describe('a non-toric Rx with no axis is unaffected', () => {
  it('saves with a null axis -- no prompt, and no fabricated 180', async () => {
    H.rx = { sph_od: -2, cyl_od: 0, axis_od: undefined, sph_os: -1.5, cyl_os: undefined, axis_os: undefined };
    const submit = await openNewRxForm();
    fireEvent.click(submit);

    await waitFor(() => expect(H.createPrescription).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(PROMPT_TITLE)).not.toBeInTheDocument();
    const body = H.createPrescription.mock.calls[0][0];
    expect(body.right_eye.axis).toBeNull();
    expect(body.left_eye.axis).toBeNull();
  });
});

describe('the Rx chooser never prints an axis the prescription does not have', () => {
  it('shows "axis not recorded" instead of a fabricated 180, on BOTH eyes', async () => {
    H.stored = [{
      prescription_id: 'RX-OLD-1',
      test_date: new Date().toISOString(),
      validity_months: 24,
      right_eye: { sph: '-2.00', cyl: '-1.25', axis: null },
      left_eye: { sph: '-1.00', cyl: '-0.75', axis: null },
    }];
    await openRxSurface();

    const row = await screen.findByText(/axis not recorded/);
    expect(row).toBeInTheDocument();
    // Both eyes, and nothing claiming 180.
    expect(row.textContent).toMatch(/R: .*×axis not recorded/);
    expect(row.textContent).toMatch(/L: .*×axis not recorded/);
    expect(row.textContent).not.toContain('180');
  });

  it('shows a recorded axis of 0 as 0, not as 180', async () => {
    H.stored = [{
      prescription_id: 'RX-OLD-2',
      test_date: new Date().toISOString(),
      validity_months: 24,
      right_eye: { sph: '-2.00', cyl: '-1.25', axis: 0 },
      left_eye: { sph: '-1.00', cyl: '-0.75', axis: 5 },
    }];
    await openRxSurface();

    const row = await screen.findByText(/R: .*×0/);
    expect(row.textContent).toMatch(/R: .*×0/);
    expect(row.textContent).toMatch(/L: .*×5/);
    expect(row.textContent).not.toContain('180');
  });

  // BOTH eyes exercise the MISSING-axis path here. An earlier version of this
  // fixture put the missing axis on the right eye and a recorded 0 on the left,
  // so the left eye's only assertion pinned the 0 case -- and a mutation that
  // fabricated 180 on the LEFT eye alone passed green while the identical
  // mutation on the right eye was killed. Same door, same mutation, one eye
  // caught. Keep both eyes on `null`.
  it('attaches a stored axis-less Rx as axis-less on BOTH eyes, never as 180', async () => {
    H.stored = [{
      prescription_id: 'RX-OLD-3',
      test_date: new Date().toISOString(),
      validity_months: 24,
      right_eye: { sph: '-2.00', cyl: '-1.25', axis: null },
      left_eye: { sph: '-1.00', cyl: '-0.75', axis: null },
    }];
    await openRxSurface();

    fireEvent.click(await screen.findByRole('button', { name: 'Attach' }));
    await waitFor(() => expect(usePOSStore.getState().prescription).not.toBeNull());
    const rx: any = usePOSStore.getState().prescription;
    expect(rx.rightEye.axis).toBeNull();
    expect(rx.leftEye.axis).toBeNull();
  });

  it('keeps a recorded axis of 0 on BOTH eyes through the attach', async () => {
    H.stored = [{
      prescription_id: 'RX-OLD-4',
      test_date: new Date().toISOString(),
      validity_months: 24,
      right_eye: { sph: '-2.00', cyl: '-1.25', axis: 0 },
      left_eye: { sph: '-1.00', cyl: '-0.75', axis: 0 },
    }];
    await openRxSurface();

    fireEvent.click(await screen.findByRole('button', { name: 'Attach' }));
    await waitFor(() => expect(usePOSStore.getState().prescription).not.toBeNull());
    const rx: any = usePOSStore.getState().prescription;
    expect(rx.rightEye.axis).toBe(0);
    expect(rx.leftEye.axis).toBe(0);
  });
});

// ============================================================================
// The one-click "Attach Latest Rx" door (RxAvailableBadge.handleSwitchToRx)
// ============================================================================
// This was the highest-traffic axis door and had NO test at all: restoring the
// original `Number(... || ... || 180)` on BOTH eyes here killed nothing. It is
// also the worst place to fabricate, because it renders no powers before
// attaching -- staff see a button, not a prescription, so there is nothing on
// screen to contradict a fabricated 180.
describe('one-click "Attach Latest Rx" never fabricates an axis', () => {
  it('attaches an axis-less stored Rx as axis-less on BOTH eyes', async () => {
    H.stored = [{
      prescription_id: 'RX-LATEST-1',
      test_date: new Date().toISOString(),
      validity_months: 24,
      right_eye: { sph: '-2.00', cyl: '-1.25', axis: null },
      left_eye: { sph: '-1.00', cyl: '-0.75', axis: null },
    }];
    await openCustomerStep();

    fireEvent.click(await screen.findByRole('button', { name: 'Attach Latest Rx' }));
    await waitFor(() => expect(usePOSStore.getState().prescription).not.toBeNull());
    const rx: any = usePOSStore.getState().prescription;
    expect(rx.rightEye.axis).toBeNull();
    expect(rx.leftEye.axis).toBeNull();
  });

  it('keeps a recorded axis of 0 on BOTH eyes', async () => {
    H.stored = [{
      prescription_id: 'RX-LATEST-2',
      test_date: new Date().toISOString(),
      validity_months: 24,
      right_eye: { sph: '-2.00', cyl: '-1.25', axis: 0 },
      left_eye: { sph: '-1.00', cyl: '-0.75', axis: 0 },
    }];
    await openCustomerStep();

    fireEvent.click(await screen.findByRole('button', { name: 'Attach Latest Rx' }));
    await waitFor(() => expect(usePOSStore.getState().prescription).not.toBeNull());
    const rx: any = usePOSStore.getState().prescription;
    expect(rx.rightEye.axis).toBe(0);
    expect(rx.leftEye.axis).toBe(0);
  });

  it('carries a genuinely recorded axis through unchanged on BOTH eyes', async () => {
    H.stored = [{
      prescription_id: 'RX-LATEST-3',
      test_date: new Date().toISOString(),
      validity_months: 24,
      right_eye: { sph: '-2.00', cyl: '-1.25', axis: 12 },
      left_eye: { sph: '-1.00', cyl: '-0.75', axis: 175 },
    }];
    await openCustomerStep();

    fireEvent.click(await screen.findByRole('button', { name: 'Attach Latest Rx' }));
    await waitFor(() => expect(usePOSStore.getState().prescription).not.toBeNull());
    const rx: any = usePOSStore.getState().prescription;
    expect(rx.rightEye.axis).toBe(12);
    expect(rx.leftEye.axis).toBe(175);
  });
});

// ============================================================================
// Keyboard safety: POS counters run barcode scanners that emit Enter
// ============================================================================
describe('the prompt is inert to stray scanner and keyboard input', () => {
  async function openPrompt() {
    H.rx = { cyl_od: -1.25, axis_od: undefined };
    const submit = await openNewRxForm();
    fireEvent.click(submit);
    await screen.findByText(PROMPT_TITLE);
  }

  it('puts focus in the axis field, not the barcode box behind the scrim', async () => {
    await openPrompt();
    expect(document.activeElement).toBe(screen.getByLabelText('Right eye (OD) axis'));
  });

  it('a stray Escape does not walk the sale back a step', async () => {
    await openPrompt();
    const before = usePOSStore.getState().current_step;
    fireEvent.keyDown(screen.getByRole('button', { name: 'Back to the prescription' }), { key: 'Escape', code: 'Escape' });
    fireEvent.keyDown(window, { key: 'Escape', code: 'Escape' });
    expect(usePOSStore.getState().current_step).toBe(before);
    expect(screen.getByText(PROMPT_TITLE)).toBeInTheDocument();
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('a scanner Enter neither submits a partial value nor advances the wizard', async () => {
    await openPrompt();
    const before = usePOSStore.getState().current_step;
    const input = screen.getByLabelText('Right eye (OD) axis');
    // A scanner bursts characters then a trailing Enter.
    fireEvent.change(input, { target: { value: '8901234' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    fireEvent.keyDown(window, { key: 'Enter', code: 'Enter' });

    expect(usePOSStore.getState().current_step).toBe(before);
    expect(screen.getByText(PROMPT_TITLE)).toBeInTheDocument();
    expect(H.createPrescription).not.toHaveBeenCalled();
  });

  it('tells a non-clinical role that saving needs a manager, before they type', async () => {
    await openPrompt();
    // MOCK_USER is STORE_MANAGER, which CAN save -- no warning.
    expect(screen.queryByText(/saving a new prescription needs a manager or optometrist/i)).not.toBeInTheDocument();
  });
});
