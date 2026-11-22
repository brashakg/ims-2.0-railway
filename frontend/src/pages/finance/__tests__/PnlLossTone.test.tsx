// ============================================================================
// IMS 2.0 - P&L statement: a loss reads as a loss (audit L3-11)
// ============================================================================
// The Net Profit box was green whatever its sign, so -Rs 40,226.72 looked like
// a good month while the same shop's row in the store table was red.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import FinanceSummary from '../FinanceSummary';
import type { ProfitLossStatement } from '../financeTypes';

const statement = (net: number, gross: number): ProfitLossStatement =>
  ({
    revenue: 1000,
    cost_of_goods: 1000 - gross,
    gross_profit: gross,
    operating_expenses: 500,
    tax_expense: 0,
    net_profit: net,
    profit_margin: (net / 1000) * 100,
  }) as unknown as ProfitLossStatement;

const box = (label: string) => screen.getAllByText(label).map((el) => el.closest('div') as HTMLElement).pop()!;

describe('P&L statement colours follow the sign', () => {
  it('shows a negative net profit and gross profit in red, never green', () => {
    render(<FinanceSummary revenueData={[]} plStatement={statement(-40226.72, -50)} />);
    const net = box('Net Loss');
    expect(net.className).not.toMatch(/green/);
    expect(net.className).toMatch(/red/);
    expect(net.textContent).toMatch(/Net Loss/);
    const gross = box('Gross Profit');
    expect(gross.innerHTML).not.toMatch(/text-green/);
    expect(gross.innerHTML).toMatch(/text-red/);
  });

  it('keeps a positive result green', () => {
    render(<FinanceSummary revenueData={[]} plStatement={statement(300, 600)} />);
    expect(box('Net Profit').className).toMatch(/green/);
    expect(box('Gross Profit').innerHTML).toMatch(/text-green/);
  });
});
