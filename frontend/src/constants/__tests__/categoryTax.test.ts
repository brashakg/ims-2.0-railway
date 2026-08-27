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
// Three checks, all of which fail on the pre-fix table:
//   1. a picker code resolves to a REAL entry, not the unknown-category default;
//   2. the rate a category quotes is the rate its own HSN carries.
// (3) -- "and the server agrees" -- is NOT done here. Doing it here needs a
// transcription of the server's table, which is a third copy that drifts; it
// is done against the live resolver in
// backend/tests/test_category_tax_matches_server.py instead.
// (2) needs no second copy of the rates: an HSN entry names its own canonical
// category, so the table is asked to price the same goods twice, under two
// different names, and must answer the same both times.

import { describe, it, expect } from 'vitest';
import { HSN_CODES, getHSNByCategory, getGSTRateByCategory } from '../gst';
import { CATEGORIES } from '../../pages/catalog/productAddShared';

// NO hand-transcribed copy of the server's table lives here any more. One did,
// and it was the same bug class one file over: change a rate in gst_rates.py
// and the stored rate moves (it feeds _HSN_RATES -> resolve_gst_rate_strict)
// while the preview and the transcription both stay green. The cross-language
// check is done for real, against the running resolver, by
// backend/tests/test_category_tax_matches_server.py -- which parses THIS
// module's CATEGORY_TAX out of the .ts source, so there is nothing to keep in
// step. What is left here is TS-vs-TS: the picker cannot offer a category the
// table has not priced, and a category's rate is its own HSN's rate.

const PICKER = CATEGORIES.map((c) => [c.code, c.name] as const);

describe('the Add-Product picker cannot offer a category the tax table has not priced', () => {
  it('prices every code the picker offers', () => {
    const unpriced = CATEGORIES.filter((c) => getHSNByCategory(c.code) === null);
    expect(unpriced.map((c) => c.code)).toEqual([]);
  });

  it.each(PICKER)('%s (%s) resolves to a real HSN, not the unknown-category default', (code) => {
    const hsn = getHSNByCategory(code);
    expect(hsn).not.toBeNull();
    // 900490 IS the right answer for RG (readymade readers). It is also the
    // default a category NOBODY priced falls to -- which is how CCL, a contact
    // lens, came to be filed as a corrective spectacle. So anything else
    // landing there is the fall-through, not a choice.
    if (hsn!.code === '900490') {
      expect(code).toBe('RG');
    }
  });

  it.each(PICKER)('%s (%s) quotes the rate its own HSN carries', (code) => {
    const hsn = getHSNByCategory(code)!;
    expect(getGSTRateByCategory(code)).toBe(
      getGSTRateByCategory(HSN_CODES[hsn.code].category),
    );
  });

  it('prices colour contact lenses as contact lenses, at 5% on HSN 900130', () => {
    // The regression in one line: 900490 at 18% was the answer before.
    expect(getHSNByCategory('CCL')!.code).toBe('900130');
    expect(getGSTRateByCategory('CCL')).toBe(5);
  });
});
