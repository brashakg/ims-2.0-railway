// Wave 2 Inventory split - the legacy /inventory?tab= deep links must all
// still land somewhere, and the two DEDUPED tabs must land on the standalone
// pages that already implement them, not on resurrected copies.
import { describe, it, expect } from 'vitest';
import { legacyTabTarget } from '../legacyTabRedirect';

describe('legacyTabTarget', () => {
  it('maps every former mega-page tab to its section URL', () => {
    expect(legacyTabTarget('?tab=alerts')).toBe('/inventory/alerts');
    expect(legacyTabTarget('?tab=catalog')).toBe('/inventory/stock');
    expect(legacyTabTarget('?tab=display-layout')).toBe('/inventory/display-layout');
    expect(legacyTabTarget('?tab=low-stock')).toBe('/inventory/low-stock');
    expect(legacyTabTarget('?tab=reorders')).toBe('/inventory/reorders');
    expect(legacyTabTarget('?tab=serial-numbers')).toBe('/inventory/serial-numbers');
    expect(legacyTabTarget('?tab=aging')).toBe('/inventory/aging');
    expect(legacyTabTarget('?tab=transfers')).toBe('/inventory/transfers');
    expect(legacyTabTarget('?tab=movements')).toBe('/inventory/movements');
    expect(legacyTabTarget('?tab=non-moving')).toBe('/inventory/non-moving');
    expect(legacyTabTarget('?tab=contact-lens')).toBe('/inventory/contact-lens');
    expect(legacyTabTarget('?tab=sell-through')).toBe('/inventory/sell-through');
    expect(legacyTabTarget('?tab=overstock')).toBe('/inventory/overstock');
    expect(legacyTabTarget('?tab=brand-insights')).toBe('/inventory/brand-insights');
    expect(legacyTabTarget('?tab=collection-insights')).toBe('/inventory/collection-insights');
    expect(legacyTabTarget('?tab=rebalance')).toBe('/inventory/rebalance');
    expect(legacyTabTarget('?tab=quarantine')).toBe('/inventory/quarantine');
  });

  // The two deliberate dedupes: each tab was a SECOND implementation of a
  // screen that already had a real route. If either ever maps to a new
  // /inventory/stock-count or a widget page again, this fails.
  it('sends ?tab=stock-count to the ONE stock-count screen (/inventory/audit)', () => {
    expect(legacyTabTarget('?tab=stock-count')).toBe('/inventory/audit');
  });
  it('sends ?tab=power-grid to the typed lens-catalog page, not the old widget', () => {
    expect(legacyTabTarget('?tab=power-grid')).toBe('/inventory/power-grid');
  });

  it('falls back to the stock ledger for a bare /inventory and unknown tabs', () => {
    expect(legacyTabTarget('')).toBe('/inventory/stock');
    // ReportCardsGrid links ?tab=stock, which was never a valid tab and
    // always fell through to the ledger; it still must.
    expect(legacyTabTarget('?tab=stock')).toBe('/inventory/stock');
  });

  it('carries every other query param through', () => {
    // QuickAdd's "Open the existing product" rescue popup links this shape.
    expect(legacyTabTarget('?tab=catalog&search=FR-RAYB-3025'))
      .toBe('/inventory/stock?search=FR-RAYB-3025');
    // The Stock ledger Zone cell deep-links a fixture.
    expect(legacyTabTarget('?tab=display-layout&fixture=FIX-7'))
      .toBe('/inventory/display-layout?fixture=FIX-7');
  });
});
