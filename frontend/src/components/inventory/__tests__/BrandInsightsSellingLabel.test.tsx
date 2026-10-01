// ============================================================================
// Audit F47: the Brands table's selling figure is labelled as SELLING
// ============================================================================
// Inventory > Insights > Brands (/inventory/brand-insights). The stat strip
// above the table says "Stock value ... at cost". The table's column was also
// headed "Stock value", but GET /inventory/brand-insights prices on-hand units
// at the offer price (MRP when there is none) -- the selling price
// (backend/api/services/brand_insights.py brand_maps). Seen as a store
// manager: "Rs 0.1L at cost" in the strip above a brand row "Stock value
// Rs 10,000" at selling price. Ruling 2026-09-28: the stock value headline is
// at cost; selling value is shown separately and labelled.

import { render, screen, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'], roles: ['STORE_MANAGER'] },
  }),
}));

let inclusive = true;
vi.mock('../../../constants/gstRuntime', () => ({
  isInclusivePricing: () => inclusive,
}));

// One brand: 2 frames on hand at offer price 5,000 = Rs 10,000 selling.
const brandInsights = vi.fn(async () => ({
  period_days: 30,
  store_id: 'BV-DHN-01',
  brands: [
    {
      brand: 'Carrera',
      units_on_hand: 2,
      stock_value: 10000,
      units_sold: 1,
      revenue: 5000,
      sell_through_percent: 33.3,
      days_cover: 60,
    },
  ],
}));
vi.mock('../../../services/api/inventory', () => ({
  inventoryApi: { brandInsights: (...a: unknown[]) => brandInsights(...(a as [])) },
}));

import { BrandInsightsWidget } from '../BrandInsightsWidget';

/** Header texts, whitespace-collapsed, in column order. */
function headers(): string[] {
  return screen
    .getAllByRole('columnheader')
    .map((th) => (th.textContent ?? '').replace(/\s+/g, ' ').trim());
}

/** The cell under the column whose header starts with `label`, in the row for `brand`. */
function cell(brand: string, label: string): string {
  const idx = headers().findIndex((h) => h.startsWith(label));
  expect(idx).toBeGreaterThanOrEqual(0);
  const row = screen.getByText(brand).closest('tr') as HTMLElement;
  return (within(row).getAllByRole('cell')[idx].textContent ?? '').trim();
}

describe('F47: Brand insights labels its selling figure as selling', () => {
  beforeEach(() => {
    inclusive = true;
    brandInsights.mockClear();
  });

  it('renders the brand row from the endpoint (harness check)', async () => {
    render(<BrandInsightsWidget />);
    expect(await screen.findByText('Carrera')).toBeInTheDocument();
    expect(brandInsights).toHaveBeenCalledWith(30, 'BV-DHN-01');
  });

  it('F47: the offer-price figure is headed "Selling value", never bare "Stock value"', async () => {
    render(<BrandInsightsWidget />);
    await screen.findByText('Carrera');
    const hs = headers();
    expect(hs.some((h) => /stock value/i.test(h))).toBe(false);
    expect(hs.some((h) => h.startsWith('Selling value'))).toBe(true);
    // The Rs 10,000 (2 x offer price 5,000) sits under the selling column.
    expect(cell('Carrera', 'Selling value')).toBe('₹10,000');
  });

  it('F47: the selling column says its basis -- offer price, GST included by default', async () => {
    render(<BrandInsightsWidget />);
    await screen.findByText('Carrera');
    const th = screen.getAllByRole('columnheader').find((h) => /Selling value/.test(h.textContent ?? ''))!;
    expect(th.textContent).toContain('at offer price, incl. GST');
    expect(th.getAttribute('title')).toMatch(/offer price \(MRP when there is none\), incl\. GST\. Not what it cost/);
  });

  it('F47: under the exclusive-GST rollback the basis says before GST', async () => {
    inclusive = false;
    render(<BrandInsightsWidget />);
    await screen.findByText('Carrera');
    const th = screen.getAllByRole('columnheader').find((h) => /Selling value/.test(h.textContent ?? ''))!;
    expect(th.textContent).toContain('at offer price, before GST');
  });
});
