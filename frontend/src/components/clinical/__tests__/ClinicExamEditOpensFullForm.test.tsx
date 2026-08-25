// ============================================================================
// The clinic's Edit pencil must reopen the EXAM, not an Rx-only form
// ============================================================================
// The owner's report: "in clinic edit tab opens another screen, make it open
// the same screen in which we put values in the first place so that we can edit
// all fields such as lensometer and slit lamp values as well".
//
// He is right, and understated. The exam screen captures ~100 values across
// seven tabs (lensometer, slit lamp, auto-ref, subjective, final Rx, SOAP,
// uploads). The Edit pencil opened the shared POS PrescriptionForm, which
// exposes 19 of them -- so a lensometer or slit-lamp reading could be recorded
// and then never corrected, because no screen in the app would show it again.
//
// Two requirements, and the second is the dangerous one:
//   1. Edit opens the SAME seven-tab screen, pre-filled.
//   2. Saving it does not BLANK anything it did not show. An "edit" screen that
//      forgets a tab is worse than no edit screen at all -- it would erase a
//      reading the optometrist took, silently.
//
// So the deciding test here is a ROUND TRIP: load a fully-populated stored
// exam, press Save without touching a thing, and assert the body that goes back
// still carries every tab. Driven through the REAL components; only the network
// is mocked.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getFamilyRx = vi.fn();
const updatePrescription = vi.fn();
const getTest = vi.fn();
const amendTest = vi.fn();

vi.mock('../../../services/api', () => ({
  prescriptionApi: {
    getFamilyRx: (...a: unknown[]) => getFamilyRx(...a),
    updatePrescription: (...a: unknown[]) => updatePrescription(...a),
    createPrescription: vi.fn(),
  },
  clinicalApi: {
    getPrescriptionPrintHtml: vi.fn(),
    getTest: (...a: unknown[]) => getTest(...a),
    amendTest: (...a: unknown[]) => amendTest(...a),
  },
  customerApi: { getCustomers: vi.fn() },
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Dr Rao', roles: ['OPTOMETRIST'], activeStoreId: 'BV-BOK-01' },
  }),
}));
const toastError = vi.fn();
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: (...a: unknown[]) => toastError(...a), success: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../print/storeIdentity', () => ({
  resolveStoreIdentity: () => Promise.resolve(null),
}));

import { MemoryRouter } from 'react-router-dom';
import { ClinicPrescriptionHistory } from '../ClinicPrescriptionHistory';

// EyeTestForm calls useNavigate (it offers a jump to the patient's test
// history), so the editor under test has to be mounted inside a router.
function renderHistory() {
  return render(
    <MemoryRouter>
      <ClinicPrescriptionHistory
        isOpen
        onClose={() => {}}
        customerId="c1"
        customerName="Asha Kumari"
      />
    </MemoryRouter>,
  );
}

// An Rx that CAME FROM AN EXAM -- it carries eye_test_id, so the full screen
// is the correct editor for it.
const RX_FROM_EXAM = {
  prescription_id: 'rx-9',
  eye_test_id: 'test-9',
  test_date: '2026-08-01T00:00:00',
  patient_name: 'Asha Kumari',
  customer_phone: '9000000001',
  right_eye: { sph: '+4.00', cyl: '-0.75', axis: 90, add: '+2.00', pd: '32.5' },
  left_eye: { sph: '+3.50', cyl: '0', axis: null, add: '+2.00', pd: '32' },
  ipd: '64',
};

// The stored exam, as GET /clinical/tests/{id} returns it (camelCased doc).
// Deliberately FULL: every tab populated, so a mapper that forgets one is
// caught by the round trip rather than by inspection.
const STORED_TEST = {
  testId: 'test-9',
  examDate: '2026-08-01T00:00:00',
  optometristName: 'Dr Rao',
  chiefComplaint: 'Blurred distance vision',
  vduUsage: '6-8 hours',
  lensometer: {
    rightEye: { sphere: '+3.75', cylinder: '-0.50', axis: '85', add: '+1.75', pd: '32', va: '6/9' },
    leftEye: { sphere: '+3.25', cylinder: '-0.25', axis: '95', add: '+1.75', pd: '32', va: '6/9' },
    remarks: 'Previous pair, 2 years old',
  },
  slitLamp: {
    rightEye: {
      lids: 'Normal', conjunctiva: 'Clear', cornea: 'Clear', ac: 'Deep',
      iris: 'Normal', pupil: 'Round reactive', lens: 'Early NS', fundus: 'Normal', iop: 16,
    },
    leftEye: {
      lids: 'Normal', conjunctiva: 'Mild injection', cornea: 'Clear', ac: 'Deep',
      iris: 'Normal', pupil: 'Round reactive', lens: 'Clear', fundus: 'Normal', iop: 15,
    },
    remarks: 'Early nuclear sclerosis RE',
  },
  autoRef: {
    rightEye: {
      sphere: '+4.25', cylinder: '-0.75', axis: '90', add: '', pd: '32', va: '',
      k1: '43.25', k1Axis: '90', k2: '44.00', k2Axis: '180',
    },
    leftEye: {
      sphere: '+3.75', cylinder: '-0.25', axis: '95', add: '', pd: '32', va: '',
      k1: '43.00', k1Axis: '85', k2: '43.75', k2Axis: '175',
    },
    remarks: 'Auto-ref pre-dilation',
  },
  subjectiveRx: {
    rightEye: { sphere: '+4.00', cylinder: '-0.75', axis: '90', add: '+2.00', pd: '32.5', va: '6/6' },
    leftEye: { sphere: '+3.50', cylinder: '0', axis: '', add: '+2.00', pd: '32', va: '6/6' },
    remarks: 'Accepted comfortably',
  },
  prescription: {
    rightEye: { sph: '+4.00', cyl: '-0.75', axis: 90, add: '+2.00', pd: '32.5', acuity: '6/6' },
    leftEye: { sph: '+3.50', cyl: '0', axis: null, add: '+2.00', pd: '32', acuity: '6/6' },
    ipd: '64',
    lensRecommendation: 'Progressive',
    notes: 'Advise photochromic',
  },
  clinicalFindings: {
    iopRight: '16', iopLeft: '15', diagnosis: 'Presbyopia with early NS',
    colourVision: 'Normal', coverTest: 'Orthophoric', dominantEye: 'RIGHT',
  },
  soapNote: { assessment: 'Presbyopia', plan: 'Progressive lenses', dxCodes: [] },
};

beforeEach(() => {
  toastError.mockReset();
  getFamilyRx.mockReset();
  getTest.mockReset();
  amendTest.mockReset();
  updatePrescription.mockReset();
  getFamilyRx.mockResolvedValue({
    members: [
      {
        patient_id: 'p1',
        name: 'Asha Kumari',
        relation: 'Self',
        prescription_count: 1,
        valid_count: 1,
        prescriptions: [RX_FROM_EXAM],
      },
    ],
  });
  getTest.mockResolvedValue(STORED_TEST);
  amendTest.mockResolvedValue({ testId: 'test-9', amended: true });
});

async function openExamEditor() {
  renderHistory();
  await waitFor(() => expect(screen.getByText('Asha Kumari')).toBeTruthy());
  fireEvent.click(await screen.findByRole('button', { name: /Edit/ }));
  // The seven-tab screen, not the Rx-only form.
  await waitFor(() => expect(screen.getByRole('button', { name: /Slit Lamp/ })).toBeTruthy());
}

const save = () => fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

describe('the Edit pencil opens the full exam screen', () => {
  it('fetches the exam behind the prescription', async () => {
    await openExamEditor();
    expect(getTest).toHaveBeenCalledWith('test-9');
  });

  it('shows the seven exam tabs, not an Rx-only form', async () => {
    await openExamEditor();
    // THE REQUIREMENT, named by the owner: lensometer and slit lamp.
    for (const tab of ['Lensometer', 'Slit Lamp', 'Auto-Ref', 'Subjective Rx', 'Final Rx']) {
      expect(screen.getByRole('button', { name: new RegExp(tab) })).toBeTruthy();
    }
  });

  it('PRE-FILLS the lensometer reading', async () => {
    await openExamEditor();
    // Lensometer is the tab the form opens on. The exam tabs label their boxes
    // "Right SPH" / "Left SPH" (both eyes share one tab).
    expect((screen.getByLabelText('Right SPH') as HTMLInputElement).value).toBe('+3.75');
    expect((screen.getByLabelText('Left SPH') as HTMLInputElement).value).toBe('+3.25');
    expect((screen.getByLabelText('Right AXIS') as HTMLInputElement).value).toBe('85');
    expect((screen.getByLabelText('Right ADD') as HTMLInputElement).value).toBe('+1.75');
  });

  it('PRE-FILLS the slit-lamp findings', async () => {
    await openExamEditor();
    fireEvent.click(screen.getByRole('button', { name: /Slit Lamp/ }));
    await waitFor(() => expect(screen.getByDisplayValue('Early NS')).toBeTruthy());
    expect(screen.getByDisplayValue('Mild injection')).toBeTruthy();
    expect(screen.getByDisplayValue('Early nuclear sclerosis RE')).toBeTruthy();
  });

  it('PRE-FILLS the keratometry readings', async () => {
    await openExamEditor();
    fireEvent.click(screen.getByRole('button', { name: /Auto-Ref/ }));
    await waitFor(() => expect(screen.getByDisplayValue('43.25')).toBeTruthy());
    expect(screen.getByDisplayValue('44.00')).toBeTruthy();
  });
});

describe('saving an amended exam blanks NOTHING', () => {
  it('round-trips every tab back to the server untouched', async () => {
    await openExamEditor();
    save();

    await waitFor(() => expect(amendTest).toHaveBeenCalledTimes(1));
    const [testId, body] = amendTest.mock.calls[0];
    expect(testId).toBe('test-9');

    // THE REQUIREMENT. Each tab asserted BY NAME -- asserting a count here
    // would pass just as happily with the wrong four tabs present.
    expect(body.lensometer?.rightEye?.sphere).toBe('+3.75');
    expect(body.lensometer?.leftEye?.sphere).toBe('+3.25');
    expect(body.lensometer?.remarks).toBe('Previous pair, 2 years old');

    expect(body.slitLamp?.rightEye?.lens).toBe('Early NS');
    expect(body.slitLamp?.leftEye?.conjunctiva).toBe('Mild injection');
    expect(body.slitLamp?.rightEye?.iop).toBe(16);

    expect(body.autoRef?.rightEye?.k1).toBe('43.25');
    expect(body.autoRef?.rightEye?.k2Axis).toBe('180');
    expect(body.autoRef?.leftEye?.sphere).toBe('+3.75');

    expect(body.subjectiveRx?.rightEye?.sphere).toBe('+4.00');
    expect(body.subjectiveRx?.remarks).toBe('Accepted comfortably');

    expect(body.rightEye?.sphere).toBe('+4.00');
    expect(body.leftEye?.sphere).toBe('+3.50');

    // The exam header, which the Rx-only form never carried at all.
    expect(body.chiefComplaint).toBe('Blurred distance vision');
    expect(body.vduUsage).toBe('6-8 hours');
  });

  it('keeps every tab present -- the SET of exam blocks, not how many', async () => {
    await openExamEditor();
    save();

    await waitFor(() => expect(amendTest).toHaveBeenCalledTimes(1));
    const body = amendTest.mock.calls[0][1];
    const present = ['lensometer', 'slitLamp', 'autoRef', 'subjectiveRx']
      .filter((k) => body[k] !== undefined)
      .sort();
    expect(present).toEqual(['autoRef', 'lensometer', 'slitLamp', 'subjectiveRx']);
  });

  it('carries an EDITED lensometer value through', async () => {
    await openExamEditor();
    const sph = screen.getByLabelText('Right SPH');
    fireEvent.change(sph, { target: { value: '+3.50' } });
    fireEvent.blur(sph);
    save();

    await waitFor(() => expect(amendTest).toHaveBeenCalledTimes(1));
    const body = amendTest.mock.calls[0][1];
    expect(body.lensometer.rightEye.sphere).toBe('+3.50');
    // ...and the tabs the clinician did NOT open are still intact.
    expect(body.slitLamp.rightEye.lens).toBe('Early NS');
    expect(body.autoRef.rightEye.k1).toBe('43.25');
  });

  it('amends the exam rather than overwriting the prescription directly', async () => {
    // An exam edit must not go down the Rx-only PUT: that path knows nothing
    // about the exam tabs and would leave them stale.
    await openExamEditor();
    save();

    await waitFor(() => expect(amendTest).toHaveBeenCalledTimes(1));
    expect(updatePrescription).not.toHaveBeenCalled();
  });
});

describe('the binocular IPD is never invented', () => {
  it('shows the stored IPD when the exam has one', async () => {
    // POSITIVE CONTROL for the test below: 64mm was recorded, 64mm is shown.
    await openExamEditor();
    fireEvent.click(screen.getByRole('button', { name: /Final Rx/ }));
    await waitFor(() =>
      expect((screen.getByLabelText('Interpupillary distance') as HTMLInputElement).value)
        .toBe('64.0'),
    );
  });

  it('leaves the box EMPTY for an exam that only stored a monocular PD', async () => {
    // The exam document's top-level `pd` is filled from the RIGHT EYE's
    // MONOCULAR pd (about half a binocular IPD). Reading it into the IPD box
    // would show ~32.5mm, and saving would push that half-value at the
    // lab-facing prescription -- both lenses decentred. Blank is the honest
    // answer, and the backend leaves a field the form did not carry alone.
    const { ipd: _ipd, ...noIpd } = STORED_TEST.prescription as Record<string, unknown>;
    getTest.mockResolvedValue({ ...STORED_TEST, prescription: { ...noIpd, pd: 32.5 } });

    await openExamEditor();
    fireEvent.click(screen.getByRole('button', { name: /Final Rx/ }));
    await waitFor(() => expect(screen.getByLabelText('Interpupillary distance')).toBeTruthy());
    expect((screen.getByLabelText('Interpupillary distance') as HTMLInputElement).value).toBe('');

    save();
    await waitFor(() => expect(amendTest).toHaveBeenCalledTimes(1));
    expect(amendTest.mock.calls[0][1].ipd).toBeUndefined();
  });
});

describe('a refused amendment is not silent', () => {
  it('shows the message the server sent, over the exam screen', async () => {
    // The exam screen is a full-screen overlay on top of the panel the inline
    // error banner lives in, so a banner alone would be invisible: a refused
    // save would look like nothing happened, and the clinician would walk away
    // believing a corrected power was stored.
    amendTest.mockRejectedValue({
      response: {
        data: { detail: 'Lensometer Right eye SPH value -9999 is outside the valid range (-25 to 25)' },
      },
    });
    await openExamEditor();
    save();

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1));
    expect(toastError.mock.calls[0][0]).toContain('-9999');
    expect(toastError.mock.calls[0][0]).toContain('Lensometer');
  });
});

describe('a prescription with NO exam behind it', () => {
  it('still opens the Rx-only form', async () => {
    // POSITIVE CONTROL. A counter-raised or doctor-supplied Rx has no exam, so
    // there are no readings to show and the simple form is correct. A fix that
    // forced the exam screen on everything would open a BLANK exam here and
    // saving it would fabricate an examination that never happened.
    getFamilyRx.mockResolvedValue({
      members: [
        {
          patient_id: 'p1',
          name: 'Asha Kumari',
          relation: 'Self',
          prescription_count: 1,
          valid_count: 1,
          // Same Rx, minus the link to an exam.
          prescriptions: [{ ...RX_FROM_EXAM, eye_test_id: undefined }],
        },
      ],
    });
    renderHistory();
    await waitFor(() => expect(screen.getByText('Asha Kumari')).toBeTruthy());
    fireEvent.click(await screen.findByRole('button', { name: /Edit/ }));

    await waitFor(() => expect(screen.getByLabelText('Right eye sphere')).toBeTruthy());
    expect(screen.queryByRole('button', { name: /Slit Lamp/ })).toBeNull();
    expect(getTest).not.toHaveBeenCalled();
  });
});
