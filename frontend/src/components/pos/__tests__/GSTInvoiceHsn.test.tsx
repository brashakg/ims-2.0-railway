// ============================================================================
// The tax invoice prints the SERVER's HSN, not a copy of it
// ============================================================================
// POSInvoice builds the invoice rows out of the cart and does NOT carry the
// product's stored hsn_code, so the HSN column on a statutory tax invoice
// (Rule 46 CGST) is derived from the item's CATEGORY at render time. That
// derivation used to read a hand-mirrored table in constants/gst.ts which had
// drifted: smartglasses were pointed at 900410, the SUNGLASSES code, while the
// backend's canonical table (services/gst_rates.GST_CATEGORY_TABLE) says
// 852580. On 2026-08-27 that was 35 of the 68 live products.
//
// DECIDING FIXTURE: two lines, smartglasses and real sunglasses, which the OLD
// table gave the SAME code (900410) and the server gives DIFFERENT codes
// (852580 vs 900410). Both are 18%, so the RATE cannot tell the two apart --
// only the HSN can, which is the point of the test.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { GSTInvoice } from '../GSTInvoice';
import { loadHsnRates } from '../../../constants/gstRuntime';

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock('../../../services/api/client', () => ({ default: { get: apiGet } }));

const order = {
  id: 'o1',
  orderNumber: 'ORD-1',
  customerName: 'Walk-in',
  items: [
    { id: 'i1', productName: 'Ray-Ban Meta Wayfarer', category: 'SMARTGLASSES',
      quantity: 1, unitPrice: 29900, finalPrice: 29900 },
    { id: 'i2', productName: 'Ray-Ban Aviator', category: 'SUNGLASS',
      quantity: 1, unitPrice: 8990, finalPrice: 8990 },
  ],
  payments: [], subtotal: 38890, totalDiscount: 0, taxAmount: 0,
  grandTotal: 38890, amountPaid: 38890, balanceDue: 0,
  createdAt: '2026-08-27T00:00:00Z',
} as any;

const store = { storeId: 's1', storeCode: 'BV-BOK01', storeName: 'Better Vision',
                state: 'Jharkhand' } as any;

describe('GST tax invoice HSN column', () => {
  it('prints 852580 for smartglasses and 900410 for sunglasses', async () => {
    apiGet.mockResolvedValue({ data: {
      by_hsn: { '900410': 18 },
      by_cat: { SUNGLASSES: 18 },
      category_hint: { SUNGLASS: 'SUNGLASSES' },
      hsn_by_category: { SMARTGLASSES: '852580', SUNGLASS: '900410' },
    } });
    await loadHsnRates();

    render(<GSTInvoice order={order} store={store} />);

    // The row-level HSN cells. 852580 must appear; the old copy printed 900410
    // for BOTH lines, so its absence is what this asserts.
    expect(screen.getAllByText('852580').length).toBeGreaterThan(0);
    expect(screen.getAllByText('900410').length).toBeGreaterThan(0);
  });
});
