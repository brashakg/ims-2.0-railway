// ============================================================================
// IMS 2.0 - Goods Receipt Note: "Bought without PO" (audit C7, ruling D14)
// ============================================================================
// A cash buy from a local dealer can only be logged today by calling it a
// "Delivery Challan": no price per item, and the History tab then reads
// "Against Unknown PO". Ruling D14: a plain "Bought without PO" receipt with
// the dealer, each item's cost and the bill photo, labelled as such in the list.

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

const products = vi.hoisted(() => ({ getProducts: vi.fn() }));
const cockpit = vi.hoisted(() => ({ uploadDoc: vi.fn() }));

vi.mock('../../../services/api', () => ({ vendorsApi: api }));
vi.mock('../../../services/api/products', () => ({ productApi: products }));
vi.mock('../../../services/api/grnCockpit', () => ({ grnCockpitApi: cockpit }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1', roles: ['STORE_MANAGER'] } }),
}));
vi.mock('../../../components/print/GRNPrint', () => ({ GRNPrint: () => null }));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));
// The labels dialog (#1164) has its own suite.
vi.mock('../../../components/labels/UnitLabelsModal', () => ({ UnitLabelsModal: () => null }));

import { GoodsReceiptNote } from '../GoodsReceiptNote';

beforeEach(() => {
  vi.clearAllMocks();
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
  cockpit.uploadDoc.mockResolvedValue({
    file_id: 'F-BILL-1',
    filename: 'cash-memo.png',
    mime: 'image/png',
  });
});

describe('C7 / D14 - bought without a PO', () => {
  it('History names a walk-in purchase "Bought without PO", not "Unknown PO"', async () => {
    api.getGRNs.mockResolvedValue({
      grns: [
        {
          grn_id: 'G-NOPO-1',
          grn_number: 'RCPT/S1/2026-27/0009',
          grn_subtype: 'NO_PO',
          po_id: null,
          po_number: null,
          dealer_name: 'Sharma Optical, Bank More',
          vendor_name: 'Sharma Optical, Bank More',
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

    expect(await screen.findByText(/Bought without PO/i)).toBeInTheDocument();
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
      fireEvent.change(billDate(), { target: { value: '2026-09-14' } });
      // Out of the mode and back in (as after a posted receipt): a fresh bill.
      fireEvent.click(mode());
      fireEvent.click(mode());
      expect(billDate().value).toBe('2026-10-01');
    } finally {
      vi.useRealTimers();
    }
  });
});
