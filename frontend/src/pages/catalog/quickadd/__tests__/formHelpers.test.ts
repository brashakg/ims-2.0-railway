import { describe, it, expect } from 'vitest';
import { copyableSpecs, softWarnings, specKey } from '../formHelpers';

const LAST = {
  category: 'SG',
  attributes: {
    brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '601', frame_material: 'Metal',
    warranty: '1 Year', lens_size: '54', upc: '8056597000000', full_model_no: '0RB4165 601/71',
    gender: 'Unisex',
  },
};

describe('copyableSpecs', () => {
  it('copies the repeating specs only: never model, colour, barcode or full model no', () => {
    const out = copyableSpecs('SG', LAST, { brand_name: 'Ray-Ban', model_no: 'RB2140' });
    expect(out).toEqual({ frame_material: 'Metal', warranty: '1 Year', lens_size: '54', gender: 'Unisex' });
  });
  it('never overwrites a typed value, and only within the same category', () => {
    expect(copyableSpecs('SG', LAST, { warranty: '2 Years' }).warranty).toBeUndefined();
    expect(copyableSpecs('FR', LAST, {})).toEqual({});
    expect(copyableSpecs('SG', null, {})).toEqual({});
  });
  it('a sub-brand / label is offered only for the same brand', () => {
    const last = { category: 'SG', attributes: { brand_name: 'Ray-Ban', subbrand: 'Chromance', label: 'Gold', warranty: '1 Year' } };
    expect(copyableSpecs('SG', last, { brand_name: 'Oakley' })).toEqual({ warranty: '1 Year' });
    expect(copyableSpecs('SG', last, { brand_name: 'RAY BAN' })).toEqual({
      subbrand: 'Chromance', label: 'Gold', warranty: '1 Year',
    });
  });
});

const base = { mrp: '', imageCount: 3, showPhotoHint: true };

describe('softWarnings', () => {
  it('lens size 99 (unilens) is valid; bridge 01 is not', () => {
    expect(softWarnings({ ...base, attributes: { lens_size: '99' } })).toEqual([]);
    expect(softWarnings({ ...base, attributes: { bridge_width: '01' } })).toEqual([
      expect.stringMatching(/^Bridge 01 looks wrong/),
    ]);
    expect(softWarnings({ ...base, attributes: { lens_size: '54', bridge_width: '18', temple_length: '145' } })).toEqual([]);
    expect(softWarnings({ ...base, attributes: { lens_size: '540', temple_length: '14' } })).toHaveLength(2);
  });
  it('brand repeated in the model number: the server says so, no rule here', () => {
    // #1271: the hint is the duplicate key's own fold (GET /products/similar
    // brand_in_model); the form keeps no copy of the rule.
    const carrera = { brand_name: 'Carrera', model_no: 'CARRERA 3001/S' };
    expect(softWarnings({ ...base, attributes: carrera, brandInModel: true })[0]).toMatch(/repeats the brand/);
    expect(softWarnings({ ...base, attributes: carrera, brandInModel: false })).toEqual([]);
    expect(softWarnings({ ...base, attributes: carrera })).toEqual([]);
  });
  it('price far from the model, and fewer than 2 photos', () => {
    expect(softWarnings({ ...base, attributes: {}, mrp: '20000', modelMrp: '7890' })[0]).toMatch(/far from/);
    expect(softWarnings({ ...base, attributes: {}, mrp: '8500', modelMrp: '7890' })).toEqual([]);
    expect(softWarnings({ ...base, attributes: {}, imageCount: 1 })[0]).toMatch(/Only 1 photo/);
    expect(softWarnings({ ...base, attributes: {}, imageCount: 0, showPhotoHint: false })).toEqual([]);
  });
});

describe('specKey', () => {
  it('ignores blanks and order, and sees a changed spec', () => {
    expect(specKey({ a: '1', b: '' }, '')).toBe(specKey({ a: '1' }, ''));
    expect(specKey({ a: '1' }, '20')).not.toBe(specKey({ a: '2' }, '20'));
  });
});
