// Quick Add's margin read-out: cost is entered before GST, MRP includes it, so
// the margin is on the MRP with the GST taken out (a 5% frame and an 18%
// sunglass, each Rs 1000 before GST and costing Rs 600, are both 40% -- not
// the 43% / 49% that counted the GST as margin).

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PricingSection } from '../PricingSection';
import type { QuickAddForm } from '../useQuickAddForm';

function form(mrp: string, gstRate: string): QuickAddForm {
  const noop = () => {};
  return {
    mrp, setMrp: noop, offerPrice: '', setOfferPrice: noop, costPrice: '600', setCostPrice: noop,
    gstRate, errors: {}, flaggedFields: new Set<string>(), clearFlag: noop, canSeeCost: true,
    openSections: { pricing: true }, toggleSection: noop, sectionIssues: { pricing: 0 },
  } as unknown as QuickAddForm;
}

describe('Quick Add margin is on the MRP before GST', () => {
  it.each([
    ['1050', '5'],
    ['1180', '18'],
  ])('MRP %s at GST %s percent, cost 600: a 40 percent margin', (mrp, gst) => {
    render(<PricingSection form={form(mrp, gst)} />);
    expect(screen.getByText('Margin on MRP before GST: 40%')).toBeTruthy();
  });
});
