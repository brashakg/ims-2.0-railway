// ============================================================================
// The clinic's Edit pencil must not quietly unsign a prescription
// ============================================================================
// Powers are stored as strings and, for everything written before this fix,
// UNSIGNED ("4"). ClinicPrescriptionHistory hydrated the edit form with
// Number("4") = 4, so a clinician who opened a prescription, corrected one
// field and pressed Save sent `String(4)` = "4" back for every OTHER power on
// the record. Nothing looked wrong -- the input re-derives the plus for
// DISPLAY, so the box read "+4.00" the whole time -- and the stored Rx was
// silently re-written without its signs.
//
// This drives the REAL component: mock only the network, click the REAL Edit
// pencil, press the REAL Save button, and assert on the body handed to
// updatePrescription.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getFamilyRx = vi.fn();
const updatePrescription = vi.fn();

vi.mock('../../../services/api', () => ({
  prescriptionApi: {
    getFamilyRx: (...a: unknown[]) => getFamilyRx(...a),
    updatePrescription: (...a: unknown[]) => updatePrescription(...a),
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

// The panel navigates to the examination page now (the Edit pencil on an
// exam-backed Rx opens /clinical/test/amend/:testId), so it needs a router.
import { MemoryRouter } from 'react-router-dom';
import { ClinicPrescriptionHistory } from '../ClinicPrescriptionHistory';

// A prescription as it actually sits in the database: strings, no signs, and
// NO eye_test_id -- a doctor-supplied Rx, which has no exam behind it and so
// legitimately opens the Rx-only form.
const STORED_RX = {
  prescription_id: 'rx-1',
  test_date: '2026-08-01T00:00:00',
  right_eye: { sph: '4', cyl: '-0.75', axis: 90, add: '2', pd: '32.5' },
  left_eye: { sph: '4.0', cyl: '0', axis: null, add: '', pd: '32' },
  ipd: '64',
};

beforeEach(() => {
  getFamilyRx.mockResolvedValue({
    members: [
      {
        patient_id: 'p1',
        name: 'Asha Kumari',
        relation: 'Self',
        prescription_count: 1,
        valid_count: 1,
        prescriptions: [STORED_RX],
      },
    ],
  });
  updatePrescription.mockResolvedValue({});
});

async function openEditor() {
  render(
    <MemoryRouter>
      <ClinicPrescriptionHistory
        isOpen
        onClose={() => {}}
        customerId="c1"
        customerName="Asha Kumari"
      />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText('Asha Kumari')).toBeTruthy());
  fireEvent.click(await screen.findByRole('button', { name: /Edit/ }));
  await waitFor(() => expect(screen.getByLabelText('Right eye sphere')).toBeTruthy());
}

describe('editing a stored prescription', () => {
  it('shows the stored unsigned "4" as +4.00 in the box', async () => {
    await openEditor();
    expect((screen.getByLabelText('Right eye sphere') as HTMLInputElement).value).toBe('+4.00');
  });

  it('sends the powers back SIGNED when nothing was touched', async () => {
    await openEditor();
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

    await waitFor(() => expect(updatePrescription).toHaveBeenCalledTimes(1));
    const body = updatePrescription.mock.calls[0][1];
    // THE REQUIREMENT: the plus must not be stripped off an untouched power.
    expect(body.sph_od).toBe('+4.00');
    expect(body.sph_os).toBe('+4.00');
    expect(String(body.sph_od)).not.toBe('4');
  });

  it('NEVER loses a minus, and keeps a recorded plano', async () => {
    await openEditor();
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

    await waitFor(() => expect(updatePrescription).toHaveBeenCalledTimes(1));
    const body = updatePrescription.mock.calls[0][1];
    expect(body.cyl_od).toBe('-0.75');
    // A recorded zero cylinder is a finding ("no astigmatism"), not a blank.
    expect(body.cyl_os).toBe('0.00');
  });

  it('leaves a power that was never recorded absent', async () => {
    // POSITIVE CONTROL: a fix that signs everything would invent an ADD of
    // +0.00 for an eye whose add was never measured.
    await openEditor();
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

    await waitFor(() => expect(updatePrescription).toHaveBeenCalledTimes(1));
    const body = updatePrescription.mock.calls[0][1];
    expect(body.add_os).toBeUndefined();
  });

  it('carries an EDITED power through with its new sign', async () => {
    await openEditor();
    const sph = screen.getByLabelText('Right eye sphere');
    fireEvent.change(sph, { target: { value: '+4.50' } });
    fireEvent.blur(sph);
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

    await waitFor(() => expect(updatePrescription).toHaveBeenCalledTimes(1));
    expect(updatePrescription.mock.calls[0][1].sph_od).toBe('+4.50');
  });

  it('shows the stored powers signed in the history list too', async () => {
    render(
      <MemoryRouter>
        <ClinicPrescriptionHistory
          isOpen
          onClose={() => {}}
          customerId="c1"
          customerName="Asha Kumari"
        />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('Asha Kumari')).toBeTruthy());
    expect(screen.getByText(/\+4\.00 \/ -0\.75 \/ 90/)).toBeTruthy();
  });
});
