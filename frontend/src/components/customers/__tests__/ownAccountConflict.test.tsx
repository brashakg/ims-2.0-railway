// ============================================================================
// AddCustomerModal: the reverse-split 409 becomes a decision, not a toast
// ============================================================================
// A create whose patients[] carries a number that is already someone's OWN
// account is refused (owner ruling 2026-09-04: "block it the same way"). The
// ONE shared form shows the same popup component in its second shape: it names
// the person and their account, offers OPEN THEIR ACCOUNT (no promote -- there
// is nothing to promote, the person already has an account) and never creates
// a copy. The rejection reaches the modal through the REAL client transform.

import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
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

vi.mock('../CustomerIdentityFields', () => ({
  CustomerIdentityFields: ({ value, onChange }: { value: any; onChange: (v: any) => void }) => (
    <button
      type="button"
      onClick={() =>
        onChange({
          ...value,
          fullName: 'Sunita Kumari',
          mobileNumber: '9876500008',
          patients: [{ id: 'p-1', name: 'Arun', mobile: '9876500005', relation: 'Son' }],
        })
      }
    >
      fill-identity
    </button>
  ),
}));

import { buildApiError } from '../../../services/api/client';
import { OWN_ACCOUNT_CONFLICT_CODE } from '../../../services/api/customers';
import { AddCustomerModal } from '../AddCustomerModal';

const OWN = {
  code: OWN_ACCOUNT_CONFLICT_CODE,
  message: "This number is already Arun Kumar's own account",
  customer_id: 'cust-own',
  customer_name: 'Arun Kumar',
  patient_index: 0,
  patient_name: 'Arun',
};

const rejectedWith = (status: number, detail: unknown) =>
  buildApiError({
    message: `Request failed with status code ${status}`,
    response: { status, data: { detail } },
  } as unknown as AxiosError<{ detail?: string }>);

function renderModal(opts: { onSelectExisting?: (c: any) => void } = {}) {
  const onClose = vi.fn();
  const onSave = vi.fn().mockRejectedValue(rejectedWith(409, OWN));
  render(
    <AddCustomerModal isOpen onClose={onClose} onSave={onSave} onSelectExisting={opts.onSelectExisting} />,
  );
  return { onClose, onSave };
}

async function submitAndGetPopup() {
  fireEvent.click(screen.getByRole('button', { name: /fill-identity/i }));
  fireEvent.click(screen.getByRole('button', { name: /create customer/i }));
  return waitFor(() => screen.getByRole('dialog'));
}

beforeEach(() => {
  vi.clearAllMocks();
  getCustomer.mockResolvedValue({
    customer_id: 'cust-own',
    name: 'Arun Kumar',
    mobile: '9876500005',
    patients: [{ patient_id: 'pat-arun', name: 'Arun Kumar', is_primary: true }],
  });
});

describe('AddCustomerModal own-account (reverse split) conflict', () => {
  it('names the person and their own account, offers OPEN only, and does not close', async () => {
    const { onClose } = renderModal();
    const dialog = await submitAndGetPopup();
    expect(dialog).toHaveTextContent("already Arun Kumar's own account");
    expect(dialog).toHaveTextContent('Arun');
    expect(screen.getByRole('button', { name: /open their account/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /promote/i })).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('OPEN with a till callback fetches the own account and hands it to the bill', async () => {
    const onSelectExisting = vi.fn();
    const { onClose } = renderModal({ onSelectExisting });
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /open their account/i }));
    await waitFor(() =>
      expect(onSelectExisting).toHaveBeenCalledWith(expect.objectContaining({ customer_id: 'cust-own' })),
    );
    expect(getCustomer).toHaveBeenCalledWith('cust-own');
    expect(promotePatient).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('OPEN without a till callback opens the own account page', async () => {
    const { onClose } = renderModal();
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /open their account/i }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/customers/cust-own'));
    expect(getCustomer).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('CANCEL closes the popup and leaves the form open for a correction', async () => {
    const { onClose } = renderModal();
    const dialog = await submitAndGetPopup();
    // The form footer has its own Cancel; the popup's is the one under test.
    fireEvent.click(within(dialog).getByRole('button', { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /create customer/i })).toBeInTheDocument();
  });
});
