// ============================================================================
// IMS 2.0 - Goods Receipt Note: "Bought without PO" (audit C7, ruling D14)
// ============================================================================
// A cash buy from a local dealer can only be logged today by calling it a
// "Delivery Challan": no price per item, and the History tab then reads
// "Against Unknown PO". Ruling D14: a plain "Bought without PO" receipt with
// the dealer, each item's cost and the bill photo, labelled as such in the list.
// Owner 2026-10-12: the dealer is a supplier -- picked, or quick-added here
// through the one vendor create door (name, phone, city / state, GSTIN if any).

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
  createVendor: vi.fn(),
}));

const products = vi.hoisted(() => ({ getProducts: vi.fn() }));
const cockpit = vi.hoisted(() => ({ uploadDoc: vi.fn() }));
const auth = vi.hoisted(() => ({ role: 'ADMIN' }));

vi.mock('../../../services/api', () => ({ vendorsApi: api }));
vi.mock('../../../services/api/products', () => ({ productApi: products }));
vi.mock('../../../services/api/grnCockpit', () => ({ grnCockpitApi: cockpit }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../hooks/usePOSQueries', () => ({
  useStores: () => ({ data: [{ store_id: 'S1', store_name: 'Receiving shop' }] }),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1', roles: [auth.role] } }),
}));
vi.mock('../../../components/print/GRNPrint', () => ({ GRNPrint: () => null }));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));
// The labels dialog (#1164) has its own suite.
vi.mock('../../../components/labels/UnitLabelsModal', () => ({ UnitLabelsModal: () => null }));
// The quick-add form reads the GST state names.
vi.mock('../../../services/api/entities', () => ({
  entitiesApi: {
    meta: vi.fn().mockResolvedValue({ state_codes: [{ code: '20', name: 'Jharkhand' }], entity_types: [] }),
  },
}));

import { GoodsReceiptNote } from '../GoodsReceiptNote';

beforeEach(() => {
  vi.clearAllMocks();
  auth.role = 'ADMIN';
  api.getGRNs.mockResolvedValue({ grns: [] });
  api.getPurchaseOrders.mockResolvedValue({ purchase_orders: [] });
  api.getVendors.mockResolvedValue({
    vendors: [{ vendor_id: 'V-77', trade_name: 'Frames Wala' }],
  });
  products.getProducts.mockResolvedValue({
    products: [{ product_id: 'P-FR1', name: 'Acme Aviator', sku: 'FR-0001' }],
  });
  api.createGRN.mockResolvedValue({ grn_id: 'NOPO-NEW-1' });
  api.acceptGRN.mockResolvedValue({ units_added: 2 });
  api.createVendor.mockResolvedValue({ vendor_id: 'V-SHARMA' });
  cockpit.uploadDoc.mockResolvedValue({
    file_id: 'F-BILL-1',
    filename: 'cash-memo.png',
    mime: 'image/png',
  });
});

describe('C7 / D14 - bought without a PO', () => {
  it('a manager receives quantities with dealer and photo while accounts enters prices later', async () => {
    auth.role = 'STORE_MANAGER';
    render(<GoodsReceiptNote />);
    await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText(/Bought without (a )?PO/i, { selector: 'input' }));
    await waitFor(() => expect(api.getVendors).toHaveBeenCalled());
    fireEvent.change(screen.getByDisplayValue('Select the vendor…'), { target: { value: 'V-77' } });
    fireEvent.change(screen.getByPlaceholderText(/Search a product to add/i), { target: { value: 'frame' } });
    fireEvent.click(await screen.findByText('Acme Aviator'));
    fireEvent.change(screen.getByLabelText('Quantity on line 1'), { target: { value: '2' } });
    expect(screen.queryByLabelText(/Cost on line 1/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/Tally line 1/));
    fireEvent.change(screen.getByLabelText(/Bill photo/i), {
      target: { files: [new File(['memo'], 'cash-memo.png', { type: 'image/png' })] },
    });
    await screen.findByText(/Attached: cash-memo.png/);
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() => expect(api.createGRN).toHaveBeenCalledTimes(1));
    const body = api.createGRN.mock.calls[0][0];
    expect(body).toMatchObject({ grn_subtype: 'NO_PO', vendor_id: 'V-77', attachment_file_id: 'F-BILL-1' });
    expect(body.items[0].unit_price).toBeUndefined();
    expect(body.items[0].received_qty).toBe(2);
  });

  it('History names a walk-in purchase "Bought without PO", not "Unknown PO"', async () => {
    api.getGRNs.mockResolvedValue({
      grns: [
        {
          grn_id: 'G-NOPO-1',
          grn_number: 'RCPT/S1/2026-27/0009',
          grn_subtype: 'NO_PO',
          po_id: null,
          po_number: null,
          vendor_id: 'V-SHARMA',
          vendor_name: 'Sharma Optical',
          status: 'ACCEPTED',
          created_at: '2026-09-14T11:00:00',
          items: [
            { product_id: 'P-FR1', received_qty: 2, accepted_qty: 2, rejected_qty: 0 },
          ],
        },
      ],
    });
    render(<GoodsReceiptNote />);
    await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole('button', { name: /History/ }));

    expect(await screen.findByText('Bought without PO · Sharma Optical')).toBeInTheDocument();
    expect(screen.queryByText(/Unknown PO/i)).not.toBeInTheDocument();
  });

  it('the receive form takes the dealer, each line cost and the bill photo, and posts a NO_PO receipt', async () => {
    render(<GoodsReceiptNote />);
    await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText(/Bought without (a )?PO/i, { selector: 'input' }));
    await waitFor(() => expect(api.getVendors).toHaveBeenCalled());
    fireEvent.change(screen.getByDisplayValue('Select the vendor…'), {
      target: { value: 'V-77' },
    });

    fireEvent.change(screen.getByPlaceholderText(/Search a product to add/i), {
      target: { value: 'frame' },
    });
    await waitFor(() => expect(products.getProducts).toHaveBeenCalled());
    fireEvent.click(await screen.findByText('Acme Aviator'));

    fireEvent.change(screen.getByLabelText('Quantity on line 1'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText(/Cost on line 1/i), { target: { value: '3100' } });
    fireEvent.click(screen.getByLabelText(/Tally line 1/));

    // No bill photo yet: refused on the client, nothing reaches the server.
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(expect.stringMatching(/photo of the dealer's bill/)),
    );
    expect(api.createGRN).not.toHaveBeenCalled();

    const photo = new File(['memo'], 'cash-memo.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText(/Bill photo/i), { target: { files: [photo] } });
    await waitFor(() => expect(screen.getByText(/Attached: cash-memo.png/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));

    await waitFor(() => expect(api.createGRN).toHaveBeenCalledTimes(1));
    const body = api.createGRN.mock.calls[0][0];
    expect(body.grn_subtype).toBe('NO_PO');
    expect(body.po_id).toBeUndefined();
    expect(body.vendor_id).toBe('V-77');
    expect(body.dc_number).toBeUndefined();
    expect(body.attachment_file_id).toBe('F-BILL-1');
    expect(body.items).toEqual([
      expect.objectContaining({ product_id: 'P-FR1', received_qty: 2, unit_price: 3100 }),
    ]);
  });

  it('the bill date starts at today in IST and never carries over to the next walk-in bill', async () => {
    // 01:30 IST on 1 October is still 30 September in UTC.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-09-30T20:00:00Z'));
    try {
      render(<GoodsReceiptNote />);
      await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());
      const mode = () => screen.getByLabelText(/Bought without (a )?PO/i, { selector: 'input' });
      const billDate = () => screen.getByLabelText(/Bill date/i) as HTMLInputElement;

      fireEvent.click(mode());
      expect(billDate().value).toBe('2026-10-01');
      // A bill is never dated after today: a slipped year would make the same
      // bill a "new" one to the same-bill rule (the server refuses it too).
      expect(billDate().max).toBe('2026-10-01');
      fireEvent.change(billDate(), { target: { value: '2026-09-14' } });
      // Out of the mode and back in (as after a posted receipt): a fresh bill.
      fireEvent.click(mode());
      fireEvent.click(mode());
      expect(billDate().value).toBe('2026-10-01');
    } finally {
      vi.useRealTimers();
    }
  });

  it('with "Bought without PO" ticked the card no longer asks for a purchase order', async () => {
    render(<GoodsReceiptNote />);
    await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());
    expect(screen.getByText('Select purchase order')).toBeInTheDocument();
    expect(screen.getByText('Pick the order being received')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/Bought without (a )?PO/i, { selector: 'input' }));
    expect(screen.queryByText('Select purchase order')).not.toBeInTheDocument();
    expect(screen.queryByText(/PO precedes GRN/)).not.toBeInTheDocument();
    expect(screen.queryByText('Pick the order being received')).not.toBeInTheDocument();
    // G2 (owner 2026-10-13): a registered dealer's walk-in bill claims credit.
    expect(screen.getByText('No PO · GST credit only from a registered dealer')).toBeInTheDocument();
    expect(screen.getByText('Pick the supplier, or add the dealer')).toBeInTheDocument();
    // The typed dealer name of round 4 is gone: the seller is a supplier.
    expect(screen.queryByPlaceholderText(/Sharma Optical/)).not.toBeInTheDocument();
  });
});

describe('a walk-in dealer is a supplier (owner 2026-10-12)', () => {
  async function typeOneLine() {
    fireEvent.change(screen.getByPlaceholderText(/Search a product to add/i), { target: { value: 'frame' } });
    fireEvent.click(await screen.findByText('Acme Aviator'));
    fireEvent.change(screen.getByLabelText('Quantity on line 1'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText(/Cost on line 1/i), { target: { value: '3100' } });
    fireEvent.click(screen.getByLabelText(/Tally line 1/));
    const photo = new File(['memo'], 'cash-memo.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText(/Bill photo/i), { target: { files: [photo] } });
    await screen.findByText(/Attached: cash-memo.png/);
  }

  it('the supplier list is every supplier on file, switched off or not (round 6)', async () => {
    // The quick-add refuses a name already on file among ALL suppliers; the
    // list was the server's first 50 active ones, so a later or switched-off
    // dealer could be neither picked nor added.
    render(<GoodsReceiptNote />);
    await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText(/Bought without (a )?PO/i, { selector: 'input' }));
    await waitFor(() => expect(api.getVendors).toHaveBeenCalled());
    expect(api.getVendors).toHaveBeenCalledWith({ limit: 1000 });
  });

  it('no supplier picked: Post is refused on the client and nothing reaches the server', async () => {
    render(<GoodsReceiptNote />);
    await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText(/Bought without (a )?PO/i, { selector: 'input' }));
    await waitFor(() => expect(api.getVendors).toHaveBeenCalled());
    await typeOneLine();
    expect(screen.getByRole('button', { name: /Post GRN/i })).toBeDisabled();
    expect(api.createGRN).not.toHaveBeenCalled();
  });

  it('a dealer not on the list is added in the small form, picked, and the receipt names it', async () => {
    render(<GoodsReceiptNote />);
    await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText(/Bought without (a )?PO/i, { selector: 'input' }));
    await waitFor(() => expect(api.getVendors).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /Add the dealer/i }));
    await screen.findByText('Add the dealer as a supplier');
    // The small form: name, phone, city, state / GSTIN -- no terms, code or email.
    expect(screen.queryByLabelText(/Supplier Code/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Email/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Payment Terms/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Dealer's name/i), { target: { value: 'Sharma Optical' } });
    fireEvent.change(screen.getByLabelText(/Phone/i), { target: { value: '9800000000' } });
    fireEvent.change(screen.getByLabelText(/City/i), { target: { value: 'Dhanbad' } });
    await screen.findByRole('option', { name: '20 Jharkhand' });
    fireEvent.change(screen.getByLabelText(/State \(unregistered vendor\)/i), { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Supplier/i }));

    await waitFor(() => expect(api.createVendor).toHaveBeenCalledTimes(1));
    const vendor = api.createVendor.mock.calls[0][0];
    expect(vendor).toMatchObject({
      legal_name: 'Sharma Optical',
      trade_name: 'Sharma Optical',
      gstin_status: 'UNREGISTERED',
      gstin: null,
      mobile: '9800000000',
      city: 'Dhanbad',
      state: '20',
      credit_days: 0, // a cash bill
    });
    await waitFor(() => expect(screen.queryByText('Add the dealer as a supplier')).not.toBeInTheDocument());
    expect((screen.getByLabelText(/Supplier \(the dealer\)/i) as HTMLSelectElement).value).toBe('V-SHARMA');

    await typeOneLine();
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));
    await waitFor(() => expect(api.createGRN).toHaveBeenCalledTimes(1));
    const body = api.createGRN.mock.calls[0][0];
    expect(body.grn_subtype).toBe('NO_PO');
    expect(body.vendor_id).toBe('V-SHARMA');
    expect(body).not.toHaveProperty('dealer_name');
  });
});

describe('a PO receipt carries the date on the supplier invoice', () => {
  it('sends the invoice date typed (IST today by default, never after today), not the UTC day it was keyed', async () => {
    // 01:30 IST on 1 April is still 31 March in UTC: the day keyed used to
    // decide the bill's financial year.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-03-31T20:00:00Z'));
    try {
      api.getPurchaseOrders.mockResolvedValue({
        purchase_orders: [
          {
            po_id: 'PO-B',
            po_number: 'PO/B',
            status: 'SENT',
            vendor_name: 'Frames Wala',
            items: [{ product_id: 'P-FR1', product_name: 'Acme Aviator', quantity: 2 }],
          },
        ],
      });
      render(<GoodsReceiptNote />);
      await waitFor(() => expect(screen.getByDisplayValue('Select a PO…')).toBeInTheDocument());
      fireEvent.change(screen.getByDisplayValue('Select a PO…'), { target: { value: 'PO-B' } });
      fireEvent.change(screen.getByPlaceholderText('e.g. JJ/24/04/2240'), { target: { value: 'INV-9' } });
      const date = screen.getByLabelText('Vendor invoice date') as HTMLInputElement;
      expect(date.value).toBe('2026-04-01');
      expect(date.max).toBe('2026-04-01');
      fireEvent.change(date, { target: { value: '2026-03-30' } });
      fireEvent.click(screen.getAllByLabelText(/Tally line 1/)[0]);
      // Main's F-S3: a PO receipt carries the vendor's bill too.
      const inv = new File(['inv'], 'inv-9.png', { type: 'image/png' });
      fireEvent.change(screen.getByLabelText(/Attach the vendor invoice or challan/i), { target: { files: [inv] } });
      await waitFor(() => expect(cockpit.uploadDoc).toHaveBeenCalled());
      await waitFor(() => expect(screen.getByText(/Attached:/)).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));

      await waitFor(() => expect(api.createGRN).toHaveBeenCalledTimes(1));
      const body = api.createGRN.mock.calls[0][0];
      expect(body.grn_subtype).toBe('STANDARD');
      expect(body.po_id).toBe('PO-B');
      expect(body.vendor_invoice_no).toBe('INV-9');
      expect(body.vendor_invoice_date).toBe('2026-03-30');
    } finally {
      vi.useRealTimers();
    }
  });
});
