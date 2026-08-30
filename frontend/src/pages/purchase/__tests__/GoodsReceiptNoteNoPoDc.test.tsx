// ============================================================================
// IMS 2.0 - Goods Receipt Note: the no-PO Delivery-Challan receive path
// ============================================================================
// The backend has accepted a PO-less DELIVERY_CHALLAN since F9; this screen
// hard-required a PO anyway, which turned "link the goods receipt" into a wall
// for every over-the-counter purchase. These tests pin the door OPEN:
//   - DC mode + vendor + typed product lines POSTs a DELIVERY_CHALLAN with
//     vendor_id and NO po_id
//   - an unticked line never reaches the server (the tally is the only
//     "a person counted this" record a PO-less receipt has)
//   - no vendor picked -> blocked client-side

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

async function openNoPoDc() {
  render(<GoodsReceiptNote />);
  await waitFor(() => expect(api.getGRNs).toHaveBeenCalled());
  fireEvent.click(
    screen.getByLabelText(/This is a Delivery Challan/i, { selector: 'input' }),
  );
  // vendor list loads once DC mode turns on
  await waitFor(() => expect(api.getVendors).toHaveBeenCalled());
}

async function addFrameLine() {
  const search = screen.getByPlaceholderText(/Search a product to add/i);
  fireEvent.change(search, { target: { value: 'frame' } });
  await waitFor(() => expect(products.getProducts).toHaveBeenCalled());
  fireEvent.click(await screen.findByText('Acme Aviator'));
}

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
  api.createGRN.mockResolvedValue({ grn_id: 'DC-NEW-1' });
  api.acceptGRN.mockResolvedValue({ units_added: 20 });
});

describe('no-PO Delivery-Challan receiving', () => {
  it('posts a DELIVERY_CHALLAN with vendor_id and no po_id once lines are tallied', async () => {
    await openNoPoDc();

    fireEvent.change(screen.getByPlaceholderText(/DC\/26\/05\/118/), {
      target: { value: 'DC/26/08/9' },
    });
    fireEvent.change(screen.getByDisplayValue('Select the vendor…'), {
      target: { value: 'V-77' },
    });
    await addFrameLine();

    // qty 20, then tick the tally
    fireEvent.change(screen.getByLabelText('Quantity on line 1'), {
      target: { value: '20' },
    });
    fireEvent.click(screen.getByLabelText(/Tally line 1/));

    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));

    await waitFor(() => expect(api.createGRN).toHaveBeenCalledTimes(1));
    const body = api.createGRN.mock.calls[0][0];
    expect(body.grn_subtype).toBe('DELIVERY_CHALLAN');
    expect(body.po_id).toBeUndefined();
    expect(body.vendor_id).toBe('V-77');
    expect(body.dc_number).toBe('DC/26/08/9');
    expect(body.items).toEqual([
      expect.objectContaining({
        product_id: 'P-FR1',
        received_qty: 20,
        accepted_qty: 20,
        tallied: true,
      }),
    ]);
    // and the receipt is posted (stock minted) right after
    await waitFor(() => expect(api.acceptGRN).toHaveBeenCalledWith('DC-NEW-1'));
  });

  it('an unticked line never reaches the server', async () => {
    await openNoPoDc();
    fireEvent.change(screen.getByPlaceholderText(/DC\/26\/05\/118/), {
      target: { value: 'DC/26/08/9' },
    });
    fireEvent.change(screen.getByDisplayValue('Select the vendor…'), {
      target: { value: 'V-77' },
    });
    await addFrameLine();

    fireEvent.click(screen.getByRole('button', { name: /Post GRN/i }));

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('Tick every line'),
      ),
    );
    expect(api.createGRN).not.toHaveBeenCalled();
  });

  it('no vendor picked keeps the post button walled off', async () => {
    await openNoPoDc();
    fireEvent.change(screen.getByPlaceholderText(/DC\/26\/05\/118/), {
      target: { value: 'DC/26/08/9' },
    });
    await addFrameLine();
    fireEvent.click(screen.getByLabelText(/Tally line 1/));

    const post = screen.getByRole('button', { name: /Post GRN/i });
    expect(post).toBeDisabled();
    fireEvent.click(post);
    expect(api.createGRN).not.toHaveBeenCalled();

    // picking the vendor is what opens the door
    fireEvent.change(screen.getByDisplayValue('Select the vendor…'), {
      target: { value: 'V-77' },
    });
    expect(screen.getByRole('button', { name: /Post GRN/i })).not.toBeDisabled();
  });
});
