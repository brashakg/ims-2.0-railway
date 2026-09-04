// ============================================================================
// A refused customer create must REACH the Add Customer modal
// ============================================================================
// The modal owns the 409 whose number belongs to a family member on another
// account: it offers promote-to-own-account / open-existing. CustomersPage's
// create handler used to toast the error and swallow it, so the modal saw a
// resolved promise and simply closed -- that popup could never show on this
// page. The handler now rethrows after the toast.
//
// Reverting the rethrow fails the first test (the promise resolves).

import { render, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', name: 'Meena', activeStoreId: 'BV-BOK-01', roles: ['SUPERADMIN'] },
    hasRole: () => true,
  }),
}));

const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const createCustomer = vi.fn();
vi.mock('../../../services/api', () => ({
  customerApi: {
    getCustomers: vi.fn().mockResolvedValue({ customers: [] }),
    createCustomer: (...a: unknown[]) => createCustomer(...a),
  },
  prescriptionApi: {},
  orderApi: {},
}));
vi.mock('../../../services/api/loyalty', () => ({ loyaltyApi: { getAccount: vi.fn() } }));

// The modal is the thing under discussion, but its internals belong to its
// owner; stand in a capture of the onSave the page hands it.
let onSaveFromPage: ((f: unknown) => Promise<void>) | null = null;
vi.mock('../../../components/customers/AddCustomerModal', () => ({
  AddCustomerModal: (props: { onSave: (f: unknown) => Promise<void> }) => {
    onSaveFromPage = props.onSave;
    return null;
  },
}));
// Leaf screens the list view mounts; none is what this file is about.
vi.mock('../../../components/common/AutoSearch', () => ({ AutoSearch: () => null }));
vi.mock('../../../components/common/Pagination', () => ({ Pagination: () => null }));
vi.mock('../../../components/crm/RecallManager', () => ({ RecallManager: () => null }));
vi.mock('../../../components/crm/CustomerPurchaseHistory', () => ({
  CustomerPurchaseHistory: () => null,
}));
vi.mock('../../../components/crm/PrescriptionQRCode', () => ({ PrescriptionQRCode: () => null }));
vi.mock('../../../components/pos/PrescriptionForm', () => ({ PrescriptionForm: () => null }));

import { CustomersPage } from '../CustomersPage';

/** A valid, minimal Add-Customer form (the shared builder maps it). */
const FORM = {
  customerType: 'B2C',
  fullName: 'Asha Verma',
  mobileNumber: '9876543210',
  email: '',
  dateOfBirth: '',
  anniversary: '',
  address: '',
  pincode: '',
  city: '',
  state: '',
  gstNumber: '',
  businessName: '',
  panNumber: '',
  patients: [],
  marketingConsent: true,
  dataConsent: true,
};

const mountPage = async () => {
  render(
    <MemoryRouter>
      <CustomersPage />
    </MemoryRouter>,
  );
  await waitFor(() => expect(onSaveFromPage).toBeTruthy());
  return onSaveFromPage!;
};

beforeEach(() => {
  onSaveFromPage = null;
  createCustomer.mockReset();
  toast.error.mockReset();
  toast.success.mockReset();
});

describe('CustomersPage hands a refused create back to the modal', () => {
  it('rejects onSave with the 409 the server sent, after toasting it', async () => {
    const refusal = {
      response: {
        status: 409,
        data: { detail: 'Mobile belongs to a family member on another account' },
      },
    };
    createCustomer.mockRejectedValue(refusal);
    const onSave = await mountPage();

    await expect(onSave(FORM)).rejects.toBe(refusal);
    expect(toast.error).toHaveBeenCalledWith(
      'Mobile belongs to a family member on another account',
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('still resolves, and reports success, when the server accepts', async () => {
    createCustomer.mockResolvedValue({ customer_id: 'c-1' });
    const onSave = await mountPage();

    await expect(onSave(FORM)).resolves.toBeUndefined();
    expect(toast.success).toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });
});
