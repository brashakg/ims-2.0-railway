// ============================================================================
// The CL fittings history must not drop the plus sign off a stored power
// ============================================================================
// A fitted +4.00 is STORED as the number 4 (JSON numbers carry no sign glyph).
// The fittings table echoed that value raw -- `e.cl_power ?? '—'` -- so the
// optician read an unsigned "4" where the fitting said "+4.00". A contact-lens
// power's sign is the difference between a myope and a hyperope; the row must
// render through the shared signed formatter (utils/rxPowerValue
// formatPowerOrDash), exactly as the printed card already does. BC and DIA are
// millimetres, never signed, and must stay raw.
//
// This drives the REAL page: mock only the network, search, click the real
// customer result, and read the real table cells.

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const H = vi.hoisted(() => ({
  getCustomers: vi.fn(),
  getPrescriptions: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  customerApi: { getCustomers: H.getCustomers },
  prescriptionApi: {
    getPrescriptions: H.getPrescriptions,
    createPrescription: vi.fn(),
    getPrintHtml: vi.fn(),
  },
}));

const MOCK_USER = { id: 'u1', name: 'Dr Rao', roles: ['OPTOMETRIST'], activeStoreId: 'BV-BOK-01' };
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: MOCK_USER }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));

import { ContactLensFittingPage } from '../ContactLensFittingPage';

// The audit's reproduction: cl_power stored as the NUMBER 4 -- a value whose
// signed render ("+4.00") and raw echo ("4") differ, so this test cannot pass
// by accident. Every fixture field carries a distinct value.
const FITTING = {
  prescription_id: 'clrx-1',
  rx_kind: 'CONTACT_LENS',
  test_date: '2026-08-12T00:00:00',
  cl_brand: 'Acuvue',
  modality: 'MONTHLY',
  cl_right: { cl_power: 4, base_curve: 8.6, diameter: 14.2 },
  cl_left: { cl_power: -2.75, base_curve: 8.4, diameter: 13.8 },
};

// Positive control: an eye with NO recorded power must show the dash, and a
// recorded plano 0 must show +0.00 -- absence and plano never print alike.
const BLANK_AND_PLANO = {
  prescription_id: 'clrx-2',
  rx_kind: 'CONTACT_LENS',
  test_date: '2026-07-02T00:00:00',
  cl_right: { base_curve: 8.7, diameter: 14.5 },
  cl_left: { cl_power: 0, base_curve: 8.5, diameter: 13.9 },
};

beforeEach(() => {
  H.getCustomers.mockResolvedValue({
    customers: [{ id: 'c1', name: 'Asha Kumari', mobile: '9998887776' }],
  });
  H.getPrescriptions.mockResolvedValue({ prescriptions: [FITTING, BLANK_AND_PLANO] });
});

async function openFittings() {
  render(<ContactLensFittingPage />);
  fireEvent.change(screen.getByPlaceholderText(/Search customer/), {
    target: { value: 'Asha' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Search/ }));
  fireEvent.click(await screen.findByRole('button', { name: /Asha Kumari/ }));
  await screen.findByText('Acuvue');
}

describe('contact-lens fittings history powers', () => {
  it('renders a stored numeric 4 as the SIGNED +4.00, never the raw echo', async () => {
    await openFittings();
    // THE REQUIREMENT: power through the shared formatter; BC/DIA raw.
    expect(screen.getByText('+4.00 · 8.6 · 14.2')).toBeTruthy();
    // The pre-fix cell -- the unsigned raw echo -- must be gone.
    expect(screen.queryByText('4 · 8.6 · 14.2')).toBeNull();
  });

  it('keeps a minus power exactly as recorded', async () => {
    await openFittings();
    expect(screen.getByText('-2.75 · 8.4 · 13.8')).toBeTruthy();
  });

  it('never confuses an unrecorded power with a recorded plano', async () => {
    await openFittings();
    // No cl_power recorded -> dash, not a fabricated 0.
    expect(screen.getByText('- · 8.7 · 14.5')).toBeTruthy();
    // Recorded 0 -> +0.00, not a dash.
    expect(screen.getByText('+0.00 · 8.5 · 13.9')).toBeTruthy();
  });
});
