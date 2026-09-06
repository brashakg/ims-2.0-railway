// ============================================================================
// Quick Add -> "open the existing product" lands on a REAL address
// ============================================================================
// Both product-open doors on the add screen (the duplicate rescue popup's
// "Open the existing product" and the similar-products strip's "Open it")
// used to link `/inventory?tab=catalog&search=<sku>` — a mega-page tab that
// the Wave 2 split turned into a redirect. They share productListPath now, so
// this fails the moment anyone puts the `?tab=` form back.

import { describe, it, expect } from 'vitest';
import { productListPath } from '../QuickAddPage';
import { legacyTabTarget } from '../../inventory/legacyTabRedirect';

describe('productListPath', () => {
  it('is the stock ledger, pre-scoped to the SKU', () => {
    expect(productListPath('FR-RAYB-3025-GLD')).toBe(
      '/inventory/stock?search=FR-RAYB-3025-GLD'
    );
    expect(productListPath('FR RAY/B')).toBe('/inventory/stock?search=FR%20RAY%2FB');
  });

  it('drops the search param when there is no SKU', () => {
    expect(productListPath()).toBe('/inventory/stock');
    expect(productListPath(null)).toBe('/inventory/stock');
    expect(productListPath('')).toBe('/inventory/stock');
  });

  it('never emits a legacy ?tab= link', () => {
    expect(productListPath('SKU-1')).not.toContain('tab=');
  });

  // The redirect shim stays for old bookmarks: it must still land on exactly
  // where the direct link now goes, or the two doors would diverge.
  it('matches where the legacy ?tab=catalog deep link still forwards', () => {
    expect(legacyTabTarget('?tab=catalog&search=FR-RAYB-3025-GLD')).toBe(
      productListPath('FR-RAYB-3025-GLD')
    );
  });
});
