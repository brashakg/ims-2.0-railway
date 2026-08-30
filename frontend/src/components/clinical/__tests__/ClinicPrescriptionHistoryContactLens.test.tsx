// ============================================================================
// A contact-lens Rx in the clinic history must show its powers, not dashes
// ============================================================================
// A CONTACT_LENS prescription stores its powers on cl_right/cl_left
// (cl_power / cl_cyl / cl_axis) -- it has no right_eye/left_eye at all. The
// history row read only the spectacle subdocs, so a real fitting rendered as
// "- / - / -" in both eyes: a row that tells the optician nothing. CL rows
// must read the CL subdocs, and the powers must go through the shared signed
// formatter (a stored numeric 4 is the fitting "+4.00").
//
// This drives the REAL component: mock only the network and read the rows.

import { render, screen, waitFor } from '@testing-library/react';
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

// A CL fitting as it actually sits in the database: numeric powers, CL keys,
// NO right_eye/left_eye. cl_power 4 is the audit's reproduction -- a value
// whose signed render ("+4.00") differs from its raw echo ("4"). Every
// fixture field carries a distinct value.
const CL_RX = {
  prescription_id: 'rx-cl-1',
  rx_kind: 'CONTACT_LENS',
  test_date: '2026-08-10T00:00:00',
  cl_right: { cl_power: 4, cl_cyl: -0.75, cl_axis: 90 },
  cl_left: { cl_power: -2.25, cl_cyl: 0, cl_axis: 180 },
};

// Control: a spectacle Rx in the same list must keep rendering exactly as
// before off right_eye/left_eye.
const SPEC_RX = {
  prescription_id: 'rx-sp-1',
  test_date: '2026-06-05T00:00:00',
  right_eye: { sph: '3.5', cyl: '-0.5', axis: 85 },
  left_eye: { sph: '-1.25', cyl: '0.25', axis: 95 },
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

async function openHistory() {
  render(
    <ClinicPrescriptionHistory
      isOpen
      onClose={() => {}}
      customerId="c1"
      customerName="Asha Kumari"
    />,
  );
  await waitFor(() => expect(screen.getByText('Asha Kumari')).toBeTruthy());
}

describe('contact-lens rows in the prescription history', () => {
  it('renders the CL powers signed instead of a row of dashes', async () => {
    await openHistory();
    // THE REQUIREMENT: cl_right through the shared formatter.
    expect(screen.getByText(/\+4\.00 \/ -0\.75 \/ 90/)).toBeTruthy();
    // A recorded zero cylinder is a finding, and the sign survives on OS.
    expect(screen.getByText(/-2\.25 \/ \+0\.00 \/ 180/)).toBeTruthy();
    // The pre-fix rendering -- dashes for a real fitting -- must be gone.
    expect(screen.queryByText(/- \/ - \/ -/)).toBeNull();
    // And the row is badged as what it is.
    expect(screen.getByText('Contact Lens')).toBeTruthy();
  });

  it('leaves the spectacle row reading right_eye/left_eye untouched', async () => {
    await openHistory();
    expect(screen.getByText(/\+3\.50 \/ -0\.50 \/ 85/)).toBeTruthy();
    expect(screen.getByText(/-1\.25 \/ \+0\.25 \/ 95/)).toBeTruthy();
  });
});
