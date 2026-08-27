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
//   2. the rate a category quotes is the rate its own HSN carries;
//   3. the rate matches what the server's own category table says.
// (2) needs no second copy of the rates: an HSN entry names its own canonical
// category, so the table is asked to price the same goods twice, under two
// different names, and must answer the same both times.

import { describe, it, expect } from 'vitest';
import { HSN_CODES, getHSNByCategory, getGSTRateByCategory } from '../gst';
import { CATEGORIES } from '../../pages/catalog/productAddShared';

// The server's category defaults, read off
// backend/api/services/gst_rates.py::GST_CATEGORY_TABLE (the short-UI-code
// block). Written out because this is the other half of a cross-language
// contract: when these two part company, the screen and the database disagree
// about tax, which is the whole bug.
const SERVER_CATEGORY_DEFAULT: Record<string, { hsn: string; rate: number }> = {
  SG: { hsn: '900410', rate: 18 },
  FR: { hsn: '900311', rate: 5 },
  CL: { hsn: '900130', rate: 5 },
  CCL: { hsn: '900130', rate: 5 },
  LS: { hsn: '900150', rate: 5 },
  RG: { hsn: '900490', rate: 5 },
  WT: { hsn: '910111', rate: 18 },
  CK: { hsn: '910500', rate: 18 },
  HA: { hsn: '902140', rate: 0 },
  ACC: { hsn: '392690', rate: 18 },
  SMTWT: { hsn: '910221', rate: 18 },
  // GST-REVIEW, and OLDER than this table: the server's default code for
  // electronic eyewear is 852580, the screen's is 900410. Both are 18%, so the
  // screen and the stored rate still agree -- and the screen sends its HSN
  // explicitly, so 900410 is what gets stored either way. Only the CODE
  // differs, and moving it is a data decision (35 live products already carry
  // 852580), not a rename. Listed so it stays visible instead of being an
  // omission from this table.
  SMTSG: { hsn: '852580', rate: 18 },
  SMTFR: { hsn: '852580', rate: 18 },
};

const PICKER = CATEGORIES.map((c) => [c.code, c.name] as const);

describe('the Add-Product picker cannot offer a category the tax table has not priced', () => {
  it('prices every code the picker offers', () => {
    expect(Object.keys(SERVER_CATEGORY_DEFAULT).sort()).toEqual(
      CATEGORIES.map((c) => c.code as string).sort(),
    );
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

  it.each(PICKER)('%s (%s) charges what the server charges', (code) => {
    expect(getGSTRateByCategory(code)).toBe(SERVER_CATEGORY_DEFAULT[code].rate);
  });

  it('prices colour contact lenses as contact lenses, at 5% on HSN 900130', () => {
    // The regression in one line: 900490 at 18% was the answer before.
    expect(getHSNByCategory('CCL')!.code).toBe('900130');
    expect(getGSTRateByCategory('CCL')).toBe(5);
  });
});
