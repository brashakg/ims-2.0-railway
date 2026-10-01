// Wave 2 Reports split — the legacy /reports?tab= deep links must all still
// land somewhere, and the one that was BROKEN must now land right.
import { describe, it, expect } from 'vitest';
import { legacyTabTarget } from '../legacyTabRedirect';

describe('legacyTabTarget', () => {
  it('maps every tab the old page could actually show', () => {
    expect(legacyTabTarget('?tab=sales')).toBe('/reports/sales');
    expect(legacyTabTarget('?tab=inventory')).toBe('/reports/inventory');
    expect(legacyTabTarget('?tab=customers')).toBe('/reports/customers');
    expect(legacyTabTarget('?tab=gst')).toBe('/reports/gst');
  });

  // The declared behaviour change: the old allow-list omitted 'forecast', so
  // /reports?tab=forecast silently rendered Sales. Nobody can depend on that.
  it('sends ?tab=forecast to the forecast page (was a live bug)', () => {
    expect(legacyTabTarget('?tab=forecast')).toBe('/reports/forecast');
  });

  // Wave 6 B13 split the forecast page into /reports/forecast (category, the
  // index) + /seasonal + /reorder. The panels never had a ?tab= id of their
  // own, so an old link still lands where it did: ?tab=forecast on the
  // category panel (the index route). The panel names are not tab ids and
  // fall back to Sales like any other unknown value - nothing invented.
  it('after the panel split, ?tab=forecast is still the category (index) panel; panel names are not tab ids', () => {
    expect(legacyTabTarget('?tab=forecast')).toBe('/reports/forecast');
    expect(legacyTabTarget('?tab=seasonal')).toBe('/reports/sales');
    expect(legacyTabTarget('?tab=reorder')).toBe('/reports/sales');
  });

  it('falls back to sales for a bare /reports and for tabs that never existed', () => {
    expect(legacyTabTarget('')).toBe('/reports/sales');
    // ModuleContext still links these; they landed on Sales before, and do now.
    expect(legacyTabTarget('?tab=dead-stock')).toBe('/reports/sales');
    expect(legacyTabTarget('?tab=churn')).toBe('/reports/sales');
  });

  it('carries every other query param through', () => {
    expect(legacyTabTarget('?tab=gst&month=2026-08')).toBe('/reports/gst?month=2026-08');
    expect(legacyTabTarget('?store_id=BV1')).toBe('/reports/sales?store_id=BV1');
  });
});
