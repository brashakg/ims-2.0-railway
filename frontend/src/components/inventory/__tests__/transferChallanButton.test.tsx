// ============================================================================
// The Delivery Challan button follows the server's answer (owner 2026-10-08)
// ============================================================================
// A valued challan (a move between two GST registrations) is printed by
// managers and accounts only. Every transfer reply carries the print route's
// own answer, can_print_challan; the details modal offers the button exactly
// when it says yes, so nobody is shown a button that answers 403.

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const getTransfers = vi.fn();

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', roles: ['STORE_MANAGER'], storeIds: ['ST-DHN-1'], activeStoreId: 'ST-DHN-1' },
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../../services/api', () => ({
  inventoryApi: {
    getTransfers: (...a: unknown[]) => getTransfers(...a),
    getMissingPieces: () => Promise.resolve({ pieces: [] }),
  },
  productApi: { searchProducts: () => Promise.resolve([]) },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: { getStores: () => Promise.resolve([]) },
}));
const openTransferChallan = vi.fn();
const openTransferInvoice = vi.fn();
vi.mock('../../../services/api/printDocuments', () => ({
  printDocumentsApi: {
    openTransferChallan: (...a: unknown[]) => openTransferChallan(...a),
    openTransferInvoice: (...a: unknown[]) => openTransferInvoice(...a),
  },
}));

import { StockTransferManagement } from '../StockTransferManagement';

const transfer = (canPrintChallan: boolean, taxInvoiceNumber?: string) => ({
  id: 'trf_1',
  transfer_number: 'TRF-202610-0001',
  from_location_id: 'ST-DHN-1',
  from_location_name: 'Hirapur Dhanbad',
  to_location_id: 'ST-BOK-1',
  to_location_name: 'Sector 4 Bokaro',
  status: 'in_transit',
  items: [],
  created_by: 'u1',
  created_at: '2026-10-13T05:00:00',
  total_items: 2,
  can_print_challan: canPrintChallan,
  tax_invoice_number: taxInvoiceNumber,
});

async function openDetails(canPrintChallan: boolean, taxInvoiceNumber?: string) {
  getTransfers.mockResolvedValue({ transfers: [transfer(canPrintChallan, taxInvoiceNumber)] });
  const view = render(<StockTransferManagement />);
  fireEvent.click(await screen.findByText('#TRF-202610-0001'));
  expect(await screen.findByText('Transfer #TRF-202610-0001')).toBeInTheDocument();
  return view;
}

describe('the transfer details modal', () => {
  it('offers the Delivery Challan only when the server says this user prints it', async () => {
    const refused = await openDetails(false);
    expect(screen.queryByRole('button', { name: /Delivery Challan/ })).toBeNull();
    refused.unmount();

    await openDetails(true);
    expect(screen.getByRole('button', { name: /Delivery Challan/ })).toBeInTheDocument();
  });

  it('offers the Tax Invoice instead for a move between two GST registrations', async () => {
    await openDetails(true, 'DHN01/T2627/0001');
    expect(screen.queryByRole('button', { name: /Delivery Challan/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Tax Invoice/ }));
    expect(openTransferInvoice).toHaveBeenCalledWith('trf_1');
    expect(openTransferChallan).not.toHaveBeenCalled();
  });
});
