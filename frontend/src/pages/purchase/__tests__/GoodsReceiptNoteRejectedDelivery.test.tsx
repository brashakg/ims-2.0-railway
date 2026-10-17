// ============================================================================
// IMS 2.0 - Goods Receipt Note: a delivery against an order a cancel closed
// ============================================================================
// Owner ruling 2026-10-08 (c): a delivery that brought cancelled items CAN be
// logged when every one of them is rejected. Once a cancel CLOSED the order
// (CANCELLED, or RECEIVED because the rest was cancelled) the backend takes
// such a receipt, but no screen offered the order. The PO picker now lists it
// under "Log a rejected delivery"; on it every unit that arrived is rejected
// and nothing is accepted, whatever the Rejected box is set to.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

const api = vi.hoisted(() => ({
  getGRNs: vi.fn(),
  getPurchaseOrders: vi.fn(),
  getVendors: vi.fn(),
  createGRN: vi.fn(),
  acceptGRN: vi.fn(),
}));

const cockpit = vi.hoisted(() => ({ uploadDoc: vi.fn() }));

vi.mock('../../../services/api', () => ({ vendorsApi: api }));
vi.mock('../../../services/api/grnCockpit', () => ({ grnCockpitApi: cockpit }));
vi.mock('../../../services/api/products', () => ({ productApi: { getProducts: vi.fn() } }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({ data: [{ store_id: 'S1', store_name: 'Receiving shop' }] }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1', roles: ['STORE_MANAGER'] } }),
}));
vi.mock('../../../components/print/GRNPrint', () => ({ GRNPrint: () => null }));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));
vi.mock('../../../components/labels/UnitLabelsModal', () => ({
  UnitLabelsModal: ({ grnId }: { grnId?: string }) => <div role="dialog">labels for {grnId}</div>,
}));

import { GoodsReceiptNote } from '../GoodsReceiptNote';

const lines = [
  { product_id: 'P1', product_name: 'Frame X', quantity: 5 },
  { product_id: 'P2', product_name: 'Ray-Ban', quantity: 3 },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.getGRNs.mockResolvedValue({ grns: [] });
  api.getVendors.mockResolvedValue({ vendors: [] });
  api.getPurchaseOrders.mockResolvedValue({
    purchase_orders: [
      { po_id: 'PO-OPEN', po_number: 'PO-OPEN', status: 'SENT', closed_by_cancel: false, items: lines },
      { po_id: 'PO-CXL', po_number: 'PO-CXL', status: 'CANCELLED', closed_by_cancel: true, items: lines },
      { po_id: 'PO-DONE', po_number: 'PO-DONE', status: 'RECEIVED', closed_by_cancel: false, items: lines },
    ],
  });
  cockpit.uploadDoc.mockResolvedValue({
    file_id: 'F-1', filename: 'bill.pdf', mime: 'application/pdf', size: 1, sha256: 'x', persisted: true,
  });
  api.createGRN.mockResolvedValue({ grn_id: 'GRN-1' });
  api.acceptGRN.mockResolvedValue({ grn_status: 'ACCEPTED', units_added: 0, po_status: 'CANCELLED' });
});

async function open() {
  const view = render(<GoodsReceiptNote />);
  await waitFor(() => expect(screen.getByDisplayValue('Select a PO…')).toBeInTheDocument());
  return view;
}

async function attachBillAndInvoice() {
  fireEvent.change(screen.getByPlaceholderText('e.g. JJ/24/04/2240'), { target: { value: 'INV-7' } });
  const file = new File(['%PDF'], 'bill.pdf', { type: 'application/pdf' });
  fireEvent.change(screen.getByLabelText('Attach the vendor invoice or challan'), {
    target: { files: [file] },
  });
  await screen.findByText('Attached: bill.pdf');
}

describe('a delivery against an order a cancel closed', () => {
  it('is offered under "Log a rejected delivery", apart from the open orders', async () => {
    const { container } = await open();
    const group = container.querySelector('optgroup[label="Log a rejected delivery"]');
    expect(group).not.toBeNull();
    expect([...group!.querySelectorAll('option')].map((o) => o.value)).toEqual(['PO-CXL']);
    expect(screen.getByRole('option', { name: 'PO-OPEN' }).closest('optgroup')).toBeNull();
    // A plain received order with no cancel has nothing to log against.
    expect(screen.queryByRole('option', { name: /PO-DONE/ })).toBeNull();
  });

  it('posts every unit that arrived as rejected - nothing accepted - with the bill', async () => {
    await open();
    fireEvent.change(screen.getByDisplayValue('Select a PO…'), { target: { value: 'PO-CXL' } });
    // Nothing is due: the receiver counts what came.
    expect(screen.getByLabelText('Received units on line 2')).toHaveValue(0);
    fireEvent.change(screen.getByLabelText('Received units on line 2'), { target: { value: '3' } });
    const rejected = screen.getByLabelText('Rejected units on line 2');
    expect(rejected).toBeDisabled();
    expect(rejected).toHaveValue(3);
    // Trying to accept a unit: the box stays at every unit that arrived.
    fireEvent.change(rejected, { target: { value: '1' } });
    expect(screen.getByLabelText('Rejected units on line 2')).toHaveValue(3);
    fireEvent.change(screen.getByLabelText('Rejection reason on line 2'), {
      target: { value: 'order was cancelled' },
    });
    fireEvent.click(screen.getByLabelText(/Tally line 2/));
    await attachBillAndInvoice();

    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));

    await waitFor(() => expect(api.createGRN).toHaveBeenCalledTimes(1));
    const body = api.createGRN.mock.calls[0][0];
    expect(body).toMatchObject({ po_id: 'PO-CXL', grn_subtype: 'STANDARD', attachment_file_id: 'F-1' });
    // Only the line that arrived: a P1 line at 0 would read as a shortage.
    expect(body.items).toEqual([
      expect.objectContaining({
        product_id: 'P2',
        received_qty: 3,
        accepted_qty: 0,
        rejected_qty: 3,
        rejection_reason: 'order was cancelled',
        tallied: true,
      }),
    ]);
    await waitFor(() => expect(api.acceptGRN).toHaveBeenCalledWith('GRN-1'));
    expect(toastMock.success).toHaveBeenCalledWith(expect.stringContaining('nothing added to stock'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('is never a Delivery Challan - no bill is due, so it would list as unbilled for good', async () => {
    const { container } = await open();
    const dcBox = screen.getByLabelText(/This is a Delivery Challan/);
    fireEvent.change(screen.getByDisplayValue('Select a PO…'), { target: { value: 'PO-CXL' } });
    expect(screen.getByLabelText('Received units on line 2')).toBeInTheDocument();
    // Ticking the box after picking the closed order drops it.
    fireEvent.click(dcBox);
    expect(screen.queryByLabelText('Received units on line 2')).toBeNull();
    expect(container.querySelector('optgroup[label="Log a rejected delivery"]')).toBeNull();
    expect(screen.queryByRole('option', { name: /PO-CXL/ })).toBeNull();
    expect(screen.getByRole('option', { name: 'PO-OPEN' })).toBeInTheDocument();
    fireEvent.click(dcBox);
    expect(container.querySelector('optgroup[label="Log a rejected delivery"]')).not.toBeNull();
  });

  it('with nothing counted, does not post', async () => {
    await open();
    fireEvent.change(screen.getByDisplayValue('Select a PO…'), { target: { value: 'PO-CXL' } });
    await attachBillAndInvoice();
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining('Enter the units that arrived')),
    );
    expect(api.createGRN).not.toHaveBeenCalled();
  });
});

describe('a standard receipt on this screen', () => {
  it('needs the vendor bill attached before it posts', async () => {
    await open();
    fireEvent.change(screen.getByDisplayValue('Select a PO…'), { target: { value: 'PO-OPEN' } });
    fireEvent.click(screen.getByLabelText(/Tally line 1/));
    fireEvent.click(screen.getByLabelText(/Tally line 2/));
    fireEvent.change(screen.getByPlaceholderText('e.g. JJ/24/04/2240'), { target: { value: 'INV-7' } });
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining('Attach the vendor invoice')),
    );
    expect(api.createGRN).not.toHaveBeenCalled();
  });
});
