// The Revenue Breakdown's Net Revenue is net of GST, the same basis as the
// P&L's Revenue card beside it (a Rs 2230 day of sales carrying Rs 230 GST is
// Rs 2000 of revenue, not 2230).

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import FinanceSummary from '../FinanceSummary';
import { mapRevenue } from '../financeUtils';

describe('Revenue Breakdown is net of GST', () => {
  it('reads Net Revenue from revenue_ex_gst, not what was paid', () => {
    const [row] = mapRevenue({
      total_revenue: 2230, total_tax: 230, total_discount: 0, revenue_ex_gst: 2000,
    });
    expect(row.net_revenue).toBe(2000);
    expect(row.gst_collected).toBe(230);
    expect(row.gross_sales).toBe(2230);
  });

  it('takes the GST off when an older server sends no revenue_ex_gst', () => {
    const [row] = mapRevenue({ total_revenue: 2230, total_tax: 230, total_discount: 0 });
    expect(row.net_revenue).toBe(2000);
  });

  it('labels both revenue figures excl. GST', () => {
    render(
      <FinanceSummary
        revenueData={mapRevenue({ total_revenue: 2230, total_tax: 230, revenue_ex_gst: 2000 })}
        plStatement={{
          revenue: 2000, cost_of_goods: 1200, gross_profit: 800, operating_expenses: 0,
          operating_profit: 800, tax_expense: 0, net_profit: 800, profit_margin: 40,
          period_start: '2026-10-01', period_end: '2026-10-21',
        }}
      />,
    );
    expect(screen.getByText('Net Revenue (excl. GST)')).toBeTruthy();
    expect(screen.getByText('Revenue (excl. GST)')).toBeTruthy();
    expect(screen.getByText('Gross Sales (incl. GST)')).toBeTruthy();
  });
});
