// S5 review: the forecast's category page worked out its own "Recommended
// Reorder Qty" (forecast + 15-day buffer - stock). It is now the sum of the
// server's reorder_recommended -- THE buy rule's number, the Reorder list's.

import { describe, expect, it } from 'vitest';
import { deriveCategoryForecasts, deriveReorderSuggestions } from '../DemandForecast';

const row = (over: Record<string, unknown>) => ({
  product_id: 'P', product_name: 'P', brand: 'B', category: 'FRAME', avg_daily_sales: 2,
  trend: 'stable' as const, predicted_30_day: 60, current_stock: 1, reorder_recommended: 0, ...over,
});

describe('demand forecast numbers are the server buy', () => {
  it('a category recommends the sum of its products buys, never its own formula', () => {
    const cats = deriveCategoryForecasts(
      [row({ product_id: 'A', reorder_recommended: 4 }), row({ product_id: 'B', reorder_recommended: 0 }),
        row({ product_id: 'C', category: 'SUNGLASS', reorder_recommended: 7 })],
      90,
    );
    const by = Object.fromEntries(cats.map((c) => [c.category, c.reorderQty]));
    // a 15-day-buffer formula would say 2*2*90 + 2*2*15 - 2 = 418 for FRAME
    expect(by).toEqual({ FRAME: 4, SUNGLASS: 7 });
  });

  it("an urgent row carries the server's wording for the buy", () => {
    const [s] = deriveReorderSuggestions([
      row({ reorder_recommended: 4, basis: 'level', reason: "Buy 4: back above the shop's level." }),
    ]);
    expect(s.suggestedQty).toBe(4);
    expect(s.reason).toContain("Buy 4: back above the shop's level.");
    expect(s.reason).not.toMatch(/30-day demand/);
  });
});
