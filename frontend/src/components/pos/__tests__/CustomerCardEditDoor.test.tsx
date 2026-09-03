// ============================================================================
// Customer edit at the till: one door, owner-widened, and it must not unlink
// the Rx
// ============================================================================
// Owner ruling 2026-09-03 (taken knowingly): CASHIER and SALES_STAFF get FULL
// customer edit at the till, phone and GSTIN included. The door is the SAME
// EditCustomerModal the Customers page opens, gated on the ONE role list it
// exports. A save merges into the bill's customer IN PLACE: routing it through
// store.setCustomer would reset the selected patient and prescription and
// silently unlink the Rx from a half-built optical bill.
//
// Narrowing the role list fails the first test; routing the save through
// setCustomer fails the Rx assertions; dropping the GSTIN box fails the wire
// assertion.

import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

let roles: string[] = ['CASHIER'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', roles },
    hasRole: (r: string | string[]) => [r].flat().some((x) => roles.includes(x)),
  }),
}));

vi.mock('../../../services/api/loyalty', () => ({
  loyaltyApi: {
    getAccount: () =>
      Promise.resolve({
        account: { balance_points: 0, tier: 'BRONZE' },
        expiring_soon_points: 0,
      }),
  },
}));

const updateCustomer = vi.fn();
vi.mock('../../../services/api/customers', () => ({
  customerApi: { updateCustomer: (...a: unknown[]) => updateCustomer(...a) },
}));

import { CustomerCardWithLoyalty } from '../CustomerCardWithLoyalty';
import { usePOSStore } from '../../../stores/posStore';

const CUSTOMER = {
  id: 'c-1',
  name: 'Asha Verma',
  phone: '9876543210',
  customerType: 'B2C',
  patients: [],
  createdAt: '',
};

/** A half-built optical bill: customer, a chosen patient, an attached Rx. */
function seedBill() {
  const s = usePOSStore.getState();
  s.setCustomer(CUSTOMER as never);
  s.setPatient({ id: 'pt-1', name: 'Asha Verma' } as never);
  s.setPrescription({ id: 'rx-1' } as never);
}

const editButton = () => screen.getByRole('button', { name: /edit/i });

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  updateCustomer.mockReset().mockResolvedValue({});
  roles = ['CASHIER'];
});

describe('the edit door on the POS customer card', () => {
  it('opens for a CASHIER (owner ruling) but not for an OPTOMETRIST', () => {
    seedBill();
    const view = render(<CustomerCardWithLoyalty />);
    expect(editButton()).toBeTruthy();
    view.unmount();

    roles = ['OPTOMETRIST'];
    render(<CustomerCardWithLoyalty />);
    expect(screen.queryByRole('button', { name: /edit/i })).toBeNull();
  });

  it('offers Change only when the till hands in a way to clear the pick', () => {
    // The new tills pass onChange (a wrong pick could otherwise only be undone
    // by reloading); the classic surface passes nothing and keeps its own.
    seedBill();
    const onChange = vi.fn();
    const view = render(<CustomerCardWithLoyalty onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: /^change$/i }));
    expect(onChange).toHaveBeenCalledTimes(1);
    view.unmount();

    render(<CustomerCardWithLoyalty />);
    expect(screen.queryByRole('button', { name: /^change$/i })).toBeNull();
  });

  it('never offers to edit a walk-in placeholder', () => {
    usePOSStore.getState().setCustomer({ ...CUSTOMER, id: 'walkin-1' } as never);
    render(<CustomerCardWithLoyalty />);
    expect(screen.queryByRole('button', { name: /edit/i })).toBeNull();
  });

  it('saves phone + GSTIN through PUT /customers/{id} and merges them into the bill WITHOUT unlinking the Rx', async () => {
    seedBill();
    render(<CustomerCardWithLoyalty />);
    fireEvent.click(editButton());

    fireEvent.change(screen.getByPlaceholderText('10-digit mobile'), {
      target: { value: '9123456780' },
    });
    fireEvent.change(screen.getByPlaceholderText('15-character GSTIN'), {
      target: { value: '20aabcu9603r1zm' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(updateCustomer).toHaveBeenCalledTimes(1));
    expect(updateCustomer.mock.calls[0][0]).toBe('c-1');
    expect(updateCustomer.mock.calls[0][1]).toMatchObject({
      phone: '9123456780',
      gstin: '20AABCU9603R1ZM',
    });

    // The bill now carries the corrected customer...
    await waitFor(() =>
      expect(usePOSStore.getState().customer?.phone).toBe('9123456780'),
    );
    expect((usePOSStore.getState().customer as { gstin?: string })?.gstin).toBe(
      '20AABCU9603R1ZM',
    );
    // ...and the patient + Rx chosen for it are still attached.
    expect(usePOSStore.getState().patient?.id).toBe('pt-1');
    expect(usePOSStore.getState().prescription?.id).toBe('rx-1');
  });

  it('shows the server refusal verbatim and leaves the bill untouched', async () => {
    seedBill();
    updateCustomer.mockRejectedValue({
      response: { data: { detail: 'Mobile 9123456780 already belongs to another customer' } },
    });
    render(<CustomerCardWithLoyalty />);
    fireEvent.click(editButton());
    fireEvent.change(screen.getByPlaceholderText('10-digit mobile'), {
      target: { value: '9123456780' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await screen.findByText(/already belongs to another customer/);
    expect(usePOSStore.getState().customer?.phone).toBe('9876543210');
  });
});
