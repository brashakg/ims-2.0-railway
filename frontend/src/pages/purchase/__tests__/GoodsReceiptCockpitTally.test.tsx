// ============================================================================
// IMS 2.0 - Goods-Receipt Cockpit: ruling 14 (THE TALLY) on the two-step form
// ============================================================================
// The express panel declares the tally once at the header ("everything arrived
// exactly as ordered, clean"), and the server stamps tallied=true for it. The
// two-step create+accept form is the automatic fallback for exactly the
// deliveries that were NOT clean -- short, over, or partly rejected -- so it is
// the one screen that has to tick line by line.
//
// This pins the wall the server puts up (422 LINES_NOT_TALLIED) against a
// screen that can actually satisfy it:
//   - submitting with a line unticked never reaches the server
//   - once every line is ticked, the POST carries tallied on every item
// Without the tick, every non-clean delivery in the shop is unreceivable.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock('../../../services/api/grnCockpit', () => ({
  grnCockpitApi: {
    listVendors: vi.fn(),
    getCockpit: vi.fn(),
    uploadDoc: vi.fn(),
    createGRN: vi.fn(),
    expressReceive: vi.fn(),
  },
}));

vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: {
    getGRNs: vi.fn(),
    getPurchaseOrders: vi.fn(),
    acceptGRN: vi.fn(),
    voidGRN: vi.fn(),
  },
}));

vi.mock('../../../services/api/labels', () => ({
  default: { getProductLabel: vi.fn() },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => toastMock,
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'S1', roles: ['STORE_MANAGER'] } }),
}));

vi.mock('../../../hooks/useIsOnlineStore', () => ({
  useIsOnlineStore: () => false,
}));

// The express panel has its own test file; here it is only the door into the
// two-step form. Stub it with a button that hands over the same prefill a real
// non-clean delivery would (2 lines, one of them partly rejected).
vi.mock('../ExpressReceivePanel', () => ({
  ExpressReceivePanel: ({
    onFallbackToTwoStep,
  }: {
    onFallbackToTwoStep: (p: unknown) => void;
  }) => (
    <button
      type="button"
      onClick={() =>
        onFallbackToTwoStep({
          reason: 'edited',
          vendorInvoiceNo: 'INV-77',
          upload: { file_id: 'f1', filename: 'inv.pdf', mime: 'application/pdf' },
          lines: [
            {
              product_id: 'prod-a',
              product_name: 'Ray-Ban RX5154',
              sku: 'RB5154',
              ordered_qty: 10,
              received_qty: 8,
              accepted_qty: 8,
              rejected_qty: 0,
              batch_code: '',
              expiry_date: '',
              unit_price: 3200,
            },
            {
              product_id: 'prod-b',
              product_name: 'Crizal Rock 1.5',
              sku: 'CRZ-15',
              ordered_qty: 20,
              received_qty: 20,
              accepted_qty: 18,
              rejected_qty: 2,
              batch_code: '',
              expiry_date: '',
              unit_price: 900,
            },
          ],
        })
      }
    >
      fall back to two-step
    </button>
  ),
}));

import { GoodsReceiptCockpit } from '../GoodsReceiptCockpit';
import { grnCockpitApi } from '../../../services/api/grnCockpit';
import { vendorsApi } from '../../../services/api/inventory';

const listVendorsMock = grnCockpitApi.listVendors as unknown as ReturnType<typeof vi.fn>;
const getCockpitMock = grnCockpitApi.getCockpit as unknown as ReturnType<typeof vi.fn>;
const createGRNMock = grnCockpitApi.createGRN as unknown as ReturnType<typeof vi.fn>;
const getGRNsMock = vendorsApi.getGRNs as unknown as ReturnType<typeof vi.fn>;
const getPOsMock = vendorsApi.getPurchaseOrders as unknown as ReturnType<typeof vi.fn>;
const acceptGRNMock = vendorsApi.acceptGRN as unknown as ReturnType<typeof vi.fn>;

const OPEN_PO = {
  po_id: 'po-1',
  po_number: 'PO-BV-26-0007',
  status: 'SENT',
  expected_date: '2026-07-06',
  lines: [],
};

/** Render the cockpit deep-linked at a PO, then take the express fallback so
 *  the two-step receive form is on screen with its lines untallied. */
async function openTwoStepForm() {
  render(
    <MemoryRouter initialEntries={['/purchase/receive?vendor_id=V1&po_id=po-1']}>
      <GoodsReceiptCockpit />
    </MemoryRouter>,
  );
  // Generous timeout: the cockpit chains vendors -> cockpit payload -> deep
  // link before the express door exists, and this file runs alongside others.
  const door = await screen.findByRole(
    'button',
    { name: /fall back to two-step/i },
    { timeout: 5000 },
  );
  fireEvent.click(door);
  await screen.findByRole(
    'button',
    { name: /create goods receipt/i },
    { timeout: 5000 },
  );
}

describe('GoodsReceiptCockpit two-step receive - ruling 14 (the tally)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listVendorsMock.mockResolvedValue([{ vendor_id: 'V1', display_name: 'Acme Optics' }]);
    getCockpitMock.mockResolvedValue({
      vendor_id: 'V1',
      open_pos: [OPEN_PO],
      pending_not_received: [],
      pending_cataloged: [],
    });
    getGRNsMock.mockResolvedValue({ grns: [] });
    getPOsMock.mockResolvedValue({ purchase_orders: [] });
    createGRNMock.mockResolvedValue({
      grn_id: 'G1',
      grn_number: 'GRN-1',
      total_received: 28,
    });
    acceptGRNMock.mockResolvedValue({ units_added: 26, po_status: 'RECEIVED' });
  });

  it('refuses to post a receipt while a line is still unticked', async () => {
    await openTwoStepForm();

    fireEvent.click(screen.getByRole('button', { name: /create goods receipt/i }));

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('Tick every line'),
      ),
    );
    expect(createGRNMock).not.toHaveBeenCalled();
  });

  it('ticking a line is not enough - EVERY line has to be counted', async () => {
    await openTwoStepForm();

    fireEvent.click(screen.getByRole('checkbox', { name: /tally line 1/i }));
    fireEvent.click(screen.getByRole('button', { name: /create goods receipt/i }));

    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(createGRNMock).not.toHaveBeenCalled();
  });

  it('sends the tally tick for every line once they are all counted', async () => {
    await openTwoStepForm();

    fireEvent.click(screen.getByRole('checkbox', { name: /tally line 1/i }));
    fireEvent.click(screen.getByRole('checkbox', { name: /tally line 2/i }));
    fireEvent.click(screen.getByRole('button', { name: /create goods receipt/i }));

    await waitFor(() => expect(createGRNMock).toHaveBeenCalledTimes(1));
    const payload = createGRNMock.mock.calls[0][0];
    expect(payload.items).toHaveLength(2);
    // The server refuses the whole receipt unless every item carries it.
    for (const item of payload.items) {
      expect(item.tallied).toBe(true);
    }
    // ...and the tick must not have quietly rewritten what arrived.
    expect(payload.items[0]).toMatchObject({ product_id: 'prod-a', received_qty: 8, rejected_qty: 0 });
    expect(payload.items[1]).toMatchObject({ product_id: 'prod-b', received_qty: 20, rejected_qty: 2 });
  });
});
