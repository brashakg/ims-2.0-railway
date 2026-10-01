// The /customers?tab= shim. Pure function, no router, no auth, no chunks.
//
// Discriminating power: drop `recalls` from the table and the first case fails;
// make the no-tab case return a path instead of null and the customer list
// turns into a redirect loop, which the last two cases catch.
import { describe, it, expect } from 'vitest';
import { legacyTabTarget, legacyLoyaltyTabTarget } from '../legacyTabRedirect';

describe('legacy /customers?tab= links keep working', () => {
  it('?tab=recalls -> /customers/recalls (the address the in-page tab never had)', () => {
    expect(legacyTabTarget('?tab=recalls')).toBe('/customers/recalls');
  });

  it('?tab=campaigns -> /customers/campaigns (was an in-page Navigate)', () => {
    expect(legacyTabTarget('?tab=campaigns')).toBe('/customers/campaigns');
  });

  it('carries every other query param across', () => {
    expect(legacyTabTarget('?tab=recalls&search=true')).toBe('/customers/recalls?search=true');
  });

  it('accepts URLSearchParams as well as a search string', () => {
    expect(legacyTabTarget(new URLSearchParams({ tab: 'recalls' }))).toBe('/customers/recalls');
  });

  it('no tab -> null, so bare /customers renders the customer list', () => {
    expect(legacyTabTarget('')).toBeNull();
    expect(legacyTabTarget('?search=true')).toBeNull();
  });

  it('a tab that was never a tab -> null, exactly as the old page fell through', () => {
    expect(legacyTabTarget('?tab=customers')).toBeNull();
    expect(legacyTabTarget('?tab=churn')).toBeNull();
  });

  it('an inherited Object key is never a tab, not a URL built from Object.prototype', () => {
    for (const k of ['constructor', '__proto__', 'toString', 'hasOwnProperty']) {
      expect(legacyTabTarget(`?tab=${k}`)).toBeNull();
    }
  });
});

// Wave 6 B12: the loyalty page's tabs became URLs. Drop `tiers` from the table
// and the first case fails; map the deleted `promotions` tab anywhere but the
// Overview and the third case fails.
describe('legacy /customers/loyalty?tab= links land on the section', () => {
  it('?tab=tiers / ?tab=rewards -> that section\'s own URL', () => {
    expect(legacyLoyaltyTabTarget('?tab=tiers')).toBe('/customers/loyalty/tiers');
    expect(legacyLoyaltyTabTarget('?tab=rewards')).toBe('/customers/loyalty/rewards');
  });

  it('?tab=overview -> the bare page (the Overview is the index)', () => {
    expect(legacyLoyaltyTabTarget('?tab=overview')).toBe('/customers/loyalty');
  });

  it('the deleted promotions tab, and anything unknown, land on the Overview', () => {
    expect(legacyLoyaltyTabTarget('?tab=promotions')).toBe('/customers/loyalty');
    expect(legacyLoyaltyTabTarget('?tab=nonsense')).toBe('/customers/loyalty');
    for (const k of ['constructor', '__proto__', 'toString']) {
      expect(legacyLoyaltyTabTarget(`?tab=${k}`)).toBe('/customers/loyalty');
    }
  });

  it('carries every other query param through', () => {
    expect(legacyLoyaltyTabTarget('?tab=rewards&active=1')).toBe('/customers/loyalty/rewards?active=1');
    expect(legacyLoyaltyTabTarget(new URLSearchParams({ tab: 'tiers' }))).toBe('/customers/loyalty/tiers');
  });
});
