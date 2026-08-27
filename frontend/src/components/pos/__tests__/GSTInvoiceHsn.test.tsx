// ============================================================================
// The tax invoice prints the HSN the PRODUCT is stored with
// ============================================================================
// The HSN column on a statutory tax invoice (Rule 46 CGST) files the supply --
// it decides the rate bucket the line lands in, here and in GSTR-1. It used to
// be DERIVED at render time from the line's category, through a hand-mirrored
// table in constants/gst.ts that had drifted: smartglasses were pointed at
// 900410, the SUNGLASSES code, while the backend's canonical table
// (services/gst_rates.GST_CATEGORY_TABLE) says 852580 -- and all 35 live
// smartglasses products carry 852580 on their own records. Every one of those
// sales printed a code the product record contradicted.
//
// So the cart line now carries the product's own hsn_code (posStore
// CartLineItem -> POSInvoice -> here) and the invoice PRINTS it. A category is
// consulted only for a line with no stored HSN, and when that cannot be
// resolved either, the cell is left EMPTY -- never filled with a guess.
//
// DECIDING FIXTURES: the stored HSN and the category's HSN are always set to
// DIFFERENT codes at the SAME rate (18%), so neither the rate nor a shared
// value can tell the tests which one the component read.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Each case here resets the module registry and renders a whole POS screen or a
// full statutory invoice, which can outrun vitest's 5s default when the entire
// suite runs in parallel on a slow machine. Slow, not flaky -- give it room.
vi.setConfig({ testTimeout: 20000 });

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock('../../../services/api/client', () => ({ default: { get: apiGet } }));

const SERVER = {
  by_hsn: { '900410': 18 },
  by_cat: { SUNGLASSES: 18 },
  category_hint: { SUNGLASS: 'SUNGLASSES' },
  hsn_by_category: { SMARTGLASSES: '852580', SUNGLASS: '900410', FRAME: '900311' },
  rate_by_category: { SMARTGLASSES: 18, SUNGLASS: 18, SUNGLASSES: 18, FRAME: 5 },
};

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

// The same two lines, but each carrying the HSN its product record holds. The
// stored codes are deliberately the WRONG ones for their categories (swapped),
// so a component that still derived from the category prints the other code.
const storedOrder = {
  ...order,
  items: [
    { ...order.items[0], hsnCode: '900410' },   // record says sunglasses
    { ...order.items[1], hsnCode: '852580' },   // record says smartglasses
  ],
};

/** A fresh module registry, so "the endpoint has answered" is a property of
 *  this test and not of whichever test ran before it. */
async function mount(ord: unknown, served: boolean) {
  vi.resetModules();
  apiGet.mockReset();
  const { GSTInvoice: Invoice } = await import('../GSTInvoice');
  if (served) {
    apiGet.mockResolvedValue({ data: SERVER });
    const { loadHsnRates } = await import('../../../constants/gstRuntime');
    await loadHsnRates();
  }
  render(<Invoice order={ord as never} store={store} />);
}

/** The row-level HSN cells, in row order. */
const printedHsn = () =>
  screen.getAllByText(/^(852580|900410)$/).map((n) => n.textContent).slice(0, 2);

describe('GST tax invoice HSN column', () => {
  beforeEach(() => vi.resetModules());

  it('prints the HSN the product record holds, not the one its category implies', async () => {
    await mount(storedOrder, true);
    // Line 1 is SMARTGLASSES stored as 900410; line 2 is SUNGLASS stored as
    // 852580. Deriving from the category yields exactly the opposite pair, so
    // the ORDER of these two cells is the whole assertion.
    expect(printedHsn()).toEqual(['900410', '852580']);
  });

  it('falls back to the server code for a line with no stored HSN', async () => {
    await mount(order, true);
    // A manually-added lens, or an older order, has no hsn_code to print.
    // 852580 is the server's code for smartglasses; the deleted frontend copy
    // said 900410 for BOTH lines, so its presence is what this asserts.
    expect(printedHsn()).toEqual(['852580', '900410']);
  });

  it('prints NO HSN rather than an invented one when neither is known', async () => {
    await mount(order, false);
    // Before GET /products/gst-rates answers there is no code to derive, and
    // these lines carry none. The column used to print a bare 4-digit '9004'
    // here -- for all 13 categories, on a statutory tax invoice.
    expect(screen.queryAllByText('9004')).toHaveLength(0);
    expect(screen.queryAllByText('852580')).toHaveLength(0);
    expect(screen.queryAllByText('900410')).toHaveLength(0);
    // The cell reads as "none", the way the HSN-wise summary already renders it.
    expect(screen.getAllByText('-').length).toBeGreaterThan(0);
  });

  it('still prints a stored HSN before the endpoint has answered', async () => {
    await mount(storedOrder, false);
    // Nothing about the printed code depends on the network any more; that is
    // the whole reason the cart carries it.
    expect(printedHsn()).toEqual(['900410', '852580']);
  });
});

describe('THE POS CONSTRAINT: a stored HSN files the supply, it cannot move the rate', () => {
  // A 5% frame whose product record carries 900410 -- the sunglasses code,
  // which IS a row in the owner-editable master at 18%. If the printed
  // percentage were derived from the code in the HSN column, this line would
  // bill 18% GST on a frame the customer is charged 5% for, and the change
  // that was only meant to correct a CODE would have moved a MONEY number.
  // Measured 2026-08-27: feeding the stored HSN back into the rate moves the
  // printed rate on 40 of 62 category spellings once the server has answered
  // -- FRAME, OPTICAL_LENS, CONTACT_LENS and READING_GLASSES 5% -> 18%, and
  // HEARING_AID 0% -> 18%.
  const rateTrap = {
    ...order,
    items: [{ id: 'i3', productName: 'Titan Frame', category: 'FRAME',
              quantity: 1, unitPrice: 4000, finalPrice: 4000, hsnCode: '900410' }],
  };

  /** [HSN, GST%] off the first line-item row of the printed table. */
  function firstRow(): Array<string | null | undefined> {
    const tr = Array.from(document.querySelectorAll('tr')).find((r) => {
      const c = r.querySelectorAll('td');
      return c.length >= 8 && c[0].textContent === '1';
    });
    const c = tr ? Array.from(tr.querySelectorAll('td')) : [];
    return [c[2]?.textContent, c[7]?.textContent];
  }

  beforeEach(() => vi.resetModules());

  it('prints the stored code and the CATEGORY rate once the server has answered', async () => {
    await mount(rateTrap, true);
    expect(firstRow()).toEqual(['900410', '5%']);
  });

  it('prints the stored code and the CATEGORY rate before the server answers', async () => {
    await mount(rateTrap, false);
    expect(firstRow()).toEqual(['900410', '5%']);
  });
});
