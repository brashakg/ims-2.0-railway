// ============================================================================
// /clinical/test/:entryId -- the eye examination is a PAGE, and it behaves
// ============================================================================
// Owner (2026-09-04): "why is this screen still a pop up". The exam is its own
// page now. These drive the REAL page, the REAL workbench, the REAL step
// components and the REAL range validator; only the network is mocked.
//
// Pinned here:
//   * the drift guardrail renders UNDER the field for a >0.50 D move against
//     an Rx under a year old on an adult -- and not for a smaller move, not
//     for a child, not against an old Rx;
//   * Save & pause goes through the NON-completing door and never through the
//     completing one, so the queue entry stays in progress; Continue then
//     reopens the exam on the step it was left, with the readings;
//   * the staff-only note travels as `internalNote`, never as the printed
//     `notes`;
//   * amending a completed exam from Rx history reopens the SAME page at
//     /clinical/test/amend/:testId and round-trips every step untouched (the
//     "blanks nothing" guarantee, moved here from the deleted modal's test).

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const H = vi.hoisted(() => ({
  getQueue: vi.fn(),
  getTest: vi.fn(),
  startTest: vi.fn(),
  completeTest: vi.fn(),
  amendTest: vi.fn(),
  getFamilyRx: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  clinicalApi: {
    getQueue: (...a: unknown[]) => H.getQueue(...a),
    getTest: (...a: unknown[]) => H.getTest(...a),
    startTest: (...a: unknown[]) => H.startTest(...a),
    completeTest: (...a: unknown[]) => H.completeTest(...a),
    amendTest: (...a: unknown[]) => H.amendTest(...a),
  },
  prescriptionApi: { getFamilyRx: (...a: unknown[]) => H.getFamilyRx(...a) },
}));

const MOCK_USER = { id: 'u1', name: 'Dr Anita Kumari', roles: ['OPTOMETRIST'], activeStoreId: 'BV-BOK-01' };
const MOCK_AUTH = {
  user: MOCK_USER,
  hasRole: (role: string | string[]) =>
    (Array.isArray(role) ? role : [role]).some((r) => MOCK_USER.roles.includes(r)),
  hasPermission: () => true,
};
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => MOCK_AUTH }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    success: (...a: unknown[]) => H.toastSuccess(...a),
    error: (...a: unknown[]) => H.toastError(...a),
    warning: () => {},
    info: () => {},
  }),
}));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: () => Promise.resolve(null),
}));

import { EyeExamPage } from '../EyeExamPage';

const SLOW = 20000;
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

const ENTRY = {
  id: 'q-1',
  tokenNumber: 'A-14',
  patientName: 'Rahul Sharma',
  customerPhone: '9000000001',
  age: 34,
  status: 'IN_PROGRESS',
  waitTime: 5,
  createdAt: daysAgo(0),
  testId: 'test-1',
  customerId: 'c1',
  patientId: 'p1',
};

/** GET /clinical/tests/test-1 for a test the queue has only just started. */
const FRESH_TEST = {
  testId: 'test-1',
  id: 'test-1',
  status: 'IN_PROGRESS',
  queueId: 'q-1',
  patientName: 'Rahul Sharma',
  customerPhone: '9000000001',
  customerId: 'c1',
  patientId: 'p1',
  optometristName: 'Dr Anita Kumari',
  startedAt: daysAgo(0),
};

/** An earlier Rx, 60 days old: R -1.50 / L -1.75. */
const FAMILY = (date = daysAgo(60)) => ({
  members: [
    {
      patient_id: 'p1',
      name: 'Rahul Sharma',
      relation: 'Self',
      prescription_count: 1,
      valid_count: 1,
      prescriptions: [
        {
          prescription_id: 'rx-old',
          test_date: date,
          right_eye: { sph: '-1.50', cyl: '-0.50', axis: 180, add: null },
          left_eye: { sph: '-1.75', cyl: null, axis: null, add: null },
        },
      ],
    },
  ],
});

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/clinical/test/:entryId" element={<EyeExamPage />} />
          <Route path="/clinical/test/amend/:testId" element={<EyeExamPage />} />
          <Route path="/clinical/queue" element={<div>QUEUE-SENTINEL</div>} />
          <Route path="/clinical/prescriptions" element={<div>RX-DOOR-SENTINEL</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Rail button, matched EXACTLY so "Continue to subjective Rx" cannot match. */
const goTo = (label: string) =>
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}$`) }));

/** Type into a labelled Rx box the way a clinician does: change, then blur. */
function typeInto(label: string, value: string) {
  const el = screen.getByLabelText(label);
  fireEvent.change(el, { target: { value } });
  fireEvent.blur(el);
  return el;
}

/** The drift note in the SAME table cell as a power box, or null. */
function noteUnder(label: string): HTMLElement | null {
  const cell = screen.getByLabelText(label).closest('td') as HTMLElement;
  return within(cell).queryByRole('note');
}

beforeEach(() => {
  for (const fn of Object.values(H)) fn.mockReset();
  H.getQueue.mockResolvedValue({ queue: [ENTRY] });
  H.getTest.mockResolvedValue(FRESH_TEST);
  H.getFamilyRx.mockResolvedValue(FAMILY());
  H.startTest.mockResolvedValue({ testId: 'test-1' });
  H.completeTest.mockResolvedValue({ testId: 'test-1', prescriptionId: 'rx-new' });
  H.amendTest.mockResolvedValue({ testId: 'test-1', paused: true });
});

async function openExam(path = '/clinical/test/q-1') {
  renderAt(path);
  await screen.findByRole('button', { name: /^Lensometer$/ }, { timeout: SLOW });
}

// ---------------------------------------------------------------------------
// A PAGE, not a popup
// ---------------------------------------------------------------------------
describe('the examination page', () => {
  it('renders the exam in the document flow with the rail of seven steps -- no dialog, no scrim', async () => {
    await openExam();
    expect(document.querySelector('.scrim, .modal-overlay, [role="dialog"]')).toBeNull();
    for (const step of ['Lensometer', 'Slit lamp', 'Auto-refraction', 'Subjective Rx', 'Final Rx', 'Uploads', 'Clinical note']) {
      expect(screen.getByRole('button', { name: new RegExp(`^${step}$`) })).toBeInTheDocument();
    }
    // WHO is in the chair: name, age, token, last Rx, the examining optometrist.
    expect(screen.getByText('Rahul Sharma')).toBeInTheDocument();
    expect(screen.getByText('34y')).toBeInTheDocument();
    expect(screen.getByText('Token A-14')).toBeInTheDocument();
    expect(screen.getByText(/^Last Rx /)).toBeInTheDocument();
    expect(screen.getByTitle('Examining optometrist')).toHaveTextContent('Dr Anita Kumari');
  }, SLOW);

  it('a WAITING entry reached by URL offers Start, and starts THAT entry', async () => {
    H.getQueue.mockResolvedValue({ queue: [{ ...ENTRY, id: 'q-2', status: 'WAITING', testId: undefined }] });
    renderAt('/clinical/test/q-2');
    fireEvent.click(await screen.findByRole('button', { name: /Start test/ }, { timeout: SLOW }));
    await waitFor(() => expect(H.startTest).toHaveBeenCalledWith('q-2'));
  }, SLOW);

  it('an entry that is not in the queue says so instead of opening a blank exam', async () => {
    renderAt('/clinical/test/q-gone');
    expect(await screen.findByText(/not in today's queue/, {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Lensometer$/ })).toBeNull();
    expect(H.getTest).not.toHaveBeenCalled();
  }, SLOW);
});

// ---------------------------------------------------------------------------
// THE DRIFT GUARDRAIL, on the field
// ---------------------------------------------------------------------------
describe('the drift warning sits under the field', () => {
  it('warns under Right SPH for a >0.50 D move, and not for a smaller one', async () => {
    await openExam();
    goTo('Subjective Rx');
    typeInto('Right SPH', '-2.25'); // -1.50 -> -2.25 = -0.75
    const note = noteUnder('Right SPH');
    expect(note).not.toBeNull();
    expect(note).toHaveTextContent(/Right SPH has moved -0\.75 since/);
    // The left eye did not move; its box carries no warning.
    expect(noteUnder('Left SPH')).toBeNull();

    // A smaller move clears it.
    typeInto('Right SPH', '-1.75'); // -0.25
    expect(noteUnder('Right SPH')).toBeNull();
  }, SLOW);

  it('warns on the FINAL Rx too (the billable one)', async () => {
    await openExam();
    goTo('Final Rx');
    typeInto('Left (OD) sphere'.replace('OD', 'OS'), '-2.50'); // -1.75 -> -2.50 = -0.75
    expect(noteUnder('Left (OS) sphere')).toHaveTextContent(/Left SPH has moved -0\.75/);
  }, SLOW);

  it('does not warn for a child', async () => {
    H.getQueue.mockResolvedValue({ queue: [{ ...ENTRY, age: 10 }] });
    await openExam();
    goTo('Subjective Rx');
    typeInto('Right SPH', '-2.25');
    expect(noteUnder('Right SPH')).toBeNull();
  }, SLOW);

  it('does not warn against an Rx more than a year old', async () => {
    H.getFamilyRx.mockResolvedValue(FAMILY(daysAgo(400)));
    await openExam();
    goTo('Subjective Rx');
    typeInto('Right SPH', '-2.25');
    expect(noteUnder('Right SPH')).toBeNull();
  }, SLOW);

  it('shows the move against the last Rx in the right panel as well', async () => {
    await openExam();
    goTo('Subjective Rx');
    typeInto('Right SPH', '-2.25');
    const against = screen.getByText(/^Against /).closest('.card') as HTMLElement;
    expect(against).toHaveTextContent('-1.50 → -2.25');
    expect(within(against).getByText('-0.75')).toHaveClass('warn');
  }, SLOW);
});

// ---------------------------------------------------------------------------
// SAVE & PAUSE: the test stays in the queue
// ---------------------------------------------------------------------------
describe('Save & pause', () => {
  it('saves through the non-completing door, never the completing one, and returns to the queue', async () => {
    await openExam();
    goTo('Subjective Rx');
    typeInto('Right SPH', '-2.25');
    fireEvent.click(screen.getByRole('button', { name: /Save & pause/ }));

    await waitFor(() => expect(H.amendTest).toHaveBeenCalledTimes(1));
    const [testId, body] = H.amendTest.mock.calls[0];
    expect(testId).toBe('test-1');
    expect(body.subjectiveRx.rightEye.sphere).toBe('-2.25');
    // Where the optometrist stopped, so Continue can reopen there.
    expect(body.examStep).toBe('subjective');
    // THE REQUIREMENT: the entry is NOT completed.
    expect(H.completeTest).not.toHaveBeenCalled();
    expect(await screen.findByText('QUEUE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('Continue reopens a paused exam on the step it was left, with the readings', async () => {
    H.getTest.mockResolvedValue({
      ...FRESH_TEST,
      examStep: 'subjective',
      subjectiveRx: {
        rightEye: { sphere: '-2.25', cylinder: '', axis: '', add: '', pd: '', va: '' },
        leftEye: { sphere: '-1.75', cylinder: '', axis: '', add: '', pd: '', va: '' },
        remarks: '',
      },
    });
    await openExam();
    expect(screen.getByRole('button', { name: /^Subjective Rx$/ })).toHaveAttribute('aria-current', 'step');
    expect((screen.getByLabelText('Right SPH') as HTMLInputElement).value).toBe('-2.25');
  }, SLOW);

  it('a fresh test seeds the slit lamp with its "Normal" defaults, not blanks', async () => {
    await openExam();
    goTo('Slit lamp');
    expect(screen.getAllByDisplayValue('Normal').length).toBeGreaterThan(0);
  }, SLOW);

  it('refuses an impossible power on pause too -- in a banner, and nothing is sent', async () => {
    await openExam();
    typeInto('Right SPH', '-9999');
    fireEvent.click(screen.getByRole('button', { name: /Save & pause/ }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/SPH/));
    expect(H.amendTest).not.toHaveBeenCalled();
    expect(H.completeTest).not.toHaveBeenCalled();
  }, SLOW);
});

// ---------------------------------------------------------------------------
// COMPLETE TEST, and the staff-only note
// ---------------------------------------------------------------------------
describe('Complete test', () => {
  it('goes through the completing door with the internal note as internalNote, never as the printed notes', async () => {
    await openExam();
    goTo('Final Rx');
    typeInto('Right (OD) sphere', '-2.25');
    fireEvent.change(screen.getByLabelText('Internal note (staff only)'), {
      target: { value: 'Progressive trial went badly in 2024.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Complete test/ }));

    await waitFor(() => expect(H.completeTest).toHaveBeenCalledTimes(1));
    const [testId, body] = H.completeTest.mock.calls[0];
    expect(testId).toBe('test-1');
    expect(body.rightEye.sphere).toBe('-2.25');
    expect(body.internalNote).toBe('Progressive trial went badly in 2024.');
    expect(body.notes).not.toContain('Progressive');
    expect(H.amendTest).not.toHaveBeenCalled();
    expect(await screen.findByText('QUEUE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('is offered from the Final Rx step on, not before', async () => {
    await openExam();
    expect(screen.queryByRole('button', { name: /Complete test/ })).toBeNull();
    goTo('Final Rx');
    expect(screen.getByRole('button', { name: /Complete test/ })).toBeInTheDocument();
  }, SLOW);
});

// ---------------------------------------------------------------------------
// AMENDING a completed exam from Rx history: the same page, its own address
// ---------------------------------------------------------------------------
// The stored exam, as GET /clinical/tests/{id} returns it (camelCased doc).
// Deliberately FULL: every step populated, so a mapper that forgets one is
// caught by the round trip rather than by inspection.
const STORED_TEST = {
  testId: 'test-9',
  id: 'test-9',
  status: 'COMPLETED',
  patientName: 'Asha Kumari',
  customerPhone: '9000000001',
  customerId: 'c1',
  patientId: 'p1',
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
  internalNote: 'Went back to bifocal last time.',
};

describe('amending a completed exam (/clinical/test/amend/:testId)', () => {
  beforeEach(() => {
    H.getQueue.mockResolvedValue({ queue: [] });
    H.getTest.mockResolvedValue(STORED_TEST);
    H.amendTest.mockResolvedValue({ testId: 'test-9', amended: true });
  });

  const save = () => fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

  it('reopens the SAME page, pre-filled, with no queue entry needed', async () => {
    await openExam('/clinical/test/amend/test-9');
    expect(H.getTest).toHaveBeenCalledWith('test-9');
    expect(screen.getByText('Amend eye test')).toBeInTheDocument();
    expect(screen.getByText('Asha Kumari')).toBeInTheDocument();
    expect((screen.getByLabelText('Right SPH') as HTMLInputElement).value).toBe('+3.75');
    expect((screen.getByLabelText('Right ADD') as HTMLInputElement).value).toBe('+1.75');
    expect((screen.getByLabelText('Internal note (staff only)') as HTMLTextAreaElement).value)
      .toBe('Went back to bifocal last time.');
    // An amendment has no pause -- the exam is already recorded.
    expect(screen.queryByRole('button', { name: /Save & pause/ })).toBeNull();
  }, SLOW);

  it('PRE-FILLS the slit-lamp findings and the keratometry', async () => {
    await openExam('/clinical/test/amend/test-9');
    goTo('Slit lamp');
    expect(screen.getByDisplayValue('Early NS')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Mild injection')).toBeInTheDocument();
    goTo('Auto-refraction');
    expect(screen.getByDisplayValue('43.25')).toBeInTheDocument();
  }, SLOW);

  it('round-trips EVERY step back to the server untouched on Save changes', async () => {
    await openExam('/clinical/test/amend/test-9');
    save();

    await waitFor(() => expect(H.amendTest).toHaveBeenCalledTimes(1));
    const [testId, body] = H.amendTest.mock.calls[0];
    expect(testId).toBe('test-9');
    // Each step BY NAME -- a count would pass with the wrong four present.
    expect(body.lensometer?.rightEye?.sphere).toBe('+3.75');
    expect(body.lensometer?.remarks).toBe('Previous pair, 2 years old');
    expect(body.slitLamp?.rightEye?.lens).toBe('Early NS');
    expect(body.slitLamp?.rightEye?.iop).toBe(16);
    expect(body.autoRef?.rightEye?.k1).toBe('43.25');
    expect(body.autoRef?.rightEye?.k2Axis).toBe('180');
    expect(body.subjectiveRx?.rightEye?.sphere).toBe('+4.00');
    expect(body.subjectiveRx?.remarks).toBe('Accepted comfortably');
    expect(body.rightEye?.sphere).toBe('+4.00');
    expect(body.leftEye?.sphere).toBe('+3.50');
    expect(body.chiefComplaint).toBe('Blurred distance vision');
    expect(body.vduUsage).toBe('6-8 hours');
    expect(body.internalNote).toBe('Went back to bifocal last time.');
    const present = ['lensometer', 'slitLamp', 'autoRef', 'subjectiveRx']
      .filter((k) => body[k] !== undefined)
      .sort();
    expect(present).toEqual(['autoRef', 'lensometer', 'slitLamp', 'subjectiveRx']);

    // Never the completing door, and back to the prescriptions door after.
    expect(H.completeTest).not.toHaveBeenCalled();
    expect(await screen.findByText('RX-DOOR-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('leaves the binocular IPD EMPTY for an exam that only stored a monocular PD', async () => {
    // The exam document's top-level `pd` is the RIGHT EYE's MONOCULAR pd.
    // Reading it into the IPD box would push a half-value at the lab.
    const { ipd: _ipd, ...noIpd } = STORED_TEST.prescription as Record<string, unknown>;
    H.getTest.mockResolvedValue({ ...STORED_TEST, prescription: { ...noIpd, pd: 32.5 } });
    await openExam('/clinical/test/amend/test-9');
    goTo('Final Rx');
    expect((screen.getByLabelText('Interpupillary distance') as HTMLInputElement).value).toBe('');
    save();
    await waitFor(() => expect(H.amendTest).toHaveBeenCalledTimes(1));
    expect(H.amendTest.mock.calls[0][1].ipd).toBeUndefined();
  }, SLOW);

  it('shows the message the server sent when the amendment is refused', async () => {
    H.amendTest.mockRejectedValue({
      response: { data: { detail: 'Lensometer Right eye SPH value -9999 is outside the valid range (-25 to 25)' } },
    });
    await openExam('/clinical/test/amend/test-9');
    save();
    await waitFor(() => expect(H.toastError).toHaveBeenCalledTimes(1));
    expect(H.toastError.mock.calls[0][0]).toContain('Lensometer');
  }, SLOW);
});
