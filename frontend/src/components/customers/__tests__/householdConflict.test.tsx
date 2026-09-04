// ============================================================================
// AddCustomerModal: the one-household 409 becomes a decision, not a toast
// ============================================================================
// A create whose patients[] carries a number that is already a family member
// on ANOTHER account is refused (owner ruling 2026-09-04: "Block it, one
// household account"). The ONE shared form shows the same popup component in
// its third shape: it names the member and the household that holds them,
// offers OPEN THAT HOUSEHOLD only (no promote, no link) and never creates a
// copy. The rejection reaches the modal through the REAL client transform.

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
          fullName: 'Sunil Verma',
          mobileNumber: '9876500011',
          patients: [{ id: 'p-1', name: 'Riya', mobile: '9876500002', relation: 'Daughter' }],
        })
      }
    >
      fill-identity
    </button>
  ),
}));

import { buildApiError } from '../../../services/api/client';
import { HOUSEHOLD_CONFLICT_CODE } from '../../../services/api/customers';
import { AddCustomerModal } from '../AddCustomerModal';

const HOUSEHOLD = {
  code: HOUSEHOLD_CONFLICT_CODE,
  message: "This number is already Riya Devi, a family member on Meena Devi's account",
  customer_id: 'cust-holder',
  account_holder_name: 'Meena Devi',
  patient_id: 'pat-daughter',
  patient_name: 'Riya Devi',
  relation: 'Daughter',
  patient_index: 0,
};

const rejectedWith = (status: number, detail: unknown) =>
  buildApiError({
    message: `Request failed with status code ${status}`,
    response: { status, data: { detail } },
  } as unknown as AxiosError<{ detail?: string }>);

function renderModal(opts: { onSelectExisting?: (c: any) => void } = {}) {
  const onClose = vi.fn();
  const onSave = vi.fn().mockRejectedValue(rejectedWith(409, HOUSEHOLD));
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
    customer_id: 'cust-holder',
    name: 'Meena Devi',
    mobile: '9876500001',
    patients: [{ patient_id: 'pat-daughter', name: 'Riya Devi', mobile: '9876500002' }],
  });
});

describe('AddCustomerModal one-household conflict', () => {
  it('names the member and the household, offers OPEN THAT HOUSEHOLD only, and does not close', async () => {
    const { onClose } = renderModal();
    const dialog = await submitAndGetPopup();
    expect(dialog).toHaveTextContent("already Riya Devi, a family member on Meena Devi's account");
    const open = screen.getByRole('button', { name: /open that household/i });
    expect(open).toHaveClass('min-h-[44px]');
    expect(screen.queryByRole('button', { name: /promote/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /open their account|open existing account/i })).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('OPEN with a till callback fetches the household and hands it to the bill', async () => {
    const onSelectExisting = vi.fn();
    const { onClose } = renderModal({ onSelectExisting });
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /open that household/i }));
    await waitFor(() =>
      expect(onSelectExisting).toHaveBeenCalledWith(expect.objectContaining({ customer_id: 'cust-holder' })),
    );
    expect(getCustomer).toHaveBeenCalledWith('cust-holder');
    expect(promotePatient).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('OPEN without a till callback opens the household page', async () => {
    const { onClose } = renderModal();
    await submitAndGetPopup();
    fireEvent.click(screen.getByRole('button', { name: /open that household/i }));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/customers/cust-holder'));
    expect(getCustomer).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('CANCEL closes the popup and leaves the form open for a correction', async () => {
    const { onClose } = renderModal();
    const dialog = await submitAndGetPopup();
    fireEvent.click(within(dialog).getByRole('button', { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /create customer/i })).toBeInTheDocument();
  });
});
