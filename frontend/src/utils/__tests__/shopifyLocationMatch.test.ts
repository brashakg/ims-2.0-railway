// The preselect rule for the Organization StoreModal's "Shopify location"
// dropdown (per-store locations, owner ruling 2026-09-06): exact
// case-insensitive name/code equality only. Each case is revert-proof --
// loosen the rule to a substring match and the "never a substring" cases fail.
import { describe, expect, it } from 'vitest';
import { exactLocationMatch } from '../shopifyLocationMatch';

const LOCS = [
  { id: 'gid://shopify/Location/1', name: 'Better Vision Sector 4' },
  { id: 'gid://shopify/Location/2', name: 'Gangadham Pune' },
  { id: 'gid://shopify/Location/3', name: 'hirapur-dhn' },
  { id: 'gid://shopify/Location/4', name: 'BV-BOK-02' },
];

describe('exactLocationMatch', () => {
  it('preselects on an exact case-insensitive store_name match', () => {
    expect(exactLocationMatch(LOCS, { store_name: 'HIRAPUR-DHN', store_code: 'BV-DHN-02' }))
      .toBe('gid://shopify/Location/3');
  });

  it('preselects on an exact store_code match and trims whitespace', () => {
    expect(exactLocationMatch(LOCS, { store_name: 'Sec 4 Bokaro', store_code: ' bv-bok-02 ' }))
      .toBe('gid://shopify/Location/4');
  });

  it('never matches a substring or a shared word (the #1125 hint-picker trap)', () => {
    expect(exactLocationMatch(LOCS, { store_name: 'Sector 4', store_code: 'BV-BOK-03' })).toBe('');
    expect(exactLocationMatch(LOCS, { store_name: 'Better Vision', store_code: 'BV-ONLINE-01' })).toBe('');
    expect(exactLocationMatch(LOCS, { store_name: 'Pune', store_code: 'BV-PUN-01' })).toBe('');
    expect(exactLocationMatch(LOCS, { store_name: 'Better Vision Sector 4 Bokaro', store_code: 'X' })).toBe('');
  });

  it('refuses an ambiguous pick (two locations with the same name)', () => {
    const dup = [...LOCS, { id: 'gid://shopify/Location/9', name: 'Gangadham Pune' }];
    expect(exactLocationMatch(dup, { store_name: 'gangadham pune', store_code: 'BV-PUN-01' })).toBe('');
  });

  it('is empty for a blank store, no locations or a nameless location', () => {
    expect(exactLocationMatch(LOCS, { store_name: '', store_code: '' })).toBe('');
    expect(exactLocationMatch([], { store_name: 'hirapur-dhn' })).toBe('');
    expect(exactLocationMatch(undefined, { store_name: 'hirapur-dhn' })).toBe('');
    expect(exactLocationMatch([{ id: 'gid://shopify/Location/5', name: null }], { store_name: '' })).toBe('');
  });
});
