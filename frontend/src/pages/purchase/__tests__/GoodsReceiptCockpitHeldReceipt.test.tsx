// ============================================================================
// IMS 2.0 - Receive Goods: a receipt held "waiting to be catalogued" (audit C1)
// ============================================================================
// Owner procurement audit 2026-09-28, blocker C1. Items the manager typed onto a
// PO through "Not in the catalogue?" arrive, and accept holds their lines
// (backend grn_accept: status PARTIALLY_ACCEPTED, 0 units minted, the lines in
// unresolved_lines). The "Receipts still waiting" row for that receipt used to:
//   - wears a green "On shelf" chip while 0 units are on the shelf
//     (PurchaseStatusChip GRN_MAP: PARTIALLY_ACCEPTED -> on_shelf), and
//   - on "Add to stock" toasts a green success for 0 units, although the
//     server answered that the lines are still held.
//
// Both are fixed: the chip at its source (PurchaseStatusChip GRN_MAP) and the
// toast through the one accept reading (grnAcceptToast). The C1 cases below
// guard them; the first case proves the row really rendered, so a broken
// harness cannot make them pass hollow.
// Backend half of C1/C2/C3: backend/tests/test_off_catalogue_items_release.py.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
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
  useAuth: () => ({ user: { activeStoreId: 'BV-DHN-02', roles: ['STORE_MANAGER'] } }),
}));

vi.mock('../../../hooks/useIsOnlineStore', () => ({
  useIsOnlineStore: () => false,
}));

import { GoodsReceiptCockpit } from '../GoodsReceiptCockpit';
import { grnCockpitApi } from '../../../services/api/grnCockpit';
import { vendorsApi } from '../../../services/api/inventory';

const listVendorsMock = grnCockpitApi.listVendors as unknown as ReturnType<typeof vi.fn>;
const getCockpitMock = grnCockpitApi.getCockpit as unknown as ReturnType<typeof vi.fn>;
const getGRNsMock = vendorsApi.getGRNs as unknown as ReturnType<typeof vi.fn>;
const getPOsMock = vendorsApi.getPurchaseOrders as unknown as ReturnType<typeof vi.fn>;
const acceptGRNMock = vendorsApi.acceptGRN as unknown as ReturnType<typeof vi.fn>;
const voidGRNMock = vendorsApi.voidGRN as unknown as ReturnType<typeof vi.fn>;

const HELD_NO = 'RCPT/BV-DHN-02/26-27/0022';

// The receipt exactly as GET /vendors/grn returned it in the audit
// (notes/critic/s2_release.out): two lines held, nothing minted.
const HELD_GRN = {
  grn_id: 'g-held',
  grn_number: HELD_NO,
  vendor_id: 'V-JOT',
  vendor_invoice_no: 'JOT/26-27/0701',
  created_at: '2026-09-28T11:00:00',
  status: 'PARTIALLY_ACCEPTED',
  units_added: 0,
  items: [
    { product_id: 'p-boss', received_qty: 2, accepted_qty: 2 },
    { product_id: 'p-carrera', received_qty: 2, accepted_qty: 2 },
  ],
  unresolved_lines: [
    { product_id: 'p-boss', accepted_qty: 2, reason: 'incomplete_catalog' },
    { product_id: 'p-carrera', accepted_qty: 2, reason: 'incomplete_catalog' },
  ],
};

async function heldRow(): Promise<HTMLElement> {
  render(
    <MemoryRouter initialEntries={['/purchase/receive?vendor_id=V-JOT']}>
      <GoodsReceiptCockpit />
    </MemoryRouter>,
  );
  const num = await screen.findByText(HELD_NO, undefined, { timeout: 5000 });
  // The row is the bordered box holding the number, the chip and the buttons.
  const row = num.closest('div.rounded-lg') as HTMLElement | null;
  if (!row) throw new Error('held receipt row not found');
  return row;
}

describe('Receive Goods - a receipt held for cataloguing (audit C1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listVendorsMock.mockResolvedValue([
      { vendor_id: 'V-JOT', display_name: 'Jharkhand Optical Traders' },
    ]);
    getCockpitMock.mockResolvedValue({
      vendor_id: 'V-JOT',
      open_pos: [],
      pending_not_received: [],
      pending_cataloged: [],
    });
    getGRNsMock.mockImplementation(async (params: { status?: string }) =>
      params?.status === 'PARTIALLY_ACCEPTED' ? { grns: [HELD_GRN] } : { grns: [] },
    );
    getPOsMock.mockResolvedValue({ purchase_orders: [] });
    // What POST /grn/{id}/accept answers while the lines are still held.
    acceptGRNMock.mockResolvedValue({
      message: 'GRN partially accepted -- some lines need cataloguing',
      grn_id: 'g-held',
      grn_status: 'PARTIALLY_ACCEPTED',
      units_added: 0,
      stock_ids: [],
      po_status: 'PARTIALLY_RECEIVED',
      unresolved_lines: HELD_GRN.unresolved_lines,
      needs_cataloguing: true,
    });
  });

  it('shows the held receipt in "Receipts still waiting" (harness precondition)', async () => {
    const row = await heldRow();
    expect(within(row).getByText(/2 line\(s\) waiting to be catalogued/i)).toBeTruthy();
    expect(within(row).getByRole('button', { name: /add to stock/i })).toBeTruthy();
  });

  // C1: a receipt with 0 units on the shelf must not say "On shelf".
  it('C1: the held receipt does not wear an "On shelf" chip for 0 units', async () => {
    const row = await heldRow();
    expect(within(row).queryByText(/on shelf/i)).toBeNull();
  });

  // C1: "Add to stock" on a still-held receipt must not claim success.
  it('C1: "Add to stock" on a still-held receipt does not toast a success', async () => {
    const row = await heldRow();
    fireEvent.click(within(row).getByRole('button', { name: /add to stock/i }));
    await waitFor(() => expect(acceptGRNMock).toHaveBeenCalledWith('g-held'));
    await waitFor(() =>
      expect(toastMock.success.mock.calls.length + toastMock.warning.mock.calls.length).toBeGreaterThan(0),
    );
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  // Panel round 2: a second receipt of the same box stays held for the store
  // manager, whose task says to void it here. The server proves it put
  // nothing on the shelf (and refuses otherwise).
  it('the store manager can void a held receipt (a second receipt of the same box)', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    voidGRNMock.mockResolvedValue({ grn_status: 'VOID' });
    const row = await heldRow();
    fireEvent.click(within(row).getByRole('button', { name: /void/i }));
    await waitFor(() => expect(voidGRNMock).toHaveBeenCalledWith('g-held'));
    confirmSpy.mockRestore();
  });
});
