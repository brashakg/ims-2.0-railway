// ============================================================================
// productAddShared — the shared trimmed attribute-diff predicate (PR #911
// adversarial finding 9): validateReviewForm's attrsTouched gate and
// formValuesToCatalogUpdate's per-key payload diff MUST agree. When they
// diverged (untrimmed gate vs trimmed diff), a whitespace-only touch of any
// attribute armed the full dictionary check over untouched legacy-invalid
// values and blocked a pricing-only save whose PUT did not even contain
// attributes.
// ============================================================================

import { describe, it, expect } from 'vitest';
import {
  attrChanged,
  formValuesToCatalogUpdate,
  validateReviewForm,
  type ProductFormValues,
} from '../productAddShared';

// Minimal SG-category form values (SG's brand_name is a select with local
// fallback options, so a dictionary-invalid legacy value is representable).
const fv = (
  attributes: Record<string, string>,
  over: Partial<ProductFormValues> = {}
): ProductFormValues => ({
  category: 'SG',
  attributes,
  gstRate: '18',
  mrp: '5000',
  offerPrice: '4500',
  discountCategory: '',
  syncToShopify: false,
  shopifyTags: [],
  publishPOS: true,
  ...over,
});

describe('attrChanged (shared trimmed predicate)', () => {
  it('treats a whitespace-only difference as unchanged', () => {
    expect(attrChanged(fv({ colour_code: 'Black ' }), fv({ colour_code: 'Black' }), 'colour_code')).toBe(false);
    expect(attrChanged(fv({ colour_code: '  Black' }), fv({ colour_code: 'Black ' }), 'colour_code')).toBe(false);
  });

  it('detects a real value change', () => {
    expect(attrChanged(fv({ colour_code: 'Blue' }), fv({ colour_code: 'Black' }), 'colour_code')).toBe(true);
  });

  it('treats absent keys as empty strings', () => {
    expect(attrChanged(fv({}), fv({ colour_code: '' }), 'colour_code')).toBe(false);
    expect(attrChanged(fv({ colour_code: 'Black' }), fv({}), 'colour_code')).toBe(true);
  });
});

describe('validateReviewForm and formValuesToCatalogUpdate stay in lockstep', () => {
  // Imported doc with a legacy dictionary-INVALID select value (brand not in
  // the SG options list) — must never block a save that does not touch attrs.
  const baseline = fv({ brand_name: 'LEGACY BAD BRAND', colour_code: 'Black' });

  it('whitespace-only attribute touch: payload omits attributes AND the dictionary gate stays disarmed', () => {
    // Reviewer fixes only the MRP but leaves a trailing space in colour.
    const values = fv(
      { brand_name: 'LEGACY BAD BRAND', colour_code: 'Black ' },
      { mrp: '5100' }
    );

    const payload = formValuesToCatalogUpdate(values, baseline);
    expect(payload.attributes).toBeUndefined(); // trimmed diff: nothing changed
    expect(payload.pricing).toEqual({ mrp: 5100 }); // the price fix rides alone

    const errors = validateReviewForm(values, baseline);
    expect(errors).toEqual({}); // the untouched legacy value must NOT block
  });

  it('real attribute change: payload carries the patch AND the dictionary gate arms', () => {
    const values = fv({ brand_name: 'LEGACY BAD BRAND', colour_code: 'Blue' });

    const payload = formValuesToCatalogUpdate(values, baseline);
    expect(payload.attributes).toEqual({ colour_code: 'Blue' });

    const errors = validateReviewForm(values, baseline);
    expect(errors.brand_name).toMatch(/not in the allowed list/);
  });
});
