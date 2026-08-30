// ============================================================================
// CL fittings history must not drop the plus sign
// ============================================================================
// The backend stores cl_power as a NUMBER: a fitted +4.00 sits in Mongo as 4.
// The fittings table echoed it raw, so the hyperope's card read "4" -- plus
// or minus four? The power segment must go through the shared display
// formatter (utils/rxPowerValue.formatPowerOrDash) and render "+4.00",
// exactly as the printed card does. BC/DIA are unsigned by nature and stay
// raw. The fixture power is 4 ON PURPOSE: its signed ("+4.00") and unsigned
// ("4") renders differ, so this test dies if the formatter is bypassed.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getCustomers = vi.fn();
const getPrescriptions = vi.fn();

vi.mock('../../../services/api', () => ({
  customerApi: { getCustomers: (...a: unknown[]) => getCustomers(...a) },
  prescriptionApi: {
    getPrescriptions: (...a: unknown[]) => getPrescriptions(...a),
    createPrescription: vi.fn(),
    getPrintHtml: vi.fn(),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', name: 'Dr Rao', roles: ['OPTOMETRIST'], activeStoreId: 'S1' } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));

import { ContactLensFittingPage } from '../ContactLensFittingPage';

// A fitting exactly as the backend returns it: numeric powers, unsigned.
const STORED_FITTING = {
  prescription_id: 'clrx-1',
  rx_kind: 'CONTACT_LENS',
  test_date: '2026-08-12T00:00:00',
  cl_brand: 'Acuvue Oasys',
  modality: 'MONTHLY',
  cl_right: { cl_power: 4, base_curve: 8.6, diameter: 14.2 },
  cl_left: { cl_power: -2.5, base_curve: 8.7, diameter: 14 },
};

beforeEach(() => {
  getCustomers.mockResolvedValue({
    customers: [{ id: 'c1', name: 'Asha Kumari', mobile: '9000000001' }],
  });
  getPrescriptions.mockResolvedValue({ prescriptions: [STORED_FITTING] });
});

async function openHistory() {
  render(<ContactLensFittingPage />);
  fireEvent.change(screen.getByPlaceholderText(/Search customer/), { target: { value: 'Asha' } });
  fireEvent.click(screen.getByRole('button', { name: /Search/ }));
  fireEvent.click(await screen.findByRole('button', { name: /Asha Kumari/ }));
  await waitFor(() => expect(screen.getByText('Acuvue Oasys')).toBeTruthy());
}

describe('contact-lens fittings history', () => {
  it('renders a stored numeric 4 as SIGNED +4.00; BC/DIA stay unsigned', async () => {
    await openHistory();
    // THE REQUIREMENT: the power segment goes through the shared formatter.
    expect(screen.getByText('+4.00 · 8.6 · 14.2')).toBeTruthy();
    // The raw unsigned echo must be gone.
    expect(screen.queryByText('4 · 8.6 · 14.2')).toBeNull();
  });

  it('keeps a minus power signed to two decimals', async () => {
    await openHistory();
    expect(screen.getByText('-2.50 · 8.7 · 14')).toBeTruthy();
  });
});
