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
