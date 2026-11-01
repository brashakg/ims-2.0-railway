import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const api = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }));
const notes = vi.hoisted(() => ({ list: vi.fn() }));
const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' }));
vi.mock('../../../services/api/client', () => ({ default: api }));
vi.mock('../../../services/api/rtvDebitNotes', () => ({ rtvDebitNotesApi: notes }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1', roles: [auth.role] } }),
}));
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({ data: [{ store_id: 'S1', name: 'Test shop' }], isLoading: false }),
}));

import { VendorReturns } from '../VendorReturns';

const draft = {
  return_id: 'VR-DRAFT', vendor_id: 'V1', vendor_name: 'Acme', store_id: 'S1',
  status: 'draft', return_type: null, receipt_rejection: true,
  debit_note_draft_status: 'DRAFT', total_value: null, created_at: '2026-10-18T09:00:00+05:30',
  items: [{ product_id: 'P1', product_name: 'Frame', quantity: 1, reason: 'defective', unit_price: null }],
};

beforeEach(() => {
  vi.clearAllMocks();
  auth.role = 'STORE_MANAGER';
  api.get.mockImplementation((url: string) => Promise.resolve({ data:
    url === '/vendor-returns/' ? { returns: [draft] } :
      url === '/vendor-returns/VR-DRAFT' ? draft : { vendors: [] },
  }));
  notes.list.mockResolvedValue({ debit_notes: [] });
  api.patch.mockImplementation((_url, body) => Promise.resolve({ data: { return: { ...draft, ...body } } }));
});

function open(linked = false) {
  render(<MemoryRouter initialEntries={[linked ? '/purchase/vendor-returns?return_id=VR-DRAFT' : '/purchase/vendor-returns']}><VendorReturns /></MemoryRouter>);
}

describe('receipt rejection draft handoff', () => {
  it('requires a disposition, confirms the same draft, and never claims it was issued', async () => {
    open();
    fireEvent.click(await screen.findByText('Acme'));
    expect(screen.getByRole('button', { name: 'Confirm return' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Issue Debit Note' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Print' })).toBeNull();
    fireEvent.change(screen.getByLabelText('Return type'), { target: { value: 'replacement' } });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm return' }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/vendor-returns/VR-DRAFT/status', { status: 'created', return_type: 'replacement' }));
    await screen.findByRole('button', { name: 'Approve' });
    expect(screen.queryByRole('button', { name: 'Issue Debit Note' })).toBeNull();
  });

  it('preselects the receiver choice but still requires explicit confirmation', async () => {
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url === '/vendor-returns/' ? { returns: [{ ...draft, return_type: 'credit_note' }] } : { vendors: [] } }));
    open();
    fireEvent.click(await screen.findByText('Acme'));
    expect(screen.getByLabelText('Return type')).toHaveValue('credit_note');
    expect(api.patch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm return' }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/vendor-returns/VR-DRAFT/status', { status: 'created', return_type: 'credit_note' }));
  });

  it('does not let Accounts issue an unconfirmed draft even if it has a price', async () => {
    auth.role = 'ACCOUNTANT';
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url === '/vendor-returns/' ? { returns: [{ ...draft, items: [{ ...draft.items[0], unit_price: 100 }] }] } : { vendors: [] } }));
    open();
    fireEvent.click(await screen.findByText('Acme'));
    expect(screen.queryByRole('button', { name: 'Issue Debit Note' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Export Tally' })).toBeNull();
    expect(screen.getByText('Confirm the return before issuing a debit note')).toBeInTheDocument();
  });

  it('opens the existing draft from quarantine even when it is outside the first list page', async () => {
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url === '/vendor-returns/' ? { returns: [] } : url === '/vendor-returns/VR-DRAFT' ? draft : { vendors: [] } }));
    open(true);
    await screen.findByRole('button', { name: 'Confirm return' });
    expect(api.get).toHaveBeenCalledWith('/vendor-returns/VR-DRAFT');
    expect(screen.getByText('Draft — Accounts review. No debit note has been issued.')).toBeInTheDocument();
  });

  it('reopens cancelled rejected custody on the same return and requires fresh confirmation', async () => {
    const cancelled = { ...draft, status: 'cancelled', return_type: 'replacement', debit_note_draft_status: 'CANCELLED' };
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url === '/vendor-returns/' ? { returns: [cancelled] } : { vendors: [] } }));
    api.patch.mockResolvedValue({ data: { return: { ...cancelled, status: 'draft', debit_note_draft_status: 'DRAFT' } } });
    open(true);
    fireEvent.click(await screen.findByRole('button', { name: 'Reopen draft' }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/vendor-returns/VR-DRAFT/status', { status: 'draft' }));
    await screen.findByRole('button', { name: 'Confirm return' });
    expect(screen.getByLabelText('Return type')).toHaveValue('replacement');
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Issue Debit Note' })).toBeNull();
  });

  it.each([
    { receipt_rejection: false },
    { issued_debit_note_id: 'DN1', debit_note_draft_status: 'ISSUED' },
  ])('does not reopen ordinary or already-issued cancelled returns: %j', async (extra) => {
    const cancelled = { ...draft, status: 'cancelled', ...extra };
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url === '/vendor-returns/' ? { returns: [cancelled] } : { vendors: [] } }));
    open(true);
    await screen.findByText('Items');
    expect(screen.queryByRole('button', { name: 'Reopen draft' })).toBeNull();
  });

  it('shows the receiver free-text rejection reason on the draft', async () => {
    api.get.mockImplementation((url: string) => Promise.resolve({ data: url === '/vendor-returns/' ? { returns: [{ ...draft, items: [{ ...draft.items[0], reason: 'Damaged packaging and scratched lens' }] }] } : { vendors: [] } }));
    open(true);
    expect(await screen.findByText('Damaged packaging and scratched lens')).toBeInTheDocument();
  });
});

describe('the damaged piece photo (S2b: the debit note evidence)', () => {
  const withPhoto = {
    ...draft, grn_id: 'G1', grn_line_index: 0,
    items: [{ ...draft.items[0], photo_file_id: 'F-DMG' }],
  };

  it('accounts open the photo kept from receiving', async () => {
    auth.role = 'ACCOUNTANT';
    const win = { location: { href: '' }, close: vi.fn() };
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    const blob = new Blob(['png'], { type: 'image/png' });
    URL.createObjectURL = vi.fn(() => 'blob:dmg');
    api.get.mockImplementation((url: string) => Promise.resolve({ data:
      url === '/vendor-returns/' ? { returns: [withPhoto] } :
        url === '/vendors/grn/G1/document' ? blob : { vendors: [] },
    }));
    open();
    fireEvent.click(await screen.findByText('Acme'));
    fireEvent.click(screen.getByRole('button', { name: 'Damage photo' }));
    await waitFor(() => expect(win.location.href).toBe('blob:dmg'));
    expect(api.get).toHaveBeenCalledWith('/vendors/grn/G1/document', { params: { line: 0 }, responseType: 'blob' });
    openSpy.mockRestore();
  });

  it('a line with no photo offers none', async () => {
    auth.role = 'ACCOUNTANT';
    open();
    fireEvent.click(await screen.findByText('Acme'));
    expect(screen.queryByRole('button', { name: 'Damage photo' })).toBeNull();
  });
});
