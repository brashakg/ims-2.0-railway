// ============================================================================
// The clinic's Edit pencil reopens the EXAM PAGE, at its own address
// ============================================================================
// The owner's earlier report: "in clinic edit tab opens another screen, make
// it open the same screen in which we put values in the first place so that
// we can edit all fields such as lensometer and slit lamp values as well".
// And then (2026-09-04): "why is this screen still a pop up".
//
// So an Rx that came from an examination is edited on the SAME examination
// page the readings were typed into -- /clinical/test/amend/:testId -- and
// not in a modal over the history panel. The page's own tests
// (pages/clinical/__tests__/EyeExamPage.test.tsx) prove it pre-fills every
// step and round-trips them untouched; this file pins the DOOR.
//
// Driven through the REAL history component inside a router whose amend
// route is a sentinel that prints the test id it was opened with.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route, useParams } from 'react-router-dom';

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
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));

import { ClinicPrescriptionHistory } from '../ClinicPrescriptionHistory';

function AmendSentinel() {
  const { testId } = useParams();
  return <div>EXAM-PAGE-SENTINEL for {testId}</div>;
}

function renderHistory() {
  return render(
    <MemoryRouter initialEntries={['/clinical/queue']}>
      <Routes>
        <Route
          path="/clinical/queue"
          element={
            <ClinicPrescriptionHistory isOpen onClose={() => {}} customerId="c1" customerName="Asha Kumari" />
          }
        />
        <Route path="/clinical/test/amend/:testId" element={<AmendSentinel />} />
      </Routes>
    </MemoryRouter>,
  );
}

// An Rx that CAME FROM AN EXAM -- it carries eye_test_id.
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

const family = (rx: Record<string, unknown>) => ({
  members: [
    { patient_id: 'p1', name: 'Asha Kumari', relation: 'Self', prescription_count: 1, valid_count: 1, prescriptions: [rx] },
  ],
});

beforeEach(() => {
  getFamilyRx.mockReset();
  getTest.mockReset();
  amendTest.mockReset();
  updatePrescription.mockReset();
  getFamilyRx.mockResolvedValue(family(RX_FROM_EXAM));
});

describe('the Edit pencil on an Rx that came from an exam', () => {
  it('opens the examination PAGE at /clinical/test/amend/<test id> -- not a popup', async () => {
    renderHistory();
    await waitFor(() => expect(screen.getByText('Asha Kumari')).toBeTruthy());
    fireEvent.click(await screen.findByRole('button', { name: /Edit/ }));

    expect(await screen.findByText('EXAM-PAGE-SENTINEL for test-9')).toBeInTheDocument();
    // The page loads the exam; the history does not fetch it into an overlay.
    expect(getTest).not.toHaveBeenCalled();
    // No Rx-only form, no exam form, no overlay left behind.
    expect(screen.queryByLabelText('Right eye sphere')).toBeNull();
    expect(screen.queryByLabelText('Right SPH')).toBeNull();
    expect(document.querySelector('.fixed.inset-0, [role="dialog"]')).toBeNull();
  });
});

describe('a prescription with NO exam behind it', () => {
  it('still opens the Rx-only form, in place', async () => {
    // POSITIVE CONTROL. A counter-raised or doctor-supplied Rx has no exam, so
    // there are no readings to show and the simple form is correct. Sending it
    // to the exam page would open a BLANK exam, and saving that would
    // fabricate an examination that never happened.
    getFamilyRx.mockResolvedValue(family({ ...RX_FROM_EXAM, eye_test_id: undefined }));
    renderHistory();
    await waitFor(() => expect(screen.getByText('Asha Kumari')).toBeTruthy());
    fireEvent.click(await screen.findByRole('button', { name: /Edit/ }));

    await waitFor(() => expect(screen.getByLabelText('Right eye sphere')).toBeTruthy());
    expect(screen.queryByText(/EXAM-PAGE-SENTINEL/)).toBeNull();
    expect(getTest).not.toHaveBeenCalled();
  });
});
