// ============================================================================
// PR-1430 round 1 (Codex) + D71: the patient's own Rx card
// ============================================================================
// (1) The issued date must be the exam's India (IST) calendar day whatever
//     timezone the patient's phone uses. The page formatted with the VIEWER's
//     zone, so one exam read "14 Nov 2026" in India and "13 Nov 2026" in New
//     York. This file runs the page in America/New_York on purpose.
// (2) D71: one account holds the whole family's prescriptions, so every card
//     names the member it belongs to.

process.env.TZ = 'America/New_York';

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

const base = { right_eye: { sph: '-2.00' }, left_eye: { sph: '-1.75' }, expiry_date: null };

async function view(prescriptions: Record<string, unknown>[]) {
  H.getMyPrescriptions.mockResolvedValue({
    customer_id: 'CUST-A', customer_first_name: 'Ravi', count: prescriptions.length, prescriptions,
  });
  render(<ToastProvider><RxPortalPage /></ToastProvider>);
  fireEvent.change(screen.getByLabelText('Mobile number'), { target: { value: '9000000001' } });
  fireEvent.click(screen.getByRole('button', { name: /Send.*code|Continue|Get code/i }));
  fireEvent.change(await screen.findByLabelText('Verification code'), { target: { value: '123456' } });
  fireEvent.click(screen.getByRole('button', { name: /Verify|View/i }));
  await waitFor(() => expect(H.getMyPrescriptions).toHaveBeenCalled());
  await screen.findByText('RX-1', undefined, { timeout: 10000 });
}

beforeEach(() => H.getMyPrescriptions.mockReset());

describe('portal issued date is the IST day in any viewer timezone', () => {
  it('a server IST day "2026-11-14" reads 14 Nov 2026 in New York', async () => {
    expect(new Date('2026-11-14T00:00:00Z').getDate()).toBe(13); // the harness really is in New York
    await view([{ ...base, prescription_id: 'RX-1', prescription_number: 'RX-1', prescription_date: '2026-11-14' }]);
    expect(screen.getByText(/Issued/).textContent).toContain('14 Nov 2026');
  }, 20000);

  it('a stored instant 20:30 UTC (02:00 IST next day) reads the IST day', async () => {
    await view([{ ...base, prescription_id: 'RX-1', prescription_number: 'RX-1', prescription_date: '2026-11-13T20:30:00+00:00' }]);
    expect(screen.getByText(/Issued/).textContent).toContain('14 Nov 2026');
  }, 20000);
});

describe('D71: every portal card names the family member', () => {
  it('labels each card with its member, and an unnamed one honestly', async () => {
    await view([
      { ...base, prescription_id: 'RX-1', prescription_number: 'RX-1', prescription_date: '2026-11-14', patient_name: 'Asha Daughter' },
      { ...base, prescription_id: 'RX-2', prescription_number: 'RX-2', prescription_date: '2026-11-10', patient_name: 'Ravi Holder' },
      { ...base, prescription_id: 'RX-3', prescription_number: 'RX-3', prescription_date: null, patient_name: null },
    ]);
    const labels = screen.getAllByTestId('rx-portal-member').map((el) => el.textContent);
    expect(labels).toEqual([
      'For Asha Daughter',
      'For Ravi Holder',
      'For family member (name not recorded)',
    ]);
  }, 20000);
});
