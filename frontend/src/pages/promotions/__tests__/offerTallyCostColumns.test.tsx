// ============================================================================
// Offer Tally shows no cost column to a manager (owner ruling 2026-10-12)
// ============================================================================
// The server drops the cost of goods and the margin for anyone outside
// COST_ROLES. The screen must not then show the managers a margin tile, cost
// columns and an "estimated cost" banner full of false Rs 0s.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' }));
const report = vi.hoisted(() => vi.fn());
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles: [auth.role] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../services/api/promotions', () => ({ promotionsApi: { report } }));

import PromotionsReportPage from '../PromotionsReportPage';

const ROW = { promo_id: 'PR1', promo_name: 'Monsoon 20', promo_type: 'PERCENT', orders_count: 3, total_discount_given: 1500, cogs_is_estimated: true };
const SUMMARY = { total_discount_given: 1500, orders_with_promos: 3, promos_fired: 1, any_cogs_estimated: true };

describe('Offer Tally', () => {
  it.each(['STORE_MANAGER', 'AREA_MANAGER'])('shows %s the discounts and no cost column', async (role) => {
    auth.role = role;
    report.mockResolvedValue({ summary: SUMMARY, promos: [ROW] }); // as the server masks it
    render(<PromotionsReportPage />);
    expect(await screen.findByText('Monsoon 20')).toBeTruthy();
    expect(screen.getAllByText(/1,500/).length).toBeGreaterThan(0);
    expect(screen.queryByText('Est. COGS')).toBeNull();
    expect(screen.queryByText('Net margin impact')).toBeNull();
    expect(screen.queryByText(/estimated cost of goods/i)).toBeNull();
    expect(screen.queryByText(/estimated COGS/)).toBeNull();
    expect(screen.queryByText('Rs 0')).toBeNull();
  });

  it.each(['ADMIN', 'ACCOUNTANT'])('keeps the cost and margin for %s', async (role) => {
    auth.role = role;
    report.mockResolvedValue({
      summary: { ...SUMMARY, net_margin_impact: -432 },
      promos: [{ ...ROW, estimated_cogs: 4321, net_margin_after_promo: -432 }],
    });
    render(<PromotionsReportPage />);
    expect(await screen.findByText('Monsoon 20')).toBeTruthy();
    expect(screen.getByText('Est. COGS')).toBeTruthy();
    expect(screen.getAllByText('Net margin impact').length).toBe(2);
    expect(screen.getByText(/Rs 4,321/)).toBeTruthy();
  });
});
