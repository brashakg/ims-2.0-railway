// ============================================================================
// IMS 2.0 - the Quick Add GST box quotes a number only when it can stand behind it
// ============================================================================
// The Add-Product form shows the CATEGORY's rate. The rate that actually gets
// stored is derived from the HSN, server-side (product_master.normalise_payload
// -> gst_rates.resolve_gst_rate_strict). Those agree for the HSN a category
// auto-fills, and only then -- so once the cataloguer picks a different code
// the box must stop quoting a number and say the HSN settles it on save.
//
// This shipped in round 3 with no test at all: neutering the rule to a constant
// `true` left the whole 937-test frontend suite green while the box went back
// to asserting a rate the server would overrule.

import { describe, it, expect } from 'vitest';
import {
  CATEGORIES,
  hsnImpliesCategoryRate,
  resolveHsnGst,
} from '../productAddShared';

describe('may the GST box quote the category rate?', () => {
  it.each(CATEGORIES.map((c) => [c.code, c.name] as const))(
    'yes for %s (%s) while the HSN is the one the category filled in',
    (code) => {
      expect(hsnImpliesCategoryRate(code, resolveHsnGst(code).hsnCode)).toBe(true);
    },
  );

  it('no once the cataloguer picks a different HSN', () => {
    // A frame (900311, 5%) recoded to sunglasses (900410, 18%). Quoting 5% here
    // is the screen promising a rate the save will not honour.
    expect(hsnImpliesCategoryRate('FR', '900410')).toBe(false);
    // ... and the other way: sunglasses recoded to a corrective spectacle.
    expect(hsnImpliesCategoryRate('SG', '900490')).toBe(false);
  });

  it('says nothing rather than guessing when there is nothing to compare', () => {
    // No HSN typed yet, or no category chosen: the box has no contradiction to
    // warn about, so it keeps showing the ordinary auto-filled figure.
    expect(hsnImpliesCategoryRate('FR', '')).toBe(true);
    expect(hsnImpliesCategoryRate('', '900410')).toBe(true);
  });
});
