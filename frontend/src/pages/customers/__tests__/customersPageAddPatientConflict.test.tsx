// ============================================================================
// CustomersPage add-patient: the reverse-split 409 becomes a popup, not a toast
// ============================================================================
// Adding a family member from the account page with a number that is already
// someone's OWN account is refused by POST /customers/{id}/patients (owner
// ruling 2026-09-04). The page must show the one-person-one-record popup with
// OPEN THEIR ACCOUNT instead of the blanket "Failed to add customer" toast.
// The rejection reaches the handler through the REAL client transform.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { AxiosError } from 'axios';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const orig = await importOriginal<typeof import('react-router-dom')>();
  return { ...orig, useNavigate: () => navigateSpy };
});

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['SUPERADMIN'] },
    hasRole: () => true,
  }),
}));

const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

// vi.mock factories are hoisted above every import/const, so anything they
// read must be hoisted with them.
const { HOLDER, addPatient } = vi.hoisted(() => ({
  HOLDER: {
    customer_id: 'cust-holder',
    name: 'Meena Devi',
    mobile: '9876500001',
    phone: '9876500001',
    customer_type: 'B2C',
    customerType: 'B2C',
    patients: [],
  },
  addPatient: vi.fn(),
}));

vi.mock('../../../services/api', () => ({
  customerApi: {
    getCustomers: vi.fn().mockResolvedValue({ customers: [HOLDER] }),
    addPatient: (...a: unknown[]) => addPatient(...a),
    createCustomer: vi.fn(),
  },
  prescriptionApi: { getPrescriptions: vi.fn().mockResolvedValue({ prescriptions: [] }) },
  orderApi: { getOrders: vi.fn().mockResolvedValue({ orders: [] }) },
}));
vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: { getAccount: vi.fn().mockResolvedValue({ account: null }) },
}));

// Leaf screens the page mounts; none is what this file is about.
vi.mock('../../../components/customers/AddCustomerModal', () => ({ AddCustomerModal: () => null }));
vi.mock('../../../components/common/AutoSearch', () => ({ AutoSearch: () => null }));
vi.mock('../../../components/common/Pagination', () => ({ Pagination: () => null }));
vi.mock('../../../components/crm/CustomerPurchaseHistory', () => ({
  CustomerPurchaseHistory: () => null,
}));
vi.mock('../../../components/crm/PrescriptionQRCode', () => ({ PrescriptionQRCode: () => null }));
vi.mock('../../../components/pos/PrescriptionForm', () => ({ PrescriptionForm: () => null }));

import { buildApiError } from '../../../services/api/client';
import { HOUSEHOLD_CONFLICT_CODE, OWN_ACCOUNT_CONFLICT_CODE } from '../../../services/api/customers';
import { CustomersPage } from '../CustomersPage';

const OWN = {
  code: OWN_ACCOUNT_CONFLICT_CODE,
  message: "This number is already Arun Kumar's own account",
  customer_id: 'cust-own',
  customer_name: 'Arun Kumar',
  patient_index: 0,
  patient_name: 'Arun',
};

// The one-household refusal (owner ruling 2026-09-04): the number is already
// a family member on ANOTHER account. Same popup, third shape.
const HOUSEHOLD = {
  code: HOUSEHOLD_CONFLICT_CODE,
  message: "This number is already Arun Verma, a family member on Sunil Verma's account",
  customer_id: 'cust-house2',
  account_holder_name: 'Sunil Verma',
  patient_id: 'pat-arun-2',
  patient_name: 'Arun Verma',
  relation: 'Son',
  patient_index: 0,
};

const rejectedWith = (status: number, detail: unknown) =>
  buildApiError({
    message: `Request failed with status code ${status}`,
    response: { status, data: { detail } },
  } as unknown as AxiosError<{ detail?: string }>);

/** List -> account detail -> Add -> fill -> submit. */
async function openAccountAndSubmitMember() {
  render(
    <MemoryRouter>
      <CustomersPage />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole('button', { name: /Meena Devi/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
  fireEvent.change(screen.getByPlaceholderText('Full name'), { target: { value: 'Arun' } });
  fireEvent.change(screen.getByPlaceholderText('10-digit mobile'), { target: { value: '9876500005' } });
  fireEvent.click(screen.getByRole('button', { name: /add patient/i }));
  await waitFor(() => expect(addPatient).toHaveBeenCalled());
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('CustomersPage add-patient own-account conflict', () => {
  it('shows the popup naming the own account, with OPEN, and no blanket toast', async () => {
    addPatient.mockRejectedValue(rejectedWith(409, OWN));
    await openAccountAndSubmitMember();
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent("already Arun Kumar's own account");
    expect(screen.getByRole('button', { name: /open their account/i })).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
    expect(addPatient).toHaveBeenCalledWith(
      'cust-holder',
      expect.objectContaining({ name: 'Arun', mobile: '9876500005' }),
    );
  });

  it('OPEN THEIR ACCOUNT navigates to the own account and closes both popups', async () => {
    addPatient.mockRejectedValue(rejectedWith(409, OWN));
    await openAccountAndSubmitMember();
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: /open their account/i }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/customers/cust-own'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByPlaceholderText('10-digit mobile')).toBeNull();
  });

  it('a number already on ANOTHER household shows the popup with OPEN THAT HOUSEHOLD, and navigates there', async () => {
    addPatient.mockRejectedValue(rejectedWith(409, HOUSEHOLD));
    await openAccountAndSubmitMember();
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent("already Arun Verma, a family member on Sunil Verma's account");
    expect(screen.queryByRole('button', { name: /promote|open their account/i })).toBeNull();
    expect(toast.error).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /open that household/i }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/customers/cust-house2'));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByPlaceholderText('10-digit mobile')).toBeNull();
  });

  it('any other failure still toasts and shows no popup (control)', async () => {
    addPatient.mockRejectedValue(rejectedWith(500, 'boom'));
    await openAccountAndSubmitMember();
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to add customer'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
