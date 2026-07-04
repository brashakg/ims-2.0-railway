// Unit tests for the variant-assist copy rulebook (Phase 1).
//
// Locks the owner-ruled per-category rules productToVariantFormValues applies
// when flipping the Add-Product form into VARIANT MODE (duplicate-rescue popup
// default action / the "+ Variant" ?variant=<id> deep link):
//
//   copy silently  - model-level attributes (brand/model/shape/materials/...)
//   copy + FLAG    - sizes (lens/bridge/temple/size/dial/belt) and the
//                    form-level pricing + HSN/GST (amber until touched)
//   NEVER          - every colour-ish field, polarization (SG), mfr identity
//                    codes (upc/gtin/full_model_no/serial_no), batch expiry,
//                    description, images
//   unknown keys   - DEFAULT-DENY (fail closed)
//
// The registry fetch is NOT mocked/loaded here, so getCategoryFields uses the
// local CATEGORY_FIELDS metadata — deterministic for these pure-function tests.

import { describe, it, expect } from 'vitest';
import {
  variantFieldRule,
  VARIANT_COPY_FIELDS,
  variantFlaggedFormFields,
  productToVariantFormValues,
  CATEGORIES,
  CATEGORY_FIELDS,
  type ProductDoc,
} from '../productAddShared';

describe('variantFieldRule', () => {
  it('FAILS CLOSED: unknown keys are never copied', () => {
    expect(variantFieldRule('SG', 'mystery_key')).toBe('never');
    expect(variantFieldRule('FR', 'some_future_field')).toBe('never');
    expect(variantFieldRule('SG', '')).toBe('never');
  });

  it('never copies colour-ish fields (pattern-matched, every category)', () => {
    for (const name of [
      'colour_code',
      'colour_name',
      'frame_color',
      'temple_color',
      'lens_colour',
      'tint',
      'dial_colour',
      'belt_colour',
      'body_colour',
    ]) {
      expect(variantFieldRule('SG', name)).toBe('never');
      expect(variantFieldRule('WT', name)).toBe('never');
    }
  });

  it('never copies polarization (variant-defining on sunglasses)', () => {
    expect(variantFieldRule('SG', 'polarization')).toBe('never');
  });

  it('never copies manufacturer identity codes or batch expiry', () => {
    for (const name of [
      'upc',
      'gtin',
      'full_model_no',
      'serial_no',
      'sku',
      'barcode',
      'expiry_date',
      'autopilot_reference',
    ]) {
      expect(variantFieldRule('SG', name)).toBe('never');
    }
  });

  it('copies sizes but FLAGS them for confirmation', () => {
    for (const name of [
      'lens_size',
      'bridge_width',
      'temple_length',
      'size',
      'dial_size',
      'belt_size',
    ]) {
      expect(variantFieldRule('SG', name)).toBe('flag');
    }
  });

  it('copies model-level attributes silently', () => {
    for (const name of [
      'brand_name',
      'subbrand',
      'label',
      'model_no',
      'model_name',
      'shape',
      'frame_type',
      'gender',
      'warranty',
      'country_of_origin',
      'usp_1',
      'usp_2',
      'blue_cut_lens',
      'lens_material',
      'frame_material',
      'temple_material',
    ]) {
      expect(variantFieldRule('FR', name)).toBe('copy');
    }
  });
});

describe('VARIANT_COPY_FIELDS (per-category derivation)', () => {
  it('covers every category picker code with a complete partition', () => {
    for (const c of CATEGORIES) {
      const rules = VARIANT_COPY_FIELDS[c.code];
      expect(rules, `rules for ${c.code}`).toBeTruthy();
      const declared = (CATEGORY_FIELDS[c.code] || []).map((f) => f.name);
      const all = [...rules.copy, ...rules.flag, ...rules.cleared].sort();
      expect(all).toEqual([...declared].sort());
    }
  });

  it('SG clears colour + polarization + mfr codes, flags sizes', () => {
    const sg = VARIANT_COPY_FIELDS.SG;
    for (const n of ['colour_code', 'lens_colour', 'tint', 'frame_color', 'temple_color', 'polarization', 'upc', 'gtin']) {
      expect(sg.cleared).toContain(n);
    }
    expect(sg.flag).toEqual(
      expect.arrayContaining(['lens_size', 'bridge_width', 'temple_length'])
    );
    expect(sg.copy).toEqual(
      expect.arrayContaining(['brand_name', 'model_no', 'shape', 'frame_type', 'lens_material'])
    );
  });

  it('FR copies blue_cut_lens; CL clears its power/batch variant fields', () => {
    expect(VARIANT_COPY_FIELDS.FR.copy).toContain('blue_cut_lens');
    const cl = VARIANT_COPY_FIELDS.CL;
    // power / toric / pack / expiry are variant-defining or batch-specific —
    // the fail-closed default keeps them out of the copy set.
    for (const n of ['power', 'base_curve', 'diameter', 'cl_cyl', 'cl_axis', 'cl_add', 'pack', 'expiry_date', 'colour_name']) {
      expect(cl.cleared).toContain(n);
    }
    expect(cl.copy).toEqual(expect.arrayContaining(['brand_name', 'cl_series', 'model_name', 'modality']));
  });
});

// ---------------------------------------------------------------------------
// productToVariantFormValues
// ---------------------------------------------------------------------------

const SG_PRODUCT: ProductDoc = {
  product_id: 'P-1',
  sku: 'SGRAYBANRB2140BLK',
  category: 'SUNGLASS', // canonical long-form, as stored on the spine
  brand: 'Ray-Ban',
  model: 'RB-2140',
  description: 'Classic wayfarer in gloss black.',
  hsn_code: '9004',
  gst_rate: 18,
  weight: 45,
  mrp: 5000,
  offer_price: 4500,
  cost_price: 2000,
  discount_category: 'PREMIUM',
  images: ['/api/v1/products/image/a', '/api/v1/products/image/b'],
  attributes: {
    brand_name: 'Ray-Ban',
    model_no: 'RB-2140',
    model_name: 'Wayfarer',
    colour_code: 'BLK',
    frame_color: 'Black',
    temple_color: 'Black',
    lens_colour: 'Green G-15',
    tint: 'Solid',
    polarization: 'Yes',
    lens_size: '52',
    bridge_width: '18',
    temple_length: '140',
    shape: 'Wayfarer',
    frame_type: 'Full Rim',
    gender: 'Unisex',
    lens_material: 'Glass',
    warranty: '12 months',
    country_of_origin: 'Italy',
    usp_1: 'Iconic since 1952',
    upc: '805289048725',
    gtin: '00805289048725',
    mystery_scraped_key: 'something',
  },
};

describe('productToVariantFormValues', () => {
  it('resolves the canonical category to the picker code', () => {
    const seed = productToVariantFormValues(SG_PRODUCT);
    expect(seed.category).toBe('SG');
    expect(seed.values.category).toBe('SG');
  });

  it('locks brand + model, copies model-level attributes', () => {
    const seed = productToVariantFormValues(SG_PRODUCT);
    expect(seed.locked).toEqual(['brand_name', 'model_no', 'model_name']);
    expect(seed.values.attributes.brand_name).toBe('Ray-Ban');
    expect(seed.values.attributes.model_no).toBe('RB-2140');
    expect(seed.values.attributes.shape).toBe('Wayfarer');
    expect(seed.values.attributes.frame_type).toBe('Full Rim');
    expect(seed.values.attributes.lens_material).toBe('Glass');
    expect(seed.values.attributes.warranty).toBe('12 months');
    expect(seed.values.attributes.usp_1).toBe('Iconic since 1952');
  });

  it('clears every colour field + polarization + mfr codes; drops unknown keys (fail closed)', () => {
    const seed = productToVariantFormValues(SG_PRODUCT);
    for (const n of ['colour_code', 'frame_color', 'temple_color', 'lens_colour', 'tint', 'polarization', 'upc', 'gtin', 'mystery_scraped_key']) {
      expect(seed.values.attributes[n], n).toBeUndefined();
      expect(seed.dropped).toContain(n);
    }
    // the first CLEARED field (focus target) is the colour code — the first
    // variant-defining field in the category's render order.
    expect(seed.cleared[0]).toBe('colour_code');
  });

  it('copies sizes + prices but flags them amber (owner ruling)', () => {
    const seed = productToVariantFormValues(SG_PRODUCT);
    expect(seed.values.attributes.lens_size).toBe('52');
    expect(seed.values.attributes.bridge_width).toBe('18');
    expect(seed.values.attributes.temple_length).toBe('140');
    expect(seed.values.mrp).toBe('5000');
    expect(seed.values.offerPrice).toBe('4500');
    expect(seed.values.costPrice).toBe('2000');
    expect(seed.flagged).toEqual(
      expect.arrayContaining([
        'lens_size',
        'bridge_width',
        'temple_length',
        'mrp',
        'offer_price',
        'cost_price',
        'hsn_code',
        'gst_rate',
        'weight',
      ])
    );
  });

  it('never copies description or images; sibling photos ride separately', () => {
    const seed = productToVariantFormValues(SG_PRODUCT);
    expect(seed.values.description).toBe('');
    expect(seed.values.images).toEqual([]);
    expect(seed.sourceImages).toEqual([
      '/api/v1/products/image/a',
      '/api/v1/products/image/b',
    ]);
  });

  it('behaves like a fresh create for online flags + carries the source identity', () => {
    const seed = productToVariantFormValues(SG_PRODUCT);
    expect(seed.values.syncToShopify).toBe(false);
    expect(seed.values.shopifyTags).toEqual([]);
    expect(seed.values.publishPOS).toBe(true);
    expect(seed.sourceProductId).toBe('P-1');
    expect(seed.sourceSku).toBe('SGRAYBANRB2140BLK');
    expect(seed.sourceLabel).toBe('Ray-Ban RB-2140');
  });

  it('drops a dictionary-governed value outside the current options (with a note), case-insensitively keeps valid ones', () => {
    const doc: ProductDoc = {
      ...SG_PRODUCT,
      attributes: {
        ...(SG_PRODUCT.attributes as Record<string, unknown>),
        gender: 'Boys', // not in the SG gender options
        frame_type: 'full rim', // valid, differs only in case
      },
    };
    const seed = productToVariantFormValues(doc);
    expect(seed.values.attributes.gender).toBeUndefined();
    expect(seed.dictionaryDropped).toContain('gender');
    expect(seed.values.attributes.frame_type).toBe('full rim');
  });

  it('keeps + locks a brand outside the local options (Brand Master governed the sibling)', () => {
    const doc: ProductDoc = {
      ...SG_PRODUCT,
      attributes: {
        ...(SG_PRODUCT.attributes as Record<string, unknown>),
        brand_name: 'Carrera', // not in the local fallback brand list
      },
    };
    const seed = productToVariantFormValues(doc);
    expect(seed.values.attributes.brand_name).toBe('Carrera');
    expect(seed.locked).toContain('brand_name');
    expect(seed.dictionaryDropped).not.toContain('brand_name');
  });

  it('works for ALL categories (no category-specific crash; partition holds)', () => {
    for (const c of CATEGORIES) {
      const doc: ProductDoc = {
        product_id: `P-${c.code}`,
        sku: `${c.code}-1`,
        category: c.code,
        attributes: Object.fromEntries(
          (CATEGORY_FIELDS[c.code] || []).map((f) => [f.name, f.options?.[0] || 'x'])
        ),
        mrp: 100,
        offer_price: 90,
      };
      const seed = productToVariantFormValues(doc, c.code);
      expect(seed.category).toBe(c.code);
      // no colour-ish key survives in any category
      Object.keys(seed.values.attributes).forEach((k) => {
        expect(/colou?r|tint/i.test(k), `${c.code}.${k}`).toBe(false);
      });
    }
  });
});

describe('variantFlaggedFormFields', () => {
  it('flags only the values that are present', () => {
    const seed = productToVariantFormValues(SG_PRODUCT);
    const flags = variantFlaggedFormFields(seed.values);
    expect(flags).toContain('mrp');
    expect(flags).toContain('offer_price');
    expect(flags).toContain('cost_price');
    expect(flags).toContain('discount_category');
    const noPrices = variantFlaggedFormFields({
      ...seed.values,
      mrp: '',
      offerPrice: '',
      costPrice: '',
      discountCategory: '',
      weight: '',
      hsnCode: '',
      gstRate: '',
    });
    expect(noPrices).toEqual([]);
  });
});
