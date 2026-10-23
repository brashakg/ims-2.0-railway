// ============================================================================
// IMS 2.0 - The one receiving screen (/purchase/receive)
// ============================================================================
// Owner ruling: ONE receiving screen. Express panel, step-by-step fallback and
// the classic /purchase/grn form are one form now; these drive it end to end
// with the HTTP-facing APIs mocked: the inbox, a clean box (express, "Sent to
// accounts"), a damaged piece (create + accept, quarantine + draft debit
// note), the duplicate-bill block, the half-finished receipt, bought without
// PO (no price for a manager, a cost for accounts), a delivery challan, extra
// and rejected deliveries, contact-lens batch/expiry, the scan box, the
// receipts still waiting, and the history.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
const api = vi.hoisted(() => ({
  getGRNs: vi.fn(),
  getPurchaseOrders: vi.fn(),
  getVendors: vi.fn(),
  createGRN: vi.fn(),
  acceptGRN: vi.fn(),
  voidGRN: vi.fn(),
  dropOverOrder: vi.fn(),
  getVendor: vi.fn(),
}));
const printed = vi.hoisted(() => ({ grn: null as null | Record<string, unknown> }));
const cockpit = vi.hoisted(() => ({ uploadDoc: vi.fn(), expressReceive: vi.fn() }));
const products = vi.hoisted(() => ({ getProduct: vi.fn(), getProducts: vi.fn() }));
const auth = vi.hoisted(() => ({ roles: ['STORE_MANAGER'] as string[] }));
const online = vi.hoisted(() => ({ value: false }));

vi.mock('../../../services/api/inventory', () => ({ vendorsApi: api }));
vi.mock('../../../services/api/grnCockpit', () => ({ grnCockpitApi: cockpit }));
vi.mock('../../../services/api/products', () => ({ productApi: products }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', roles: auth.roles, activeStoreId: 'S1' },
    hasRole: (want: string[]) => want.some((r) => auth.roles.includes(r)),
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => online.value }));
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({ data: [{ store_id: 'S1', store_name: 'Dhanbad' }, { store_id: 'S2', store_name: 'Pune' }] }),
}));
vi.mock('../../../components/labels/UnitLabelsModal', () => ({
  UnitLabelsModal: ({ grnId }: { grnId: string }) => <div>labels for {grnId}</div>,
}));
vi.mock('../../../components/print/GRNPrint', () => ({
  GRNPrint: ({ grn }: { grn: Record<string, unknown> }) => {
    printed.grn = grn;
    return null;
  },
}));
vi.mock('../../../components/print/storeIdentity', () => ({ resolveStoreIdentity: vi.fn().mockResolvedValue(null) }));
// The quick-add form has its own suite: here it only hands back the dealer.
vi.mock('../SupplierFormModal', () => ({
  SupplierFormModal: ({ onCreated }: { onCreated: (s: { id: string; name: string }) => void }) => (
    <button type="button" onClick={() => onCreated({ id: 'V-NEW', name: 'Sharma Optical' })}>save dealer</button>
  ),
}));

import { ApiError, buildApiError } from '../../../services/api/client';
import type { AxiosError } from 'axios';

/** What the api client really rejects with, for a server answer. */
const serverSaid = (status: number, detail: unknown) =>
  buildApiError({ message: `Request failed with status code ${status}`, response: { status, data: { detail } } } as unknown as AxiosError<never>);
import { ReceiveGoods } from '../ReceiveGoods';

const PO = {
  po_id: 'PO1',
  po_number: 'PO-0412',
  vendor_id: 'V1',
  vendor_name: 'Safilo India',
  status: 'SENT',
  delivery_store_id: 'S1',
  expected_date: '2026-10-25',
  items: [
    { po_item_id: 'i1', product_id: 'P1', product_name: 'Carrera 1234', sku: 'CAR-1234', quantity: 4, received_qty: 0 },
    { po_item_id: 'i2', product_id: 'P2', product_name: 'Acuvue Oasys', sku: 'ACU-1', quantity: 2, received_qty: 0 },
  ],
};
const LATER = { ...PO, po_id: 'PO2', po_number: 'PO-0500', vendor_name: 'Luxottica', expected_date: '2026-11-20' };
const UNDATED = { ...PO, po_id: 'PO3', po_number: 'PO-0300', vendor_name: 'Essilor', expected_date: null };
const DONE = { ...PO, po_id: 'PO4', po_number: 'PO-0100', status: 'RECEIVED', extra_receipt_allowed: true,
  items: [{ po_item_id: 'd1', product_id: 'P1', product_name: 'Carrera 1234', quantity: 2, received_qty: 2 }] };
const CLOSED = { ...PO, po_id: 'PO5', po_number: 'PO-0099', status: 'CANCELLED', closed_by_cancel: true };

let waiting: Record<string, unknown[]> = {};
let history: unknown[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  auth.roles = ['STORE_MANAGER'];
  online.value = false;
  waiting = {};
  history = [];
  printed.grn = null;
  api.getPurchaseOrders.mockResolvedValue({ purchase_orders: [UNDATED, LATER, PO, DONE, CLOSED] });
  api.getGRNs.mockImplementation(async (p: { status?: string }) => ({ grns: p?.status ? waiting[p.status] ?? [] : history }));
  api.getVendors.mockResolvedValue({ vendors: [{ vendor_id: 'V-77', trade_name: 'Frames Wala' }] });
  api.createGRN.mockResolvedValue({ grn_id: 'G-NEW', grn_number: 'GRN-0400' });
  api.acceptGRN.mockResolvedValue({ grn_status: 'ACCEPTED', units_added: 3, quarantined_units: 1, po_status: 'PARTIALLY_RECEIVED' });
  cockpit.uploadDoc.mockResolvedValue({ file_id: 'F-BILL', filename: 'SAF-8841.pdf', mime: 'application/pdf', size: 2048, persisted: true });
  cockpit.expressReceive.mockResolvedValue({
    grn_id: 'G-EXP', grn_number: 'GRN-0401', accepted_units: 6, po_status: 'RECEIVED',
    match_preview: { match_status: 'MATCHED', exception_count: 0 }, invoice_draft: null, accountant_task_id: 't1',
  });
  products.getProduct.mockImplementation(async (id: string) =>
    id === 'P2' ? { product_id: 'P2', category: 'CONTACT_LENS', gtin: '8901234567890' } : { product_id: id, category: 'FRAME' });
  products.getProducts.mockResolvedValue({ products: [{ product_id: 'P-FR1', name: 'Acme Aviator', sku: 'FR-0001' }] });
});

const renderAt = (url = '/purchase/receive') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <ReceiveGoods />
    </MemoryRouter>,
  );

const receiveBtn = () => screen.getByRole('button', { name: /^Receive \d+ piece/ });

async function openPO() {
  renderAt();
  const card = (await screen.findByText('PO-0412')).closest('.card') as HTMLElement;
  fireEvent.click(within(card).getByRole('button', { name: /Receive this box/ }));
  await screen.findByText(/What arrived/);
  await waitFor(() => expect(products.getProduct).toHaveBeenCalledTimes(2));
}

async function attachBill(no = 'SAF-8841') {
  if (no) fireEvent.change(screen.getByLabelText(/^Vendor bill no/), { target: { value: no } });
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(['%PDF'], 'SAF-8841.pdf', { type: 'application/pdf' })] } });
  await screen.findByText('SAF-8841.pdf');
}

describe('the deliveries inbox', () => {
  it('lists every receivable order soonest first, receipt candidates included, in the shop scope', async () => {
    renderAt();
    await screen.findByText('PO-0412');
    expect(api.getPurchaseOrders).toHaveBeenCalledWith({ store_id: 'S1', receipt_candidates: true });
    const cards = screen.getAllByRole('button', { name: /Receive this box/ }).map((b) => b.closest('.card')?.textContent);
    expect(cards.map((t) => t?.match(/PO-\d{4}/)?.[0])).toEqual(['PO-0412', 'PO-0500', 'PO-0300']);
    // Fully received / cancel-closed orders are not boxes on their way.
    expect(screen.getByRole('option', { name: /PO-0100.*count extra goods/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /PO-0099.*closed by a cancel/ })).toBeInTheDocument();
  });

  it('an online store is warned up front that it holds no stock', async () => {
    online.value = true;
    renderAt();
    expect(await screen.findByText(/online store — it holds no stock/)).toBeInTheDocument();
  });

  it('the deep link ?po_id= opens that order; an unknown one says so', async () => {
    const first = renderAt('/purchase/receive?vendor_id=V1&po_id=PO2');
    expect(await screen.findByText(/What arrived/)).toBeInTheDocument();
    expect(screen.getByText('PO-0500')).toBeInTheDocument();
    first.unmount();
    renderAt('/purchase/receive?po_id=NOPE');
    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(expect.stringMatching(/not open for receiving/)));
  });
});

describe('a clean box (express)', () => {
  it('"Everything arrived as ordered" ticks all; Receive stays off until the bill is in', async () => {
    await openPO();
    expect(screen.getByText('0 of 2 counted', { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Everything arrived as ordered/ }));
    expect(screen.getByText('2 of 2 counted', { exact: false })).toBeInTheDocument();
    expect(receiveBtn()).toBeDisabled();
    expect(screen.getByTestId('receive-problem')).toHaveTextContent(/vendor bill number/);
    await attachBill();
    expect(receiveBtn()).toBeEnabled();
    expect(screen.queryByLabelText(/Cost on line/)).toBeNull(); // the manager only counts
  });

  it('goes express and shows the receipt, "Sent to accounts" and Print N labels', async () => {
    await openPO();
    fireEvent.click(screen.getByRole('button', { name: /Everything arrived as ordered/ }));
    fireEvent.change(screen.getByLabelText('Shelf for Carrera 1234'), { target: { value: 'c1' } });
    await attachBill();
    fireEvent.click(receiveBtn());
    await screen.findByText('Received and put on the shelf.');
    expect(api.createGRN).not.toHaveBeenCalled();
    const body = cockpit.expressReceive.mock.calls[0][0];
    expect(body).toMatchObject({ po_id: 'PO1', vendor_invoice_no: 'SAF-8841', attachment_file_id: 'F-BILL' });
    expect(body.items).toEqual([
      expect.objectContaining({ product_id: 'P1', received_qty: 4, accepted_qty: 4, rejected_qty: 0, location_code: 'C1' }),
      expect.objectContaining({ product_id: 'P2', received_qty: 2, accepted_qty: 2 }),
    ]);
    expect(screen.getByText(/Sent to accounts/)).toHaveTextContent('Sent to accounts: MATCHED');
    fireEvent.click(screen.getByRole('button', { name: 'Print 6 labels' }));
    expect(screen.getByText('labels for G-EXP')).toBeInTheDocument();
  });

  it('the bill already received: the block names the receipt and offers no retry', async () => {
    cockpit.expressReceive.mockRejectedValue(
      new ApiError('dup', { status: 409, detail: { code: 'GRN_DUPLICATE', grn_number: 'GRN-0377', message: 'Bill SAF-8841 was already received on 9 Oct.' } }),
    );
    await openPO();
    fireEvent.click(screen.getByRole('button', { name: /Everything arrived as ordered/ }));
    await attachBill();
    fireEvent.click(receiveBtn());
    expect(await screen.findByText('This bill was already received (GRN-0377)')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Receive \d+ piece/ })).toBeNull();
    expect(api.createGRN).not.toHaveBeenCalled();
  });

  it('EXPRESS_PARTIAL: the recovery banner leads to the receipts still waiting, highlighted', async () => {
    cockpit.expressReceive.mockRejectedValue(
      new ApiError('partial', { status: 409, detail: { code: 'EXPRESS_PARTIAL', grn_number: 'GRN-0402', message: 'Created but not accepted.' } }),
    );
    await openPO();
    fireEvent.click(screen.getByRole('button', { name: /Everything arrived as ordered/ }));
    await attachBill();
    fireEvent.click(receiveBtn());
    expect(await screen.findByText(/GRN-0402 was saved but is NOT all on the shelf yet/)).toBeInTheDocument();
    waiting.PENDING = [{ grn_id: 'G-2', grn_number: 'GRN-0402', status: 'PENDING', items: [{}] }];
    fireEvent.click(screen.getByRole('button', { name: /Open receipts still waiting/ }));
    expect(await screen.findByText('GRN-0402')).toBeInTheDocument();
    expect(screen.getByTestId('receipts-waiting')).toBeInTheDocument();
  });

  it('the server judging it not clean sends the same lines through create + accept', async () => {
    cockpit.expressReceive.mockRejectedValue(new ApiError('nc', { status: 400, detail: { code: 'EXPRESS_NOT_CLEAN' } }));
    await openPO();
    fireEvent.click(screen.getByRole('button', { name: /Everything arrived as ordered/ }));
    await attachBill();
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(api.acceptGRN).toHaveBeenCalledWith('G-NEW'));
    expect(api.createGRN.mock.calls[0][0]).toMatchObject({ po_id: 'PO1', grn_subtype: 'STANDARD' });
  });
});

describe('a damaged piece, inline on its row', () => {
  it('"Everything arrived as ordered" after a damaged piece keeps the damage: never sellable via express', async () => {
    await openPO();
    fireEvent.change(screen.getByLabelText('Damaged on line 1'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('What is wrong on line 1'), { target: { value: 'Broken hinge' } });
    fireEvent.click(screen.getByLabelText('Replacement coming'));
    fireEvent.click(screen.getByRole('button', { name: /Everything else arrived as ordered/ }));
    expect((screen.getByLabelText('Damaged on line 1') as HTMLInputElement).value).toBe('1');
    // The edited line is the receiver's own count: it is not ticked for him.
    expect((screen.getByLabelText(/I counted line 1/) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText(/I counted line 2/) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByLabelText(/I counted line 1/));
    await attachBill();
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(api.createGRN).toHaveBeenCalled());
    expect(cockpit.expressReceive).not.toHaveBeenCalled();
    expect(api.createGRN.mock.calls[0][0].items[0]).toMatchObject({ accepted_qty: 3, rejected_qty: 1, rejection_disposition: 'replacement' });
  });

  it('asks what is wrong and replacement / credit, then posts it to quarantine with a draft debit note', async () => {
    await openPO();
    fireEvent.click(screen.getByRole('button', { name: /Everything arrived as ordered/ }));
    fireEvent.change(screen.getByLabelText('Damaged on line 1'), { target: { value: '1' } });
    expect(screen.getByText('1 damaged: to quarantine')).toBeInTheDocument();
    expect(screen.getByText(/goes to Quarantine \(never on the shelf\), and a draft debit note/)).toBeInTheDocument();
    await attachBill();
    expect(receiveBtn()).toBeDisabled();
    fireEvent.change(screen.getByLabelText('What is wrong on line 1'), { target: { value: 'Scratched lens' } });
    expect(screen.getByTestId('receive-problem')).toHaveTextContent(/replacement coming/);
    fireEvent.click(screen.getByLabelText('Credit'));
    fireEvent.click(receiveBtn());
    await screen.findByText('Received and put on the shelf.');
    expect(cockpit.expressReceive).not.toHaveBeenCalled();
    const body = api.createGRN.mock.calls[0][0];
    expect(body).toMatchObject({ grn_subtype: 'STANDARD', po_id: 'PO1', vendor_invoice_no: 'SAF-8841', attachment_file_id: 'F-BILL' });
    expect(body.items[0]).toMatchObject({
      received_qty: 4, accepted_qty: 3, rejected_qty: 1, rejection_reason: 'Scratched lens',
      rejection_disposition: 'credit_note', tallied: true,
    });
    expect(screen.getByText(/1 to quarantine, with a draft debit note for accounts/)).toBeInTheDocument();
    // Credit does not close the order line by itself: say what to do.
    expect(screen.getByText(/Credit asked for Carrera 1234/)).toBeInTheDocument();
  });

  it('a receipt saved but not added to stock says so and never invites a second receipt', async () => {
    api.acceptGRN.mockRejectedValue(
      serverSaid(503, 'Some units were received before the stock store stopped responding. Open the receiving screen and accept it again.'),
    );
    await openPO();
    fireEvent.click(screen.getByRole('button', { name: /Everything arrived as ordered/ }));
    fireEvent.change(screen.getByLabelText('Arrived on line 1'), { target: { value: '5' } });
    expect(screen.getByText('1 more than ordered: held for the manager')).toBeInTheDocument();
    await attachBill();
    fireEvent.click(receiveBtn());
    expect(await screen.findByText(/GRN-0400 was saved but is NOT on the shelf yet/)).toBeInTheDocument();
    expect(screen.getByText(/do not receive the box again/)).toBeInTheDocument();
    expect(screen.getByText(/stock store stopped responding/)).toBeInTheDocument();
  });

  it('a receipt whose status did not advance is shown as unfinished, with the way out', async () => {
    api.acceptGRN.mockResolvedValue({
      status_flip_failed: true, units_added: 4, grn_status: 'PENDING',
      message: '4 unit(s) were received, but the receipt status could not be updated.',
    });
    await openPO();
    fireEvent.change(screen.getByLabelText('Damaged on line 1'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('What is wrong on line 1'), { target: { value: 'Scratched lens' } });
    fireEvent.click(screen.getByLabelText('Credit'));
    fireEvent.click(screen.getByRole('button', { name: /Everything else arrived as ordered/ }));
    fireEvent.click(screen.getByLabelText(/I counted line 1/));
    await attachBill();
    fireEvent.click(receiveBtn());
    expect(await screen.findByText(/GRN-0400: 4 piece\(s\) are on the shelf, but the receipt did not finish/)).toBeInTheDocument();
    expect(screen.queryByText('Received and put on the shelf.')).toBeNull();
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Open receipts still waiting/ })).toBeInTheDocument();
  });

  it('a server field error (a 422 list) is read out, never "try again"', async () => {
    api.createGRN.mockRejectedValue(
      serverSaid(422, [{ loc: ['body', 'vendor_invoice_date'], msg: 'Value error, the bill date is after today', type: 'value_error' }]),
    );
    await openPO();
    fireEvent.change(screen.getByLabelText('Damaged on line 1'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('What is wrong on line 1'), { target: { value: 'Scratched lens' } });
    fireEvent.click(screen.getByLabelText('Credit'));
    fireEvent.click(screen.getByRole('button', { name: /Everything else arrived as ordered/ }));
    fireEvent.click(screen.getByLabelText(/I counted line 1/));
    await attachBill();
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0][0]).toMatch(/the bill date is after today/);
  });
});

describe('contact lenses and the scan box', () => {
  it('a contact-lens line asks for batch + expiry; short-dated is a warning only', async () => {
    await openPO();
    expect(screen.queryByLabelText('Batch for Carrera 1234')).toBeNull();
    fireEvent.change(screen.getByLabelText('Batch for Acuvue Oasys'), { target: { value: 'L23310' } });
    const in3Months = new Date(Date.now() + 90 * 864e5).toISOString().slice(0, 10);
    fireEvent.change(screen.getByLabelText('Expiry for Acuvue Oasys'), { target: { value: in3Months } });
    expect(screen.getByText('Short-dated: warning only')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Everything arrived as ordered/ }));
    await attachBill();
    expect(receiveBtn()).toBeEnabled();
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(cockpit.expressReceive).toHaveBeenCalled());
    expect(cockpit.expressReceive.mock.calls[0][0].items[1]).toMatchObject({ batch_code: 'L23310', expiry_date: in3Months });
  });

  it('each scan of a GTIN adds one to its line (the first scan starts the count)', async () => {
    await openPO();
    const scan = screen.getByLabelText('Scan a barcode');
    const arrived = screen.getByLabelText('Arrived on line 2') as HTMLInputElement;
    expect(arrived.value).toBe('2');
    for (const _ of [1, 2, 3]) {
      fireEvent.change(scan, { target: { value: '8901234567890' } });
      fireEvent.keyDown(scan, { key: 'Enter' });
    }
    await waitFor(() => expect(arrived.value).toBe('3'));
    expect(screen.getByText('1 more than ordered: held for the manager')).toBeInTheDocument();
  });
});

describe('no purchase order', () => {
  it('scanning a product not yet on the list adds it at 1, and the next scan makes 2', async () => {
    products.getProducts.mockResolvedValue({ products: [{ product_id: 'P-FR1', name: 'Acme Aviator', gtin: '8900000000017' }] });
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: 'Bought without PO' }));
    const scan = await screen.findByLabelText('Scan a barcode');
    fireEvent.change(scan, { target: { value: '8900000000017' } });
    fireEvent.keyDown(scan, { key: 'Enter' });
    const arrived = (await screen.findByLabelText('Arrived on line 1')) as HTMLInputElement;
    expect(arrived.value).toBe('1');
    fireEvent.change(scan, { target: { value: '8900000000017' } });
    fireEvent.keyDown(scan, { key: 'Enter' });
    await waitFor(() => expect(arrived.value).toBe('2'));
  });

  async function typeOneLine() {
    fireEvent.change(screen.getByPlaceholderText(/Search a product to add/), { target: { value: 'frame' } });
    fireEvent.click(await screen.findByText('Acme Aviator'));
    fireEvent.change(screen.getByLabelText('Arrived on line 1'), { target: { value: '2' } });
    fireEvent.click(screen.getByLabelText(/I counted line 1/));
  }

  it('bought without PO, by a manager: dealer + bill photo, no price box, a NO_PO receipt', async () => {
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: 'Bought without PO' }));
    await waitFor(() => expect(api.getVendors).toHaveBeenCalledWith({ limit: 1000 }));
    fireEvent.change(await screen.findByLabelText(/Supplier/), { target: { value: 'V-77' } });
    await typeOneLine();
    expect(screen.queryByLabelText(/Cost on line/)).toBeNull();
    expect(screen.getByText(/You only count\. Prices are never typed here/)).toBeInTheDocument();
    expect(screen.getByTestId('receive-problem')).toHaveTextContent(/no paper, no stock/);
    await attachBill('');
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(api.createGRN).toHaveBeenCalled());
    const body = api.createGRN.mock.calls[0][0];
    expect(body).toMatchObject({ grn_subtype: 'NO_PO', vendor_id: 'V-77', attachment_file_id: 'F-BILL' });
    expect(body.po_id).toBeUndefined();
    expect(body.items[0]).toMatchObject({ product_id: 'P-FR1', received_qty: 2, tallied: true });
    expect(body.items[0].unit_price).toBeUndefined();
    expect(await screen.findByText(/Sent to accounts: a task to book this bill/)).toBeInTheDocument();
  });

  it('bought without PO, by an admin: the cost per unit; a dealer not on file is added and picked', async () => {
    auth.roles = ['ADMIN'];
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: 'Bought without PO' }));
    // The sentence says what THIS person types: a cost, from the dealer's bill.
    expect(screen.getByText(/type each item's cost per unit/)).toBeInTheDocument();
    expect(screen.queryByText(/Prices are never typed here/)).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: /Add the dealer/ }));
    fireEvent.click(screen.getByRole('button', { name: 'save dealer' }));
    expect((screen.getByLabelText(/Supplier/) as HTMLSelectElement).value).toBe('V-NEW');
    await typeOneLine();
    fireEvent.change(screen.getByLabelText('Cost on line 1'), { target: { value: '3100' } });
    await attachBill('');
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(api.createGRN).toHaveBeenCalled());
    expect(api.createGRN.mock.calls[0][0]).toMatchObject({ vendor_id: 'V-NEW' });
    expect(api.createGRN.mock.calls[0][0].items[0].unit_price).toBe(3100);
  });

  it('a delivery challan: the challan number, no photo, the supplier; invoice later', async () => {
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: /Delivery challan, invoice comes later/ }));
    fireEvent.change(await screen.findByLabelText(/Supplier/), { target: { value: 'V-77' } });
    await typeOneLine();
    expect(document.querySelector('input[type="file"]')).toBeNull();
    expect(screen.getByTestId('receive-problem')).toHaveTextContent(/challan number/);
    fireEvent.change(screen.getByLabelText('Challan no. *'), { target: { value: 'DC/26/10/4' } });
    fireEvent.change(screen.getByLabelText('Challan date'), { target: { value: '' } });
    expect(screen.getByTestId('receive-problem')).toHaveTextContent(/challan date/);
    fireEvent.change(screen.getByLabelText('Challan date'), { target: { value: '2026-10-20' } });
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(api.createGRN).toHaveBeenCalled());
    const body = api.createGRN.mock.calls[0][0];
    expect(body).toMatchObject({ grn_subtype: 'DELIVERY_CHALLAN', vendor_id: 'V-77', dc_number: 'DC/26/10/4' });
    expect(body.attachment_file_id).toBeUndefined();
  });
});

describe('an open order with a line already received in full', () => {
  it('lists that line at 0, and an extra piece of it is logged (held for the manager, never express)', async () => {
    api.getPurchaseOrders.mockResolvedValue({
      purchase_orders: [{ ...PO, status: 'PARTIALLY_RECEIVED', items: [
        PO.items[0], { ...PO.items[1], received_qty: 2 },
      ] }],
    });
    await openPO();
    expect(screen.getByText('Received in full')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Arrived on line 2'), { target: { value: '1' } });
    expect(screen.getByText('1 more than ordered: held for the manager')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Everything else arrived as ordered/ }));
    fireEvent.click(screen.getByLabelText(/I counted line 2/));
    await attachBill();
    expect(screen.getByText(/1 piece on lines beyond the order are held for the manager/)).toBeInTheDocument();
    expect(screen.getByText(/4 good pieces go into stock/)).toBeInTheDocument();
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(api.createGRN).toHaveBeenCalled());
    expect(cockpit.expressReceive).not.toHaveBeenCalled();
    expect(api.createGRN.mock.calls[0][0].items).toEqual([
      expect.objectContaining({ product_id: 'P1', received_qty: 4 }),
      expect.objectContaining({ product_id: 'P2', received_qty: 1, accepted_qty: 1 }),
    ]);
  });
});

describe('other orders', () => {
  it('extra goods on a fully received order: only what arrived is sent, and it is held for the manager', async () => {
    renderAt();
    fireEvent.change(await screen.findByLabelText(/Another order/), { target: { value: 'PO4' } });
    expect(await screen.findByText(/fully received\. Count only the extra goods/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Everything arrived as ordered/ })).toBeNull();
    fireEvent.change(screen.getByLabelText('Arrived on line 1'), { target: { value: '1' } });
    expect(screen.getByText('1 more than ordered: held for the manager')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/I counted line 1/));
    await attachBill();
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(api.createGRN).toHaveBeenCalled());
    expect(cockpit.expressReceive).not.toHaveBeenCalled();
    expect(api.createGRN.mock.calls[0][0].items).toEqual([expect.objectContaining({ received_qty: 1, accepted_qty: 1 })]);
  });

  it('a delivery on an order a cancel closed: every piece rejected, never a challan', async () => {
    renderAt();
    fireEvent.change(await screen.findByLabelText(/Another order/), { target: { value: 'PO5' } });
    await screen.findByText(/closed by a cancel\. Count what the vendor sent/);
    expect(screen.queryByLabelText(/Delivery challan/)).toBeNull();
    fireEvent.change(screen.getByLabelText('Arrived on line 1'), { target: { value: '2' } });
    // No replacement is coming on a cancelled order: credit, chosen for him.
    expect(screen.queryByLabelText('Replacement coming')).toBeNull();
    expect(screen.getByText(/the order line is cancelled, so no replacement is coming/)).toBeInTheDocument();
    // Only the line that arrived needs the tick; line 2 (nothing came) does not.
    fireEvent.click(screen.getByLabelText(/I counted line 1/));
    await attachBill();
    expect(receiveBtn()).toBeEnabled();
    fireEvent.click(receiveBtn());
    await waitFor(() => expect(api.createGRN).toHaveBeenCalled());
    expect(api.createGRN.mock.calls[0][0].items).toEqual([
      expect.objectContaining({ received_qty: 2, accepted_qty: 0, rejected_qty: 2, rejection_disposition: 'credit_note' }),
    ]);
  });
});

describe('a list that failed to load says so, with Retry', () => {
  it('the deliveries inbox', async () => {
    api.getPurchaseOrders.mockRejectedValueOnce(new Error('down'));
    renderAt();
    expect(await screen.findByText(/Could not load the deliveries/)).toBeInTheDocument();
    expect(screen.queryByText('No deliveries waiting')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('PO-0412')).toBeInTheDocument();
  });

  it('the receipts still waiting', async () => {
    api.getGRNs.mockImplementation(async (p: { status?: string }) => {
      if (p?.status) throw new Error('down');
      return { grns: [] };
    });
    renderAt();
    expect(await screen.findByText(/Could not check for receipts still waiting/)).toBeInTheDocument();
  });

  it('the receipt history', async () => {
    api.getGRNs.mockImplementation(async (p: { status?: string }) => {
      if (!p?.status) throw new Error('down');
      return { grns: [] };
    });
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: /Receipt history/ }));
    expect(await screen.findByText('Could not load the receipt history.')).toBeInTheDocument();
    expect(screen.queryByText('No goods receipts yet.')).toBeNull();
  });
});

describe('receipts still waiting', () => {
  it('Add to stock on a still-held receipt warns (no green success) and opens its labels', async () => {
    waiting.PARTIALLY_ACCEPTED = [
      { grn_id: 'G-H', grn_number: 'GRN-0391', status: 'PARTIALLY_ACCEPTED', items: [{}], unresolved_lines: [{ reason: 'incomplete_catalog' }] },
    ];
    api.acceptGRN.mockResolvedValue({ grn_status: 'PARTIALLY_ACCEPTED', units_added: 0, unresolved_lines: [{ reason: 'incomplete_catalog' }] });
    renderAt();
    const panel = await screen.findByTestId('receipts-waiting');
    expect(within(panel).queryByRole('button', { name: /Not received/ })).toBeNull();
    fireEvent.click(within(panel).getByRole('button', { name: /Add to stock/ }));
    await waitFor(() => expect(toast.warning).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
    expect(await screen.findByText('labels for G-H')).toBeInTheDocument();
  });

  it('a receipt held beyond its order offers Not received; any can be voided', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    waiting.PARTIALLY_ACCEPTED = [
      { grn_id: 'G-O', grn_number: 'GRN-0389', status: 'PARTIALLY_ACCEPTED', items: [{}], unresolved_lines: [{ reason: 'over_order' }] },
    ];
    api.dropOverOrder.mockResolvedValue({ dropped_units: 1, grn_status: 'ACCEPTED' });
    renderAt();
    const panel = await screen.findByTestId('receipts-waiting');
    expect(within(panel).getByText(/beyond the order, for the store manager/)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole('button', { name: /Not received/ }));
    await waitFor(() => expect(api.dropOverOrder).toHaveBeenCalledWith('G-O'));
    fireEvent.click(within(panel).getByRole('button', { name: /Void/ }));
    await waitFor(() => expect(api.voidGRN).toHaveBeenCalledWith('G-O'));
  });
});

describe('receipt history', () => {
  it('names a walk-in purchase "Bought without PO · dealer" and lists the rejected pieces', async () => {
    history = [
      { grn_id: 'G1', grn_number: 'GRN-0009', grn_subtype: 'NO_PO', vendor_name: 'Sharma Optical', status: 'ACCEPTED',
        created_at: '2026-10-14T11:00:00', items: [{ product_id: 'P1', received_qty: 2, accepted_qty: 1, rejected_qty: 1, rejection_reason: 'Broken hinge' }] },
    ];
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: /Receipt history/ }));
    expect(await screen.findByText('Bought without PO · Sharma Optical')).toBeInTheDocument();
    expect(screen.getByText(/1 rejected — Broken hinge/)).toBeInTheDocument();
    expect(screen.getByTitle('Print GRN')).toBeInTheDocument();
    expect(screen.getByLabelText('Only with damaged or rejected pieces')).toBeInTheDocument();
  });

  it('the GRN print shows what the order asked for and the supplier with its GSTIN', async () => {
    history = [
      { grn_id: 'G2', grn_number: 'GRN-0010', po_number: 'PO-0412', vendor_id: 'V1', vendor_name: 'Safilo India', status: 'ACCEPTED',
        created_at: '2026-10-14T11:00:00', items: [{ product_id: 'P1', ordered_qty: 4, received_qty: 3, accepted_qty: 3, rejected_qty: 0 }] },
    ];
    api.getVendor.mockResolvedValue({ vendor_id: 'V1', gstin: '27AAACS1234A1Z5', address: 'Andheri', city: 'Mumbai' });
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: /Receipt history/ }));
    fireEvent.click(await screen.findByTitle('Print GRN'));
    await waitFor(() => expect(printed.grn?.vendor_gstin).toBe('27AAACS1234A1Z5'));
    expect(printed.grn).toMatchObject({ vendor_name: 'Safilo India', vendor_address: 'Andheri, Mumbai' });
    expect((printed.grn?.items as Array<Record<string, number>>)[0]).toMatchObject({ ordered_qty: 4, received_qty: 3, variance: -1 });
  });
});
