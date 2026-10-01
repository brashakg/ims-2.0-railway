// Review round 4, item 1: the purchase-recommendations table shades a row red
// only when the SERVER says low_stock - it never compares stock with the level.
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../ReportsLayout', () => ({
  useReportsContext: () => ({
    storeId: 'BV-DHN-02', dateRange: 'today', startDate: '2026-09-01', endDate: '2026-09-06', canExport: false,
  }),
}));
vi.mock('../ReportCardsGrid', () => ({ ReportCardsGrid: () => null }));
vi.mock('../sections/TaxCodeAuditCard', () => ({ TaxCodeAuditCard: () => null }));
vi.mock('../sections/WorkshopProductivityCard', () => ({ WorkshopProductivityCard: () => null }));

const row = (id: string, stock: number, level: number | null, low: boolean) => ({
  product_id: id, name: id, brand: 'Carrera', category: 'FR', velocity_90d: 9, daily_velocity: 1,
  current_stock: stock, reorder_point: level, low_stock: low, stock_status: low ? 'low' : 'healthy',
  desired_cover: 14, gap_units: 1, suggested_order_qty: 3, avg_selling_price: 1, cost_price: 1,
  unit_margin: 1, estimated_revenue_impact: 1000, estimated_purchase_cost: 1, estimated_margin: 1,
  confidence: 'HIGH', reason: '',
});
vi.mock('../reportsQueries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../reportsQueries')>()),
  useStockCount: () => ({ isPending: false, data: undefined }),
  useBrandSellthrough: () => ({ isPending: false, data: [] }),
  useNonMovingStock: () => ({ isPending: false, data: [] }),
  usePurchaseRecommendations: () => ({
    isPending: false,
    data: {
      summary: { total_recommendations: 2, total_suggested_units: 6, estimated_revenue_at_risk: 0 },
      // The server says A is NOT low although stock <= level numerically
      // (it is a sold-out discontinued twin, say); B is low although stock > level.
      recommendations: [row('A-NOT-LOW', 5, 5, false), row('B-LOW', 9, 5, true)],
    },
  }),
}));

import { ReportsInventoryPage } from '../ReportsInventoryPage';

describe('purchase recommendations table', () => {
  it('shades by the server low_stock flag, not by comparing stock with the level', () => {
    render(<MemoryRouter><ReportsInventoryPage /></MemoryRouter>);
    const cellOf = (name: string) =>
      screen.getByText(name, { exact: false }).closest('tr')!.querySelector('td.right span') as HTMLElement;
    expect(cellOf('A-NOT-LOW').className).not.toContain('text-red-600');
    expect(cellOf('B-LOW').className).toContain('text-red-600');
  });
});
