// ============================================================================
// IMS 2.0 - Reports section data via React Query
// ============================================================================
// Wave 2 split of the old ReportsPage tab container. Copies the Wave 1
// template (pages/purchase/purchaseQueries.ts): ONE cache shared by the
// layout and every section page (5-min staleTime from the app QueryClient),
// so switching sections renders instantly from cache instead of refetching.
//
// The point of the split: the old page fired 16 requests on mount for EVERY
// tab. Now the layout fetches the two the KPI strip needs, and each section
// page fetches only its own. Opening Reports to read GST no longer runs a
// 90-day non-moving-stock scan and the purchase-recommendation engine.
//
// Nothing here changes a request's URL or params — the calls are the same
// reportsApi calls the old page made, moved behind useQuery.

import { useQuery } from '@tanstack/react-query';
import { reportsApi } from '../../services/api';

export type ReportSection = 'sales' | 'inventory' | 'customers' | 'gst' | 'forecast';
export type DateRange = 'today' | 'week' | 'month' | 'quarter' | 'custom';

export interface SalesSummary {
  totalSales: number;
  orderCount: number;
  averageOrderValue: number;
  topCategory: string;
  grossProfit: number;
  gstCollected: number;
}

export interface CategoryBreakdown {
  category: string;
  sales: number;
  units: number;
  percentage: number;
}

export interface DailyTrend {
  date: string;
  sales: number;
}

/** Verbatim from the old ReportsPage.getDateRange(). */
export function resolveDateRange(dateRange: DateRange): { startDate: string; endDate: string } {
  const now = new Date();
  const endDate = now.toISOString().split('T')[0];
  let startDate: string;

  switch (dateRange) {
    case 'today':
      startDate = endDate;
      break;
    case 'week': {
      const weekAgo = new Date(now);
      weekAgo.setDate(weekAgo.getDate() - 7);
      startDate = weekAgo.toISOString().split('T')[0];
      break;
    }
    case 'month': {
      const monthAgo = new Date(now);
      monthAgo.setMonth(monthAgo.getMonth() - 1);
      startDate = monthAgo.toISOString().split('T')[0];
      break;
    }
    case 'quarter': {
      const quarterAgo = new Date(now);
      quarterAgo.setMonth(quarterAgo.getMonth() - 3);
      startDate = quarterAgo.toISOString().split('T')[0];
      break;
    }
    default:
      startDate = endDate;
  }

  return { startDate, endDate };
}

type Period = { storeId: string | undefined; startDate: string; endDate: string };

const key = (name: string, { storeId, startDate, endDate }: Period) =>
  ['reports', name, storeId ?? 'none', startDate, endDate] as const;

/** Sales summary + the two series the Sales section draws from it. */
export function useSalesSummary(p: Period) {
  return useQuery({
    queryKey: key('sales-summary', p),
    enabled: !!p.storeId,
    queryFn: async () => {
      const response = await reportsApi.getSalesSummary(p.storeId as string, p.startDate, p.endDate);
      return {
        summary: {
          totalSales: response?.summary?.total_sales || 0,
          orderCount: response?.summary?.total_orders || 0,
          averageOrderValue: response?.summary?.avg_order_value || 0,
          topCategory: '-', // Not available from backend summary
          grossProfit: 0, // Not available from backend summary
          gstCollected: response?.summary?.total_tax || 0,
        } as SalesSummary,
        categoryBreakdown: (response?.categoryBreakdown ?? []) as CategoryBreakdown[],
        dailyTrend: (response?.dailyTrend ?? []) as DailyTrend[],
      };
    },
  });
}

/** MoM / YoY — always for the CURRENT calendar month, as the old page did. */
export function useSalesGrowth(storeId: string | undefined) {
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth() + 1;
  return useQuery({
    queryKey: ['reports', 'sales-growth', storeId ?? 'none', year, month] as const,
    enabled: !!storeId,
    queryFn: () => reportsApi.getSalesGrowth(storeId, year, month),
  });
}

export function useStaffRanking(p: Period) {
  return useQuery({
    queryKey: key('staff-ranking', p),
    enabled: !!p.storeId,
    queryFn: async () => (await reportsApi.getStaffRanking(p.storeId, p.startDate, p.endDate)).data ?? [],
  });
}

export function useStockCount(p: Period) {
  return useQuery({
    queryKey: key('stock-count', p),
    enabled: !!p.storeId,
    queryFn: () => reportsApi.getStockCount(p.storeId, p.startDate, p.endDate),
  });
}

export function useBrandSellthrough(p: Period) {
  return useQuery({
    queryKey: key('brand-sellthrough', p),
    enabled: !!p.storeId,
    queryFn: async () => (await reportsApi.getBrandSellthrough(p.storeId, p.startDate, p.endDate)).data ?? [],
  });
}

export function useCustomerAcquisition(p: Period) {
  return useQuery({
    queryKey: key('customer-acquisition', p),
    enabled: !!p.storeId,
    queryFn: () => reportsApi.getCustomerAcquisition(p.storeId, p.startDate, p.endDate),
  });
}

export function useDiscountAnalysis(p: Period) {
  return useQuery({
    queryKey: key('discount-analysis', p),
    enabled: !!p.storeId,
    queryFn: () => reportsApi.getDiscountAnalysis(p.storeId, p.startDate, p.endDate),
  });
}

export function useExpenseVsRevenue(p: Period) {
  return useQuery({
    queryKey: key('expense-vs-revenue', p),
    enabled: !!p.storeId,
    queryFn: () => reportsApi.getExpenseVsRevenue(p.storeId, p.startDate, p.endDate),
  });
}

/** Non-moving stock is period-independent (fixed 90-day window), as before. */
export function useNonMovingStock(storeId: string | undefined) {
  return useQuery({
    queryKey: ['reports', 'non-moving-stock', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async () => (await reportsApi.getNonMovingStock(storeId, 90, 200)).data ?? [],
  });
}

/** Purchase recommendations are period-independent too (90-day velocity). */
export function usePurchaseRecommendations(storeId: string | undefined) {
  return useQuery({
    queryKey: ['reports', 'purchase-recommendations', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: () => reportsApi.getPurchaseRecommendations(storeId, { limit: 50 }),
  });
}
