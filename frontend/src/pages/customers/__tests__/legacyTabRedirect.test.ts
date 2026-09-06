// The /customers?tab= shim. Pure function, no router, no auth, no chunks.
//
// Discriminating power: drop `recalls` from the table and the first case fails;
// make the no-tab case return a path instead of null and the customer list
// turns into a redirect loop, which the last two cases catch.
import { describe, it, expect } from 'vitest';
import { legacyTabTarget } from '../legacyTabRedirect';

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
});
