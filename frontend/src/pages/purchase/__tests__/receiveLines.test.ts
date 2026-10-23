// ============================================================================
// IMS 2.0 - The one receiving screen's rules (receiveLines.ts)
// ============================================================================
// Every receipt kind runs through these: what is due, what each line tells
// the shop floor, what the receipt sends, which door it takes, what stops it.

import { describe, it, expect } from 'vitest';
import {
  expiryWarning,
  goesExpress,
  grnItems,
  lineNotes,
  linesFromPO,
  matchScan,
  newLine,
  productFacts,
  receiveProblem,
  tallyAll,
  toCandidate,
  type ReceiveHeader,
  type ReceiveLine,
} from '../receiveLines';

const order = (over: Record<string, unknown> = {}) =>
  toCandidate({
    po_id: 'PO-1',
    po_number: 'PO-0412',
    vendor_id: 'V1',
    vendor_name: 'Safilo India',
    status: 'PARTIALLY_RECEIVED',
    delivery_store_id: 'S1',
    items: [
      { po_item_id: 'i1', product_id: 'P1', product_name: 'Carrera 1234', quantity: 4, received_qty: 0 },
      { po_item_id: 'i2', product_id: 'P2', product_name: 'Polaroid 6014', quantity: 2, received_qty: 2 },
      { po_item_id: 'i3', product_id: 'P3', product_name: 'Polaroid 2088', quantity: 3 },
    ],
    received_qty_by_product: { P3: 1 },
    ...over,
  });

const header = (over: Partial<ReceiveHeader> = {}): ReceiveHeader => ({
  kind: 'STANDARD',
  hasOrder: true,
  vendorId: '',
  billNo: 'SAF-8841',
  dcNumber: '',
  billFileId: 'F1',
  ...over,
});

const counted = (ls: ReceiveLine[]) => ls.map((l) => ({ ...l, counted: true }));

describe('lines from an order', () => {
  it('an open order lists only what is still due, prefilled but NOT ticked', () => {
    const ls = linesFromPO(order());
    expect(ls.map((l) => [l.product_id, l.due, l.arrived, l.counted])).toEqual([
      ['P1', 4, 4, false],
      // P2 is fully received; P3 falls back to the header's received count.
      ['P3', 2, 2, false],
    ]);
  });

  it('a fully received order lists every line at 0 (only extra goods count); its cancelled lines reject', () => {
    const ls = linesFromPO(
      order({
        status: 'RECEIVED',
        extra_receipt_allowed: true,
        items: [
          { product_id: 'P1', quantity: 2, received_qty: 2 },
          { product_id: 'P9', quantity: 0, cancelled_qty: 1, line_status: 'CANCELLED' },
        ],
      }),
    );
    expect(ls.map((l) => [l.product_id, l.due, l.arrived, l.rejectOnly])).toEqual([
      ['P1', 0, 0, false],
      ['P9', 0, 0, true],
    ]);
  });

  it('an order a cancel closed: every line rejects whatever arrives', () => {
    const ls = linesFromPO(order({ status: 'CANCELLED', closed_by_cancel: true }));
    expect(ls.every((l) => l.rejectOnly && l.due === 0)).toBe(true);
    const items = grnItems('STANDARD', counted(ls).map((l) => ({ ...l, arrived: 1, damageChoice: 'credit_note' as const })), false);
    expect(items[0]).toMatchObject({ received_qty: 1, accepted_qty: 0, rejected_qty: 1, rejection_reason: 'Cancelled on the order' });
  });
});

describe('"Everything arrived as ordered"', () => {
  it('fills every line with what is due, clears damage and ticks it', () => {
    const ls = linesFromPO(order()).map((l) => ({ ...l, arrived: 1, damaged: 1 }));
    expect(tallyAll(ls).map((l) => [l.arrived, l.damaged, l.counted])).toEqual([
      [4, 0, true],
      [2, 0, true],
    ]);
  });
});

describe('what each row says', () => {
  const base = linesFromPO(order())[0];
  it.each([
    [{ arrived: 4 }, ['All good']],
    [{ arrived: 3 }, ['1 short, stays on the order']],
    [{ arrived: 5 }, ['1 more than ordered: held for the manager']],
    [{ arrived: 4, damaged: 1 }, ['1 damaged: to quarantine']],
    [{ arrived: 4, heldForCatalogue: true }, ['Held until catalogued']],
  ])('%o -> %o', (patch, texts) => {
    expect(lineNotes({ ...base, ...patch }).map((n) => n.text)).toEqual(texts);
  });
});

describe('contact-lens expiry is a warning only', () => {
  it('expired, short-dated (inside six months), fine', () => {
    expect(expiryWarning('2026-10-01', '2026-10-23')).toBe('expired');
    expect(expiryWarning('2027-03-01', '2026-10-23')).toBe('short');
    expect(expiryWarning('2028-03-01', '2026-10-23')).toBeNull();
    expect(expiryWarning('', '2026-10-23')).toBeNull();
  });
});

describe('the scan box', () => {
  it('matches a barcode, GTIN or SKU to its line, case-insensitively', () => {
    const ls = linesFromPO(order()).map((l, i) => ({ ...l, ...(i === 1 ? productFacts({ gtin: '8056597', sku: 'PLD-2088' }) : {}) }));
    expect(matchScan(ls, '8056597')).toBe(1);
    expect(matchScan(ls, 'pld-2088')).toBe(1);
    expect(matchScan(ls, 'p1')).toBe(0);
    expect(matchScan(ls, 'nope')).toBe(-1);
  });

  it('the catalogue says which lines are contact lenses and which are drafts', () => {
    expect(productFacts({ category: 'CONTACT_LENS', ordered_draft: true })).toMatchObject({ contactLens: true, heldForCatalogue: true });
    expect(productFacts({ category: 'FRAME' })).toMatchObject({ contactLens: false, heldForCatalogue: false });
  });
});

describe('what a receipt sends', () => {
  it('only lines that arrived; the damaged split, reason, choice, shelf, batch, tick', () => {
    const [a, b] = counted(linesFromPO(order()));
    const items = grnItems(
      'STANDARD',
      [
        { ...a, damaged: 1, damageReason: 'Scratched lens', damageChoice: 'replacement', shelf: ' c1 ', batch: 'L24', expiry: '2028-03-01' },
        { ...b, arrived: 0 },
      ],
      true,
    );
    expect(items).toEqual([
      {
        po_item_id: 'i1',
        product_id: 'P1',
        received_qty: 4,
        accepted_qty: 3,
        rejected_qty: 1,
        rejection_reason: 'Scratched lens',
        rejection_disposition: 'replacement',
        tallied: true,
        location_code: 'C1',
        batch_code: 'L24',
        expiry_date: '2028-03-01',
        unit_price: undefined,
      },
    ]);
  });

  it('a cost is sent only on a bought-without-PO receipt, and only from a cost role', () => {
    const line = { ...newLine({ product_id: 'P1', product_name: 'Frame' }, null), counted: true, unitPrice: 3100 };
    expect(grnItems('NO_PO', [line], true)[0].unit_price).toBe(3100);
    expect(grnItems('NO_PO', [line], false)[0].unit_price).toBeUndefined();
    expect(grnItems('DELIVERY_CHALLAN', [line], true)[0].unit_price).toBeUndefined();
  });
});

describe('which door a receipt takes', () => {
  const po = order();
  const clean = counted(linesFromPO(po));
  it('a clean box against an open order goes express', () => {
    expect(goesExpress('STANDARD', po, clean)).toBe(true);
    expect(goesExpress('STANDARD', po, [{ ...clean[0], arrived: 2 }])).toBe(true); // short is still clean
  });
  it.each([
    ['a damaged piece', [{ ...clean[0], damaged: 1 }]],
    ['a piece beyond the order', [{ ...clean[0], arrived: 5 }]],
    ['a line held for the catalogue', [{ ...clean[0], heldForCatalogue: true }]],
  ])('%s takes the two-call door', (_why, ls) => {
    expect(goesExpress('STANDARD', po, ls as ReceiveLine[])).toBe(false);
  });
  it('a challan, no order, an extra receipt, a cancel-closed order: two calls', () => {
    expect(goesExpress('DELIVERY_CHALLAN', po, clean)).toBe(false);
    expect(goesExpress('NO_PO', null, clean)).toBe(false);
    expect(goesExpress('STANDARD', order({ status: 'RECEIVED' }), clean)).toBe(false);
    expect(goesExpress('STANDARD', order({ closed_by_cancel: true }), clean)).toBe(false);
  });
});

describe('what stops the Receive button', () => {
  const ls = counted(linesFromPO(order()));
  it('ready', () => expect(receiveProblem(header(), ls, false)).toBeNull());
  it('a line not ticked "I counted this"', () => {
    expect(receiveProblem(header(), [{ ...ls[0], counted: false }, ls[1]], false)).toMatch(/1 still unticked/);
  });
  it('a 0 line still has to be ticked (a person said none came)', () => {
    expect(receiveProblem(header(), [{ ...ls[0], arrived: 0, counted: false }, ls[1]], false)).toMatch(/unticked/);
  });
  it('a damaged piece needs what is wrong, then replacement or credit', () => {
    const d = { ...ls[0], damaged: 1 };
    expect(receiveProblem(header(), [d], false)).toMatch(/what is wrong/);
    expect(receiveProblem(header(), [{ ...d, damageReason: 'Scratched' }], false)).toMatch(/replacement coming.*credit/);
  });
  it('no paper, no stock: the bill number and the bill photo', () => {
    expect(receiveProblem(header({ billNo: ' ' }), ls, false)).toMatch(/vendor bill number/);
    expect(receiveProblem(header({ billFileId: null }), ls, false)).toMatch(/no paper, no stock/);
    expect(receiveProblem(header({ kind: 'NO_PO', hasOrder: false, vendorId: 'V', billNo: '', billFileId: null }), ls, false)).toMatch(/no paper/);
  });
  it('a challan needs its number, not a photo; with no order, a supplier', () => {
    expect(receiveProblem(header({ kind: 'DELIVERY_CHALLAN', billFileId: null }), ls, false)).toMatch(/challan number/);
    expect(receiveProblem(header({ kind: 'DELIVERY_CHALLAN', billFileId: null, dcNumber: 'DC/1' }), ls, false)).toBeNull();
    expect(receiveProblem(header({ kind: 'DELIVERY_CHALLAN', hasOrder: false, dcNumber: 'DC/1' }), ls, false)).toMatch(/Pick the supplier/);
  });
  it('nothing arrived on a cancel-closed order says so', () => {
    const closed = counted(linesFromPO(order({ closed_by_cancel: true })));
    expect(receiveProblem(header(), closed, true)).toMatch(/each one is logged as rejected/);
  });
});
