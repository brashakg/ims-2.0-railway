// The New Collection chip builder's output (review round 1 of the website
// menu PR): every rule it sends is ONE value the website can read; several
// values on one field ("any of") are refused, never compiled to IN -- an IN
// rule makes the collection show 0 products (collection_membership).
import { describe, it, expect } from 'vitest';
import { compileBuilderRules } from '../collectionsShared';

const base = { category: '', brands: [] as string[], attrs: [], tags: [] as string[], priceMin: '', priceMax: '' };

describe('compileBuilderRules', () => {
  it('one value per chip group -> EQUALS rules, ANDed, plus the price band', () => {
    const out = compileBuilderRules({
      ...base,
      category: 'SUNGLASS',
      brands: ['Ray-Ban'],
      attrs: [{ name: 'frame_color', label: 'Frame colour', values: ['Black'] }],
      tags: ['bestseller'],
      priceMin: '3000',
      priceMax: '10000',
    });
    expect(out.multi).toEqual([]);
    expect(out.rules).toEqual([
      { field: 'category', relation: 'EQUALS', value: 'SUNGLASS' },
      { field: 'brand', relation: 'EQUALS', value: 'Ray-Ban' },
      { field: 'frame_color', relation: 'EQUALS', value: 'Black' },
      { field: 'tag', relation: 'EQUALS', value: 'bestseller' },
      { field: 'price', relation: 'GREATER_THAN', value: 3000 },
      { field: 'price', relation: 'LESS_THAN', value: 10000 },
    ]);
  });

  it('several values on one field are refused, never an IN rule', () => {
    const out = compileBuilderRules({
      ...base,
      brands: ['Ray-Ban', 'Oakley'],
      attrs: [{ name: 'frame_color', label: 'Frame colour', values: ['Black', 'Grey'] }],
    });
    expect(out.multi).toEqual(['Brand', 'Frame colour']);
    expect(out.rules.some((r) => r.relation === 'IN')).toBe(false);
  });
});
