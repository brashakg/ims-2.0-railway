// ============================================================================
// PATIENT SAFETY: a recorded ADD of 0 must be VISIBLE on the customer's own Rx
// ============================================================================
// The OTP-gated portal is the patient's own copy of their prescription, so a
// wrong answer here is read by the customer with no staff member in the room.
//
// The backend projection was fixed to stop dropping a recorded 0
// (`portal._safe_prescription_view`, pinned by backend/tests/test_portal.py).
// That fix is INERT on its own, because this page then hid the value again:
//
//     {(p.pd || p.add_power || p.optometrist_name || p.store_name) && ( ... )}
//
// The two value fields inside that block are gated correctly with
// `!= null && !== ''` -- but a patient whose only extra field was a 0 ADD never
// reached them, because the OUTER gate is a truthiness test and 0 is falsy. The
// whole block was removed from the page and the finding vanished.
//
// This drives the real page through the real OTP steps; only the network client
// is stubbed.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const H = vi.hoisted(() => ({ getMyPrescriptions: vi.fn() }));

vi.mock('../../../services/api/portal', () => ({
  portalApi: {
    requestRxOtp: () => Promise.resolve({ ok: true, message: 'sent', expires_in_seconds: 300 }),
    verifyRxOtp: () => Promise.resolve({ ok: true, view_token: 'tok', token_type: 'bearer', expires_in: 900 }),
    getMyPrescriptions: H.getMyPrescriptions,
  },
}));

import { ToastProvider } from '../../../context/ToastContext';
import RxPortalPage from '../RxPortalPage';

/** Drive phone -> OTP -> view with one prescription on file. */
async function viewPrescription(rx: Record<string, unknown>) {
  H.getMyPrescriptions.mockResolvedValue({
    customer_id: 'CUST-A',
    count: 1,
    prescriptions: [
      {
        prescription_id: 'RX-1',
        prescription_number: 'RX-1',
        prescribed_at: '2026-08-01T00:00:00.000Z',
        right_eye: { sph: '-2.00' },
        left_eye: { sph: '-1.75' },
        ...rx,
      },
    ],
  });

  render(
    <ToastProvider>
      <RxPortalPage />
    </ToastProvider>,
  );

  fireEvent.change(screen.getByLabelText('Mobile number'), { target: { value: '9000000001' } });
  fireEvent.click(screen.getByRole('button', { name: /Send.*code|Continue|Get code/i }));

  const otpBox = await screen.findByLabelText('Verification code');
  fireEvent.change(otpBox, { target: { value: '123456' } });
  fireEvent.click(screen.getByRole('button', { name: /Verify|View/i }));

  await waitFor(() => expect(H.getMyPrescriptions).toHaveBeenCalled());
  await screen.findByText('RX-1', undefined, { timeout: 10000 });
}

/**
 * Is `label` present in the DETAILS STRIP (the block under the eye table)?
 *
 * Scoped deliberately: "ADD" is also a COLUMN HEADER of the per-eye table, so a
 * document-wide `getByText('ADD')` is ambiguous and would report the header as
 * proof the strip rendered. The strip's labels are <p>; the table's are <th>.
 */
function detailShown(label: string): boolean {
  return screen.queryAllByText(label).some((el) => el.tagName === 'P');
}

beforeEach(() => {
  H.getMyPrescriptions.mockReset();
});

describe('the customer Rx portal shows a recorded ADD of 0', () => {
  it('renders the ADD when it is the ONLY extra field on the prescription', async () => {
    // THE REQUIREMENT. This is the exact shape the outer truthiness gate ate:
    // no PD, no optometrist, no store -- only a measured, zero, reading add.
    await viewPrescription({ add_power: 0, pd: null, optometrist_name: null, store_name: null });

    expect(detailShown('ADD')).toBe(true);
  });

  it('still shows the ADD alongside the other fields', async () => {
    // A guard against "fixing" the gate by removing it: the block must keep
    // rendering when the other fields are the ones present.
    await viewPrescription({ add_power: 0, pd: 62, optometrist_name: 'Dr Rao', store_name: 'BV Bokaro' });

    expect(detailShown('ADD')).toBe(true);
    expect(detailShown('PD')).toBe(true);
  });

  it('shows NOTHING when no extra field was recorded at all', async () => {
    // The control. A gate that always opened would pass both tests above while
    // printing an empty details strip on every prescription.
    await viewPrescription({ add_power: null, pd: null, optometrist_name: null, store_name: null });

    expect(detailShown('ADD')).toBe(false);
    expect(detailShown('PD')).toBe(false);
  });

  it('shows a PD of 0 rather than hiding it, for the same reason', async () => {
    // `pd` is the other value the outer gate covered. A PD of 0mm is not a real
    // measurement, but the gate must not be the thing deciding that -- the
    // inner `!= null` check is, and it renders whatever was recorded.
    await viewPrescription({ add_power: null, pd: 0, optometrist_name: null, store_name: null });

    expect(detailShown('PD')).toBe(true);
  });
});
