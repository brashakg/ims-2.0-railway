// Audit F47: stock with no cost is counted, not valued at Rs 0. The Stock
// Summary's 'Total Value (at cost)' adds such units at nothing, so the card
// says how many there are (GET /reports/stock/count summary.uncosted_units).
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

let summary: Record<string, number | null> = {};

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../ReportsLayout', () => ({
  useReportsContext: () => ({
    storeId: 'BV-DHN-01', dateRange: 'today', startDate: '2026-09-01', endDate: '2026-09-06', canExport: false,
  }),
}));
vi.mock('../ReportCardsGrid', () => ({ ReportCardsGrid: () => null }));
vi.mock('../sections/TaxCodeAuditCard', () => ({ TaxCodeAuditCard: () => null }));
vi.mock('../sections/WorkshopProductivityCard', () => ({ WorkshopProductivityCard: () => null }));
vi.mock('../reportsQueries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../reportsQueries')>()),
  useStockCount: () => ({ isPending: false, data: { data: [], summary } }),
  useBrandSellthrough: () => ({ isPending: false, data: [] }),
  useNonMovingStock: () => ({ isPending: false, data: [] }),
  usePurchaseRecommendations: () => ({ isPending: false, data: undefined }),
}));

import { ReportsInventoryPage } from '../ReportsInventoryPage';

const show = () => render(<MemoryRouter><ReportsInventoryPage /></MemoryRouter>);

describe('Stock Summary card', () => {
  it('says how many units have no cost beside the value at cost', () => {
    summary = { total_items: 5, total_quantity: 5, total_value: 3000, uncosted_units: 3 };
    show();
    expect(screen.getByText('3 units have no cost')).toBeInTheDocument();
  });

  it('says nothing when every unit has a cost', () => {
    summary = { total_items: 3, total_quantity: 3, total_value: 3000, uncosted_units: 0 };
    show();
    expect(screen.queryByText(/no cost/)).toBeNull();
  });
});
