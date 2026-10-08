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
  productToCloneValues,
  productToFormValues,
  validateReviewForm,
  variantFieldRule,
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

// Clone of a product with a manufacturer barcode: the GTIN rode into the new
// SKU's form, so Save got a 409 ('already assigned to another product') until
// the operator spotted and emptied the box, and the UPC rode along unchecked.
describe('Clone never copies the manufacturer barcodes', () => {
  const source = {
    category: 'FRAME',
    brand: 'Ray-Ban',
    attributes: { colour_code: 'BLK', gtin: '5901234123457', upc: '036000291452' },
  };

  it('drops gtin and upc, keeps the rest', () => {
    const { attributes } = productToCloneValues(source);
    expect(attributes.gtin).toBeUndefined();
    expect(attributes.upc).toBeUndefined();
    expect(attributes.colour_code).toBe('BLK');
  });

  // The server folds a key in any letter case or padding onto gtin / upc, so
  // an old stored 'GTIN' is the same code: copied, it made C a second holder.
  it('drops them under any spelling', () => {
    const { attributes } = productToCloneValues({
      ...source,
      attributes: { colour_code: 'BLK', GTIN: '5901234123457', ' Upc ': '036000291452', Gtin: '' },
    });
    expect(attributes).toEqual({ colour_code: 'BLK', brand_name: 'Ray-Ban' });
    for (const k of ['GTIN', ' Upc ', 'gTiN ']) expect(variantFieldRule('FR', k)).toBe('never');
  });

  it('agrees with the variant rulebook, while Edit still loads them', () => {
    expect(variantFieldRule('FR', 'gtin')).toBe('never');
    expect(variantFieldRule('FR', 'upc')).toBe('never');
    expect(productToFormValues(source).attributes.gtin).toBe('5901234123457');
  });
});
