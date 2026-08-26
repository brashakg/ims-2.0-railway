// ============================================================================
// IMS 2.0 - Add / Edit Supplier modal
// ============================================================================
// Two owner-reported problems meet here:
//   1. "edit vendor button is not working" -- there was no editor at all; this
//      modal could only CREATE. These tests drive it in edit mode and prove the
//      save round-trips through PUT /vendors/{id}.
//   2. "make sure vendor gst calculations are done according to state using gst
//      no" -- the form used to have a free-typed State box sitting next to the
//      GSTIN, so the two could disagree. The state is now READ OFF the GSTIN
//      and shown back, and is only asked for when there is no GSTIN.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));

vi.mock('../../../services/api', () => ({
  vendorsApi: { createVendor: vi.fn(), updateVendor: vi.fn() },
}));

vi.mock('../../../services/api/entities', () => ({
  entitiesApi: {
    meta: vi.fn().mockResolvedValue({
      state_codes: [
        { code: '20', name: 'Jharkhand' },
        { code: '27', name: 'Maharashtra' },
      ],
      entity_types: [],
    }),
  },
}));

import { SupplierFormModal } from '../SupplierFormModal';
import { vendorsApi } from '../../../services/api';
import type { Supplier } from '../purchaseTypes';

const updateVendor = vendorsApi.updateVendor as unknown as ReturnType<typeof vi.fn>;
const createVendor = vendorsApi.createVendor as unknown as ReturnType<typeof vi.fn>;

const existing: Supplier = {
  id: 'v1',
  name: 'Universal Optics',
  code: 'SUP004',
  contactPerson: 'Rakesh Sinha',
  phone: '9000000000',
  email: 'r@universal.in',
  address: '12 Main Road',
  city: 'Ranchi',
  state: 'Jharkhand',
  stateCode: '20',
  gstNumber: '20AABCU9603R1Z1',
  paymentTerms: 30,
  creditLimit: 250000,
  currentOutstanding: 0,
  rating: 4,
  totalPurchases: 100000,
  lastPurchaseDate: '',
  performance: { onTimeDelivery: 90, qualityScore: 90, priceCompetitiveness: 90 },
};

beforeEach(() => {
  vi.clearAllMocks();
  updateVendor.mockResolvedValue({ vendor_id: 'v1', message: 'Vendor updated successfully' });
  createVendor.mockResolvedValue({ vendor_id: 'new-1' });
});

const label = (text: RegExp | string) => screen.getByLabelText(text);

describe('SupplierFormModal in edit mode', () => {
  it('opens prefilled with the vendor it was given', () => {
    render(<SupplierFormModal supplier={existing} onClose={() => {}} onSaved={() => {}} />);
    expect(screen.getByText(/edit supplier/i)).toBeInTheDocument();
    expect(label(/company name/i)).toHaveValue('Universal Optics');
    expect(label(/contact person/i)).toHaveValue('Rakesh Sinha');
    expect(label(/gst number/i)).toHaveValue('20AABCU9603R1Z1');
    expect(label(/credit limit/i)).toHaveValue(250000);
  });

  it('saves the edit through PUT and hands the updated supplier back', async () => {
    const onSaved = vi.fn();
    render(<SupplierFormModal supplier={existing} onClose={() => {}} onSaved={onSaved} />);

    fireEvent.change(label(/contact person/i), { target: { value: 'Sunita Devi' } });
    fireEvent.change(label(/payment terms/i), { target: { value: '45' } });
    fireEvent.click(screen.getByRole('button', { name: /save supplier/i }));

    await waitFor(() => expect(updateVendor).toHaveBeenCalledTimes(1));
    const [vendorId, payload] = updateVendor.mock.calls[0];
    expect(vendorId).toBe('v1');
    expect(payload).toMatchObject({
      trade_name: 'Universal Optics',
      contact_person: 'Sunita Devi',
      credit_days: 45,
      gstin: '20AABCU9603R1Z1',
    });
    expect(createVendor).not.toHaveBeenCalled();

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onSaved.mock.calls[0][0]).toMatchObject({
      id: 'v1',
      contactPerson: 'Sunita Devi',
      paymentTerms: 45,
    });
  });

  it('surfaces the server\'s reason when the save is refused', async () => {
    updateVendor.mockRejectedValue({
      response: { data: { detail: 'Vendor with this GSTIN already exists' } },
    });
    render(<SupplierFormModal supplier={existing} onClose={() => {}} onSaved={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /save supplier/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith('Vendor with this GSTIN already exists'),
    );
  });
});

describe('SupplierFormModal state-from-GSTIN', () => {
  it('shows the state the GSTIN encodes, without the user picking one', async () => {
    render(<SupplierFormModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(label(/gst number/i), { target: { value: '27AAPFU0939F1ZV' } });
    expect(await screen.findByText(/Maharashtra/)).toBeInTheDocument();
    // No state box to disagree with the number.
    expect(screen.queryByLabelText(/^state/i)).not.toBeInTheDocument();
  });

  it('changes the state when the GSTIN changes', async () => {
    render(<SupplierFormModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(label(/gst number/i), { target: { value: '27AAPFU0939F1ZV' } });
    expect(await screen.findByText(/Maharashtra/)).toBeInTheDocument();
    fireEvent.change(label(/gst number/i), { target: { value: '20AABCU9603R1Z1' } });
    expect(await screen.findByText(/Jharkhand/)).toBeInTheDocument();
  });

  it('asks for a state only when there is no GSTIN', () => {
    render(<SupplierFormModal onClose={() => {}} onCreated={() => {}} />);
    expect(screen.getByLabelText(/^state/i)).toBeInTheDocument();
  });

  it('refuses a malformed GSTIN before it reaches the server, naming the problem', async () => {
    render(<SupplierFormModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(label(/company name/i), { target: { value: 'Acme' } });
    fireEvent.change(label(/phone/i), { target: { value: '9000000000' } });
    fireEvent.change(label(/gst number/i), { target: { value: '27AAPFU0939F1Z' } });
    fireEvent.click(screen.getByRole('button', { name: /save supplier/i }));

    expect(await screen.findByText(/15 characters/i)).toBeInTheDocument();
    expect(createVendor).not.toHaveBeenCalled();
  });

  it('sends the state code it derived, so the server and the form agree', async () => {
    render(<SupplierFormModal onClose={() => {}} onCreated={() => {}} />);
    fireEvent.change(label(/company name/i), { target: { value: 'Acme Lens Co' } });
    fireEvent.change(label(/phone/i), { target: { value: '9000000000' } });
    fireEvent.change(label(/gst number/i), { target: { value: '27AAPFU0939F1ZV' } });
    fireEvent.click(screen.getByRole('button', { name: /save supplier/i }));

    await waitFor(() => expect(createVendor).toHaveBeenCalledTimes(1));
    expect(createVendor.mock.calls[0][0]).toMatchObject({
      gstin: '27AAPFU0939F1ZV',
      state: '27',
      gstin_status: 'REGISTERED',
    });
  });
});
