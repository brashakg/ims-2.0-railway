// ============================================================================
// IMS 2.0 - a SAVED purchase order shows the tax that is actually on it
// ============================================================================
// Two things the create modal got right and the saved order did not:
//   - the stored per-line rate is shown AS STORED, and a line with no stored
//     rate is not handed a house rate. A flat 18% default over-taxed the 5%
//     goods that are most of this catalogue; a flat 0% default (which an
//     earlier version of this branch shipped, under a comment claiming the 18
//     was "money nobody charged") was equally wrong in the other direction --
//     the orders that actually HAVE rate-less lines came from the two automatic
//     PO doors, which charged a header tax of exactly subtotal x one rate, so
//     0% printed a line total that did not add up to the header beside it.
//     What is shown now is the rate the order's own header arithmetic implies,
//     and the pair of tests below can tell those three answers apart.
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
  // The discriminating pair. A flat 18 default answers 18 to both; a flat 0
  // default answers 0 to both; only reading the order's own header tells them
  // apart. A fixture with one rate would prove nothing.
  const RATELESS_LINE = [
    { product_id: 'p1', product_name: 'Lens', sku: 'L1', quantity: 2, unit_price: 1000 },
  ];

  it('reads a rate-less line off an 18% order header', () => {
    // The real legacy population: the two automatic PO doors wrote every line
    // rate-less under tax_amount === subtotal * 0.18.
    const mapped = mapPOtoPurchaseOrder({
      ...SERVER_PO,
      subtotal: 2000,
      tax_amount: 360,
      total_amount: 2360,
      items: RATELESS_LINE,
    });
    expect(mapped.items[0].taxRate).toBe(18);
    // ... and the line reconciles with the header printed beside it.
    expect(mapped.items[0].total).toBe(2360);
    expect(mapped.total).toBe(2360);
  });

  it('reads a rate-less line off a 5% order header', () => {
    const mapped = mapPOtoPurchaseOrder({ ...SERVER_PO, items: RATELESS_LINE });
    expect(mapped.items[0].taxRate).toBe(5);
    expect(mapped.items[0].total).toBe(2100);
  });

  it('will not hand a blended rate to a rate-less line on a mixed order', () => {
    // No door writes this shape. If one ever does, a header-implied rate would
    // be nobody's actual rate, so the unknown line stays visibly at 0.
    const mapped = mapPOtoPurchaseOrder({
      ...SERVER_PO,
      items: [
        ...SERVER_PO.items,
        { product_id: 'p2', product_name: 'Frame', sku: 'F1', quantity: 1, unit_price: 500 },
      ],
    });
    expect(mapped.items[0].taxRate).toBe(5);
    expect(mapped.items[1].taxRate).toBe(0);
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
