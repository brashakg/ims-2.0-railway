// ============================================================================
// IMS 2.0 - a SAVED purchase order shows the tax that is actually on it
// ============================================================================
// Two things the create modal got right and the saved order did not:
//   - the stored per-line rate is shown as stored. Defaulting a rate-less line
//     to 18% inflated its displayed total by 18% of money nobody charged --
//     and rate-less lines are exactly what the two automatic PO doors wrote.
//   - the CGST/SGST vs IGST split the SERVER decided is read back off the
//     order, instead of one opaque "Tax" line. Same money either way; which
//     one it is decides the GST return it lands in.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01' } }),
}));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));
vi.mock('../../../components/print/POPrint', () => ({ POPrint: () => null }));
vi.mock('../../../components/purchase/POLifecycleDrawer', () => ({
  POLifecycleDrawer: () => null,
}));

import { PurchaseOrderDetail } from '../PurchaseOrderDetail';
import { mapPOtoPurchaseOrder } from '../PurchaseManagementPage';
import type { PurchaseOrder } from '../purchaseTypes';

const SERVER_PO = {
  po_id: 'po-1',
  po_number: 'PO-001',
  vendor_id: 'v-1',
  vendor_name: 'Luxottica India',
  status: 'DRAFT',
  subtotal: 2000,
  tax_amount: 100,
  total_amount: 2100,
  gst_summary: { cgst: 50, sgst: 50, igst: 0, tax: 100 },
  interstate: false,
  items: [
    { product_id: 'p1', product_name: 'Ray-Ban', sku: 'RB1', quantity: 2, unit_price: 1000, tax_rate: 5 },
  ],
};

function show(po: PurchaseOrder) {
  return render(<PurchaseOrderDetail po={po} onClose={vi.fn()} onAction={vi.fn()} />);
}

describe('reading a saved purchase order back', () => {
  it('shows a line with no stored rate as 0%, not a flat 18%', () => {
    const mapped = mapPOtoPurchaseOrder({
      ...SERVER_PO,
      items: [{ product_id: 'p1', product_name: 'Lens', sku: 'L1', quantity: 2, unit_price: 1000 }],
    });
    expect(mapped.items[0].taxRate).toBe(0);
    // ... and the line total is the money actually on the order, not +18%.
    expect(mapped.items[0].total).toBe(2000);
  });

  it('keeps a stored rate exactly as stored', () => {
    const mapped = mapPOtoPurchaseOrder(SERVER_PO);
    expect(mapped.items[0].taxRate).toBe(5);
  });

  it('carries the split the server decided, rather than re-deriving it', () => {
    const mapped = mapPOtoPurchaseOrder(SERVER_PO);
    expect(mapped.gstSummary).toEqual({ cgst: 50, sgst: 50, igst: 0, tax: 100 });
    expect(mapped.interstate).toBe(false);
  });

  it('shows CGST + SGST on a within-state order', () => {
    show(mapPOtoPurchaseOrder(SERVER_PO));
    expect(screen.getByText('CGST')).toBeTruthy();
    expect(screen.getByText('SGST')).toBeTruthy();
    expect(screen.queryByText('IGST')).toBeNull();
  });

  it('shows one IGST charge on an order from another state', () => {
    show(
      mapPOtoPurchaseOrder({
        ...SERVER_PO,
        interstate: true,
        gst_summary: { cgst: 0, sgst: 0, igst: 100, tax: 100 },
      }),
    );
    expect(screen.getByText('IGST')).toBeTruthy();
    expect(screen.queryByText('CGST')).toBeNull();
  });

  it('falls back to a plain Tax line for an order raised before the split was stored', () => {
    const { gst_summary: _drop, interstate: _drop2, ...legacy } = SERVER_PO;
    show(mapPOtoPurchaseOrder(legacy));
    expect(screen.getByText('Tax')).toBeTruthy();
    expect(screen.queryByText('CGST')).toBeNull();
    expect(screen.queryByText('IGST')).toBeNull();
  });
});
