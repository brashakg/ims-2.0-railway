// ============================================================================
// IMS 2.0 - every category the screen can offer must agree with the server
// ============================================================================
// The defect this pins: `CCL` (Colour Contact Lens) is one of the thirteen live
// codes in the Add-Product picker and was in NEITHER of the two category
// switches constants/gst.ts used to hold. It fell through both defaults, so the
// form showed HSN 900490 at 18% while the server -- which derives the stored
// rate from the picked HSN (services/gst_rates.resolve_gst_rate_strict) --
// stored 5% off that very code. A live, bill-paying category asserting one tax
// rate on screen and carrying another in the database.
//
// CATEGORY_TAX is the compile-time registry those checks run against. It is
// NOT a runtime pricer: rates on screen come from the server via gstRuntime,
// with getGSTRateByCategory as the offline last resort (pinned in both states
// by gstServerFed.test.ts). getHSNByCategory itself is deleted -- that same
// suite pins its absence -- so these checks read the registry directly.
//
// Three checks, all of which fail on the pre-fix table:
//   1. a picker code resolves to a REAL entry, not the unknown-category default;
//   2. the rate a category quotes is the rate its own HSN carries.
// (3) -- "and the server agrees" -- is NOT done here. Doing it here needs a
// transcription of the server's table, which is a third copy that drifts; it
// is done against the live resolver in
// backend/tests/test_category_tax_matches_server.py instead, which parses THIS
// module's CATEGORY_TAX out of the .ts source, so there is nothing to keep in
// step. What is left here is TS-vs-TS: the picker cannot offer a category the
// registry has not declared, and a category's rate is its own HSN's rate.

import { describe, it, expect } from 'vitest';
import { HSN_CODES, CATEGORY_TAX } from '../gst';
import { CATEGORIES } from '../../pages/catalog/productAddShared';

const TAX = CATEGORY_TAX as Record<string, { hsn: string; rate: number } | undefined>;

const PICKER = CATEGORIES.map((c) => [c.code, c.name] as const);

describe('the Add-Product picker cannot offer a category the tax registry has not declared', () => {
  it('declares every code the picker offers', () => {
    const undeclared = CATEGORIES.filter((c) => TAX[c.code] === undefined);
    expect(undeclared.map((c) => c.code)).toEqual([]);
  });

  it.each(PICKER)('%s (%s) resolves to a real HSN, not the unknown-category default', (code) => {
    const entry = TAX[code];
    expect(entry).toBeDefined();
    // 900490 IS the right answer for RG (readymade readers). It is also the
    // default a category NOBODY priced used to fall to -- which is how CCL, a
    // contact lens, came to be filed as a corrective spectacle. So anything
    // else landing there is the fall-through, not a choice.
    if (entry!.hsn === '900490') {
      expect(code).toBe('RG');
    }
  });

  it.each(PICKER)('%s (%s) quotes the rate its own HSN carries', (code) => {
    const entry = TAX[code]!;
    // An HSN entry names its own canonical category, so the registry is asked
    // to price the same goods twice, under two names, and must answer the same
    // both times. No second copy of the rates involved.
    const canonical = HSN_CODES[entry.hsn].category;
    expect(entry.rate).toBe(TAX[canonical]!.rate);
  });

  it('prices colour contact lenses as contact lenses, at 5% on HSN 900130', () => {
    // The regression in one line: 900490 at 18% was the answer before.
    expect(TAX['CCL']!.hsn).toBe('900130');
    expect(TAX['CCL']!.rate).toBe(5);
  });
});
