// ============================================================================
// Exchange replacement lines: what the till is allowed to construct and send.
// ============================================================================
// The exchange difference feeds the CASH DRAWER, so every factor of it is
// server-checked. Two client-side defects made that policy unusable:
//
//   MF3 the picker rounded the catalog price (Math.round), so a catalog of
//       4241.50 went out as 4242 and the server's exact ceiling refused it --
//       the cashier picked from search, typed nothing, and was told the price
//       was above catalog, with no guessable recovery.
//   MF2 a free-text "Manual" button built a line with no product_id, which the
//       server can never price, so every payload it produced was refused. The
//       server policy was right; the BUTTON was the lie.
import { describe, it, expect } from 'vitest';
import {
  replacementUnitPrice,
  buildReplacementLine,
} from '../ReturnsPage';

describe('replacementUnitPrice', () => {
  it('sends the catalog price UNROUNDED so the exact ceiling accepts it', () => {
    expect(replacementUnitPrice({ offer_price: 4241.5 })).toBe(4241.5);
    expect(replacementUnitPrice({ offer_price: 6999.5 })).toBe(6999.5);
  });

  it('never rounds a paise value up past the catalog price', () => {
    for (const catalog of [4241.5, 6999.5, 1234.01, 99.99]) {
      expect(replacementUnitPrice({ offer_price: catalog })).toBeLessThanOrEqual(catalog);
    }
  });

  it('falls back through price and mrp', () => {
    expect(replacementUnitPrice({ price: 1500.25 })).toBe(1500.25);
    expect(replacementUnitPrice({ mrp: 2200.75 })).toBe(2200.75);
    expect(replacementUnitPrice({})).toBe(0);
  });
});

describe('buildReplacementLine', () => {
  it('builds a catalogued line carrying the product id and the exact price', () => {
    const line = buildReplacementLine({
      product_id: 'PRD-2', name: 'Frame', sku: 'RB-2', offer_price: 4241.5,
    });
    expect(line).not.toBeNull();
    expect(line!.productId).toBe('PRD-2');
    expect(line!.unitPrice).toBe(4241.5);
    expect(line!.quantity).toBe(1);
  });

  it('refuses to construct a line with no product id', () => {
    // This is the MF2 contract: an uncatalogued line is unpriceable server-side,
    // so the UI must not be able to produce one at all.
    expect(buildReplacementLine({ name: 'typed by hand', unitPrice: 999 })).toBeNull();
    expect(buildReplacementLine({})).toBeNull();
    expect(buildReplacementLine(null)).toBeNull();
  });

  it('accepts the alternate id fields the product search returns', () => {
    expect(buildReplacementLine({ id: 'X1', offer_price: 10 })!.productId).toBe('X1');
    expect(buildReplacementLine({ _id: 'X2', offer_price: 10 })!.productId).toBe('X2');
  });
});
