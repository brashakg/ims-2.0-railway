// ============================================================================
// IMS 2.0 - Purchase invoice pickers: the empty states name who receives
// ============================================================================
// Receiving is managers only (owner 2026-09-28); invoices are booked by the
// accountant, who can no longer open the Goods Receipt screen. An empty picker
// used to tell them to receive the goods themselves -- it now says the shop's
// store manager does.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const apis = vi.hoisted(() => ({
  getGRNs: vi.fn().mockResolvedValue({ grns: [] }),
  getOpenDcs: vi.fn().mockResolvedValue([]),
}));

vi.mock('../../../services/api', () => ({ vendorsApi: { getGRNs: apis.getGRNs } }));
vi.mock('../../../services/api/vendorAp', () => ({
  purchaseInvoicesApi: { getOpenDcs: apis.getOpenDcs },
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1', roles: ['ACCOUNTANT'] } }),
}));

import { GrnPickerModal, DcPickerModal } from '../invoices/pickers';

describe('empty invoice pickers', () => {
  it('no accepted receipt: the store manager receives, not the accountant', async () => {
    render(<GrnPickerModal onClose={() => {}} onPicked={() => {}} />);
    expect(await screen.findByText(/No accepted GRNs to invoice/)).toHaveTextContent(/store manager/);
  });

  it('no open Delivery Challan: the store manager logs it', async () => {
    render(<DcPickerModal suppliers={[]} onClose={() => {}} onPicked={() => {}} />);
    expect(await screen.findByText(/No open Delivery Challans/)).toHaveTextContent(/store manager/);
  });
});
