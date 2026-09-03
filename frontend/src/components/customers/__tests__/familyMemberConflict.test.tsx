// ============================================================================
// AddCustomerModal: the family-member 409 becomes a decision, not a toast
// ============================================================================
// When the create is refused because the number belongs to a family member on
// another account, the ONE shared form shows the popup with two actions:
// PROMOTE (the member becomes their own customer and is handed to the caller
// so the sale continues) or OPEN the existing account. The rejection reaches
// the modal through the REAL client transform (buildApiError), exactly as a
// till's createAndSelectCustomer rethrows it.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { AxiosError } from 'axios';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const orig = await importOriginal<typeof import('react-router-dom')>();
  return { ...orig, useNavigate: () => navigateSpy };
});

const promotePatient = vi.fn();
const getCustomer = vi.fn();
vi.mock('../../../services/api', () => ({
  customerApi: {
    getConsentText: vi.fn().mockResolvedValue({ text: 'consent', version: 'v1' }),
    searchByPhone: vi.fn().mockResolvedValue([]),
    getCustomers: vi.fn().mockResolvedValue({ customers: [] }),
    promotePatient: (...a: unknown[]) => promotePatient(...a),
    getCustomer: (...a: unknown[]) => getCustomer(...a),
  },
}));

vi.mock('../../../hooks/useDebounce', () => ({ useDebounce: (v: unknown) => v }));

// The identity body is not what this file is about: a stub with one button
// that fills the name + mobile the way an operator would type them.
vi.mock('../CustomerIdentityFields', () => ({
  CustomerIdentityFields: ({ value, onChange }: { value: any; onChange: (v: any) => void }) => (
    <button
      type="button"
      onClick={() => onChange({ ...value, fullName: 'Riya Devi', mobileNumber: '9876500002' })}
    >
      fill-identity
    </button>
  ),
}));

import { buildApiError } from '../../../services/api/client';
import { FAMILY_MEMBER_CONFLICT_CODE } from '../../../services/api/customers';
import { AddCustomerModal } from '../AddCustomerModal';

const CONFLICT = {
  code: FAMILY_MEMBER_CONFLICT_CODE,
  message: "This number belongs to Riya Devi, a family member on Meena Devi's account",
  customer_id: 'cust-holder',
  account_holder_name: 'Meena Devi',
  patient_id: 'pat-daughter',
  patient_name: 'Riya Devi',
  relation: 'Daughter',
};

const PROMOTED = {
  customer_id: 'cust-new',
  name: 'Riya Devi',
  mobile: '9876500002',
  phone: '9876500002',
  customer_type: 'B2C',
  patients: [{ patient_id: 'pat-daughter', name: 'Riya Devi', mobile: '9876500002', is_primary: true }],
  primary_patient_id: 'pat-daughter',
  promoted_from: { customer_id: 'cust-holder', patient_id: 'pat-daughter', at: 't' },
  carried: { prescriptions: 2, eye_tests: 0, eye_test_queue: 0 },
};

const rejectedWith = (status: number, detail: unknown) =>
  buildApiError({
    message: `Request failed with status code ${status}`,
    response: { status, data: { detail } },
  } as unknown as AxiosError<{ detail?: string }>);

function renderModal(opts: { onSelectExisting?: (c: any) => void; saveRejection?: unknown } = {}) {
  const onClose = vi.fn();
  const onSave = vi.fn().mockRejectedValue(opts.saveRejection ?? rejectedWith(409, CONFLICT));
  render(
    <AddCustomerModal isOpen onClose={onClose} onSave={onSave} onSelectExisting={opts.onSelectExisting} />,
  );
  return { onClose, onSave };
}

function submit() {
  fireEvent.click(screen.getByRole('button', { name: /fill-identity/i }));
  fireEvent.click(screen.getByRole('button', { name: /create customer/i }));
}

async function submitAndGetPopup() {
  submit();
  return waitFor(() => screen.getByRole('dialog'));
}

beforeEach(() => {
  vi.clearAllMocks();
  promotePatient.mockResolvedValue(PROMOTED);
  getCustomer.mockResolvedValue({ customer_id: 'cust-holder', name: 'Meena Devi', mobile: '9876500001', patients: [] });
});

describe('AddCustomerModal family-member conflict', () => {
  it('a refused create names the member and the account holder and does not close', async () => {
    const { onClose } = renderModal();
    const dialog = await submitAndGetPopup();
    expect(dialog).toHaveTextContent('Riya Devi');
    expect(dialog).toHaveTextContent('Meena Devi');
    expect(dialog).toHaveTextContent('Daughter');
    expect(screen.getByRole('button', { name: /promote to own account/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /open existing account/i })).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('PROMOTE calls the promote door and hands the promoted customer to the till', async () => {
    const onSelectExisting = vi.fn();
    const { onClose } = renderModal({ onSelectExisting });
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /promote to own account/i }));
    await waitFor(() => expect(onSelectExisting).toHaveBeenCalledWith(PROMOTED));
    expect(promotePatient).toHaveBeenCalledWith('cust-holder', 'pat-daughter');
    expect(onClose).toHaveBeenCalled();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('OPEN EXISTING fetches the holding account and hands it to the till', async () => {
    const onSelectExisting = vi.fn();
    renderModal({ onSelectExisting });
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /open existing account/i }));
    await waitFor(() =>
      expect(onSelectExisting).toHaveBeenCalledWith(expect.objectContaining({ customer_id: 'cust-holder' })),
    );
    expect(getCustomer).toHaveBeenCalledWith('cust-holder');
    expect(promotePatient).not.toHaveBeenCalled();
  });

  it('without a till callback, PROMOTE opens the promoted account page', async () => {
    const { onClose } = renderModal();
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /promote to own account/i }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/customers/cust-new'));
    expect(onClose).toHaveBeenCalled();
  });

  it('without a till callback, OPEN EXISTING opens the holding account page', async () => {
    renderModal();
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /open existing account/i }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/customers/cust-holder'));
    expect(getCustomer).not.toHaveBeenCalled();
  });

  it('a failed promote is shown in the popup, which stays open', async () => {
    promotePatient.mockRejectedValue(rejectedWith(409, {
      code: 'MOBILE_ALREADY_OWN_ACCOUNT',
      message: 'This family member already has their own customer account',
      customer_id: 'cust-split',
    }));
    const { onClose } = renderModal({ onSelectExisting: vi.fn() });
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /promote to own account/i }));
    const alert = await waitFor(() => screen.getByRole('alert'));
    expect(alert).toHaveTextContent('already has their own customer account');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('the plain duplicate-mobile 409 is NOT the family popup (control)', async () => {
    const { onSave } = renderModal({
      saveRejection: rejectedWith(409, 'Customer with this mobile already exists'),
    });
    submit();
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    // The rejection has been processed once the saving spinner is gone.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /create customer/i })).not.toBeDisabled(),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
