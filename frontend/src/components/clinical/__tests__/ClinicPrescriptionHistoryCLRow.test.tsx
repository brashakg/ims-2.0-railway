// ============================================================================
// A contact-lens Rx row must show its powers, not a card of dashes
// ============================================================================
// A CONTACT_LENS prescription has NO right_eye/left_eye -- its powers live in
// cl_right/cl_left (cl_power + base curve + diameter). The history card read
// the spectacle fields unconditionally, so every CL row rendered "- / - / -"
// twice: a row that occupied space without informing. CL rows must render
// their fitting values, with the power through the shared display formatter
// (a stored numeric 4 is a fitted +4.00 -- the fixture power is 4 ON PURPOSE
// so signed and unsigned renders differ).

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getFamilyRx = vi.fn();

vi.mock('../../../services/api', () => ({
  prescriptionApi: {
    getFamilyRx: (...a: unknown[]) => getFamilyRx(...a),
    updatePrescription: vi.fn(),
    createPrescription: vi.fn(),
  },
  clinicalApi: { getPrescriptionPrintHtml: vi.fn(), getTest: vi.fn() },
  customerApi: { getCustomers: vi.fn() },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', name: 'Dr Rao', roles: ['OPTOMETRIST'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));

import { ClinicPrescriptionHistory } from '../ClinicPrescriptionHistory';

// One CL Rx and one spectacle Rx on the same patient, with values chosen so
// no two fields share a render: the CL row must show fitting values and the
// spectacle row must be untouched by the CL branch.
const CL_RX = {
  prescription_id: 'clrx-1',
  rx_kind: 'CONTACT_LENS',
  test_date: '2026-08-12T00:00:00',
  cl_right: { cl_power: 4, base_curve: 8.6, diameter: 14.2 },
  cl_left: { cl_power: -2.5, base_curve: 8.7, diameter: 14 },
};
const SPEC_RX = {
  prescription_id: 'rx-2',
  test_date: '2026-08-01T00:00:00',
  right_eye: { sph: '1.25', cyl: '-0.75', axis: 90 },
  left_eye: { sph: '1.25', cyl: '0', axis: null },
};

beforeEach(() => {
  getFamilyRx.mockResolvedValue({
    members: [
      {
        patient_id: 'p1',
        name: 'Asha Kumari',
        relation: 'Self',
        prescription_count: 2,
        valid_count: 2,
        prescriptions: [CL_RX, SPEC_RX],
      },
    ],
  });
});

describe('a CONTACT_LENS row in the prescription history', () => {
  it('renders the cl_right/cl_left fitting values, power SIGNED', async () => {
    render(
      <ClinicPrescriptionHistory isOpen onClose={() => {}} customerId="c1" customerName="Asha Kumari" />,
    );
    // THE REQUIREMENT: the CL row informs -- OD/OS carry the fitting values.
    expect(await screen.findByText('+4.00 · BC 8.6 · DIA 14.2')).toBeTruthy();
    expect(screen.getByText('-2.50 · BC 8.7 · DIA 14')).toBeTruthy();
    // The empty spectacle-field render must be gone from the CL row. The
    // spectacle row supplies every field, so NO card on screen may be blank.
    expect(screen.queryByText('- / - / -')).toBeNull();
  });

  it('leaves the spectacle row on the spectacle fields', async () => {
    render(
      <ClinicPrescriptionHistory isOpen onClose={() => {}} customerId="c1" customerName="Asha Kumari" />,
    );
    expect(await screen.findByText('+1.25 / -0.75 / 90')).toBeTruthy();
    // A recorded plano cylinder renders as +0.00 (the formatter's contract:
    // absence is '-', a recorded zero is a finding), axis never taken -> '-'.
    expect(screen.getByText('+1.25 / +0.00 / -')).toBeTruthy();
  });
});
