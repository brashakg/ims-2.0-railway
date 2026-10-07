// ============================================================================
// An approve on a bill with NO id (round 13, item 8)
// ============================================================================
// The real purchaseInvoicesApi.approveException refuses an id-less bill before
// any request. Each of the three approve screens must then show an ERROR toast,
// never the "released for payment" success toast, and send no POST.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../hooks/usePOSQueries', () => ({
  // PurchaseShopName (the booking form / picker shop line) reads the store list.
  useStores: () => ({ data: [] }),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 's1', roles: ['ADMIN'] }, hasRole: () => true }),
}));

// Only the transport is faked: the real vendorAp guard runs.
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('../../../services/api/client', () => ({ default: http }));

vi.mock('../../../services/api/purchaseRecon', () => ({
  purchaseReconApi: {
    getRecon: vi.fn().mockResolvedValue(null),
    upsertRecon: vi.fn(),
    getWorklists: vi.fn().mockResolvedValue({
      stock_yet_to_receive: [], vendor_returns: [],
      pending_credit_notes_scheme: [], pending_credit_notes_return: [],
    }),
    markSchemeCnReceived: vi.fn(),
  },
}));

import ReconConsole from '../ReconConsole';
import { ExceptionsPanel } from '../invoices/ExceptionsPanel';
import { InvoiceDetailDrawer } from '../invoices/InvoiceDetailDrawer';
import type { PurchaseInvoice } from '../../../services/api/vendorAp';

// A bill on hold whose row carries no id at all.
const IDLESS = {
  vendor_id: 'v-1',
  vendor_name: 'Luxottica India',
  vendor_invoice_no: 'INV-NOID',
  vendor_invoice_date: '2026-07-01',
  lines: [],
  taxable_amount: 1000, cgst: 25, sgst: 25, igst: 0, tax_amount: 50, total_amount: 1050,
  status: 'OUTSTANDING',
  match_status: 'ON_HOLD_EXCEPTION',
  match_detail: { match_status: 'ON_HOLD_EXCEPTION', lines: [], exceptions: ['Qty variance 20%'] },
} as unknown as PurchaseInvoice;

const REASON = 'Supplier confirmed the short shipment by phone';

beforeEach(() => {
  vi.clearAllMocks();
  http.get.mockResolvedValue({ data: {} });
});

function expectRefused() {
  expect(http.post).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledTimes(1);
  expect(String(toast.error.mock.calls[0][0])).toMatch(/no id, so nothing was sent/);
}

describe('approving a bill with no id', () => {
  it('ExceptionsPanel quick-approve: error toast, no success toast, no POST', async () => {
    const onApproved = vi.fn();
    render(<ExceptionsPanel invoices={[IDLESS]} onApproved={onApproved} onViewDetail={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /^approve$/i }));
    fireEvent.change(screen.getByPlaceholderText(/min 10 chars/i), { target: { value: REASON } });
    fireEvent.click(screen.getByRole('button', { name: /approve exception/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expectRefused();
    expect(onApproved).not.toHaveBeenCalled();
  });

  it('InvoiceDetailDrawer: error toast, no success toast, no POST', async () => {
    const onChanged = vi.fn();
    render(<InvoiceDetailDrawer invoice={IDLESS} config={null} onClose={vi.fn()} onChanged={onChanged} />);
    fireEvent.change(await screen.findByRole('textbox'), { target: { value: REASON } });
    fireEvent.click(screen.getByRole('button', { name: /approve/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expectRefused();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('ReconConsole review modal: error toast, no success toast, no POST', async () => {
    http.get.mockResolvedValue({ data: { purchase_invoices: [IDLESS], total: 1 } });
    render(<MemoryRouter><ReconConsole /></MemoryRouter>);
    fireEvent.click(await screen.findByText(/review variance \/ approve/i));
    fireEvent.change(screen.getByPlaceholderText(/min 10 chars/i), { target: { value: REASON } });
    fireEvent.click(screen.getByRole('button', { name: /approve exception/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expectRefused();
  });
});
