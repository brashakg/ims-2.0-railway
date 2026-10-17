// ============================================================================
// A debit note's money is for the owner and accounts only (owner ruling 2026-10-12)
// ============================================================================
// Issuing the note, its total and its Tally voucher (nothing but money) are
// shown to COST_ROLES only. A manager sees the note's number and can print it
// (the server masks the print); he is shown no issue button, no total and no
// Tally export.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
const dnMock = vi.hoisted(() => ({ list: vi.fn() }));
const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' }));

vi.mock('../../../services/api/client', () => ({ default: apiMock }));
vi.mock('../../../services/api/rtvDebitNotes', () => ({ rtvDebitNotesApi: dnMock }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles: [auth.role] } }),
}));
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({ data: [{ store_id: 'BV-TEST-01', name: 'Test shop' }], isLoading: false }),
}));

import { VendorReturns } from '../VendorReturns';

const ret = (id: string, vendor: string) => ({
  return_id: id,
  vendor_id: 'V1',
  vendor_name: vendor,
  store_id: 'BV-TEST-01',
  return_type: 'credit_note',
  status: 'approved',
  created_at: '2026-10-12T10:00:00',
  items: [{ product_id: 'P1', product_name: 'RB Frame', quantity: 1, reason: 'defective' }],
});
// A note as an older (unmasked) server would have sent it: the screen must
// not show its money to a manager even then.
const NOTE = {
  debit_note_id: 'DN-1',
  debit_note_number: 'DN/26-27/0001',
  rtv_ref_id: 'VR1',
  totals_rupees: { grand_total: 4840.38 },
};

async function open(role: string, vendor: string) {
  auth.role = role;
  apiMock.get.mockImplementation((url: string) =>
    Promise.resolve({
      data: url.startsWith('/vendor-returns') ? { returns: [ret('VR1', 'Acme'), ret('VR2', 'Zeta')] } : { vendors: [] },
    }),
  );
  dnMock.list.mockResolvedValue({ debit_notes: [NOTE], total: 1 });
  render(
    <MemoryRouter>
      <VendorReturns />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText(vendor));
}

describe('the GST debit note on Vendor Returns', () => {
  beforeEach(() => vi.clearAllMocks());

  it.each(['STORE_MANAGER', 'AREA_MANAGER'])('shows %s the issued note without its money', async (role) => {
    await open(role, 'Acme');
    expect(screen.getByText('DN/26-27/0001')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Print' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Export Tally' })).toBeNull();
    expect(document.body.textContent).not.toContain('4,840');
  });

  it.each(['STORE_MANAGER', 'AREA_MANAGER'])('gives %s no button to issue one', async (role) => {
    await open(role, 'Zeta');
    expect(screen.getByText('Not yet issued')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Issue Debit Note' })).toBeNull();
  });

  it.each(['ACCOUNTANT', 'ADMIN'])('keeps the total, the Tally export and the issue button for %s', async (role) => {
    await open(role, 'Acme');
    expect(screen.getByRole('button', { name: 'Export Tally' })).toBeTruthy();
    expect(document.body.textContent).toContain('4,840.38');
    fireEvent.click(screen.getByText('Zeta'));
    expect(screen.getByRole('button', { name: 'Issue Debit Note' })).toBeTruthy();
  });
});
