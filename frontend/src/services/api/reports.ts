// ============================================================================
// IMS 2.0 - Reports API
// ============================================================================

import api from './client';

export const reportsApi = {
  getSalesSummary: async (storeId: string, startDate: string, endDate: string) => {
    const response = await api.get('/reports/sales/summary', {
      params: { store_id: storeId, from_date: startDate, to_date: endDate },
    });
    return response.data;
  },

  getDashboardStats: async (storeId: string) => {
    const response = await api.get('/reports/dashboard', { params: { store_id: storeId } });
    return response.data;
  },

  getInventoryReport: async (storeId: string) => {
    const response = await api.get('/reports/inventory', { params: { store_id: storeId } });
    return response.data;
  },

  getTargets: async (storeId?: string) => {
    const response = await api.get('/reports/targets', {
      params: storeId ? { store_id: storeId } : {}
    });
    return response.data;
  },

  getGSTR1Report: async (month: string, storeId?: string) => {
    const response = await api.get('/reports/gstr1', {
      params: { month, ...(storeId ? { store_id: storeId } : {}) },
    });
    return response.data;
  },

  getGSTR3BReport: async (month: string, storeId?: string) => {
    const response = await api.get('/reports/gstr3b', {
      params: { month, ...(storeId ? { store_id: storeId } : {}) },
    });
    return response.data;
  },

  // Phase 6.3 — cash-tied-up-in-shelves report
  getNonMovingStock: async (
    storeId?: string,
    days: number = 90,
    limit: number = 200,
  ) => {
    const response = await api.get('/reports/inventory/non-moving-stock', {
      params: { days, limit, ...(storeId ? { store_id: storeId } : {}) },
    });
    return response.data as {
      data: Array<{
        product_id: string | null;
        sku: string | null;
        brand: string | null;
        model: string | null;
        category: string | null;
        mrp: number;
        last_sold_at: string | null;
        days_since_sold: number | null;
        never_sold: boolean;
        total_sold_all_time: number;
      }>;
      count: number;
      as_of: string;
      days_threshold: number;
      store_id: string | null;
    };
  },

  // Phase 6.3 — MoM / YoY growth (endpoint already existed, surfacing it)
  getSalesGrowth: async (storeId: string | undefined, year: number, month: number) => {
    const response = await api.get('/reports/sales/growth', {
      params: { year, month, ...(storeId ? { store_id: storeId } : {}) },
    });
    return response.data as {
      current_month: { sales: number; orders: number };
      mom_growth: { percent: number; previous_month_sales: number };
      yoy_growth: { percent: number; previous_year_sales: number };
    };
  },
};

// ============================================================================
// Analytics API - Enterprise Dashboard
// ============================================================================

export const analyticsApi = {
  getDashboardSummary: async (period: string = 'month') => {
    const response = await api.get('/analytics/dashboard-summary', { params: { period } });
    return response.data;
  },

  getRevenueTrends: async (period: string = 'daily', days: number = 30) => {
    const response = await api.get('/analytics/revenue-trends', { params: { period, days } });
    return response.data;
  },

  getStorePerformance: async (period: string = 'month') => {
    const response = await api.get('/analytics/store-performance', { params: { period } });
    return response.data;
  },

  getInventoryIntelligence: async () => {
    const response = await api.get('/analytics/inventory-intelligence');
    return response.data;
  },

  getCustomerInsights: async (period: string = 'month') => {
    const response = await api.get('/analytics/customer-insights', { params: { period } });
    return response.data;
  },
};
