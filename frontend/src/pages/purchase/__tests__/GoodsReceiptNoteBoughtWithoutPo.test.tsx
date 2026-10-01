// ============================================================================
// IMS 2.0 - Goods Receipt Note: "Bought without PO" (audit C7, ruling D14)
// ============================================================================
// A cash buy from a local dealer can only be logged today by calling it a
// "Delivery Challan": no price per item, and the History tab then reads
// "Against Unknown PO". Ruling D14: a plain "Bought without PO" receipt with
// the dealer, each item's cost and the bill, labelled as such in the list.
//
// Both tests are `it.fails` (the vitest twin of pytest xfail strict=True) until
// the build lands: an unexpected pass fails the run, so the marker must come
// off in the commit that makes it pass.

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

vi.mock('../../../services/api', () => ({ vendorsApi: api }));
vi.mock('../../../services/api/products', () => ({ productApi: products }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1', roles: ['STORE_MANAGER'] } }),
}));
vi.mock('../../../components/print/GRNPrint', () => ({ GRNPrint: () => null }));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));

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
});

describe('C7 / D14 - bought without a PO', () => {
  it.fails('History names a walk-in purchase "Bought without PO", not "Unknown PO"', async () => {
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

  it.fails('the receive form takes the dealer and each line cost, and posts a NO_PO receipt', async () => {
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
    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));

    await waitFor(() => expect(api.createGRN).toHaveBeenCalledTimes(1));
    const body = api.createGRN.mock.calls[0][0];
    expect(body.grn_subtype).toBe('NO_PO');
    expect(body.po_id).toBeUndefined();
    expect(body.vendor_id).toBe('V-77');
    expect(body.dc_number).toBeUndefined();
    expect(body.items).toEqual([
      expect.objectContaining({ product_id: 'P-FR1', received_qty: 2, unit_price: 3100 }),
    ]);
  });
});
