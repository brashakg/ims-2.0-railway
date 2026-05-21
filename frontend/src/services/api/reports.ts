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

  // Phase 6.3 follow-up (May-2026) — wire the 6 reports that had backend
  // handlers but no frontend method, so the cards were always empty.

  getStaffRanking: async (storeId: string | undefined, fromDate: string, toDate: string) => {
    const r = await api.get('/reports/staff/ranking', {
      params: { from_date: fromDate, to_date: toDate, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      data: Array<{
        staff_id: string; staff_name: string;
        total_sales: number; order_count: number; avg_bill: number;
      }>;
    };
  },

  getStockCount: async (storeId: string | undefined, fromDate: string, toDate: string) => {
    const r = await api.get('/reports/stock/count', {
      params: { from_date: fromDate, to_date: toDate, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      data: Array<{ category: string; item_count: number; total_quantity: number; total_value: number }>;
      summary: { total_items: number; total_quantity: number; total_value: number };
    };
  },

  getBrandSellthrough: async (storeId: string | undefined, fromDate: string, toDate: string) => {
    const r = await api.get('/reports/inventory/brand-sellthrough', {
      params: { from_date: fromDate, to_date: toDate, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      data: Array<{
        brand: string; quantity_sold: number; revenue: number;
        avg_price: number; sellthrough_percent: number;
      }>;
      summary: { total_brands: number; total_quantity_sold: number; total_revenue: number };
    };
  },

  getCustomerAcquisition: async (storeId: string | undefined, fromDate: string, toDate: string) => {
    const r = await api.get('/reports/customers/acquisition', {
      params: { from_date: fromDate, to_date: toDate, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      new_customers: number;
      returning_customers: number;
      total_customers: number;
      retention_percent: number;
    };
  },

  getDiscountAnalysis: async (storeId: string | undefined, fromDate: string, toDate: string) => {
    const r = await api.get('/reports/discount/analysis', {
      params: { from_date: fromDate, to_date: toDate, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      by_category: Array<{
        category: string; total_discount: number; total_revenue: number;
        total_items: number; avg_discount_percent: number;
      }>;
      summary: { total_discount: number; total_revenue: number; discount_percent: number };
    };
  },

  getExpenseVsRevenue: async (storeId: string | undefined, fromDate: string, toDate: string) => {
    const r = await api.get('/reports/finance/expense-vs-revenue', {
      params: { from_date: fromDate, to_date: toDate, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      revenue: number; cost: number; profit: number; margin_percent: number;
    };
  },

  getSalesComparison: async (storeId: string | undefined, fromDate: string, toDate: string) => {
    const r = await api.get('/reports/sales/comparison', {
      params: { from_date: fromDate, to_date: toDate, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      current_period: { sales: number; orders: number; avg_order_value: number };
      previous_period: { sales: number; orders: number; avg_order_value: number };
      comparison: { sales_change_percent: number; sales_change_amount: number; order_change: number };
    };
  },

  // TechCherry R1 — 4 net-new analytics dimensions.
  // Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.

  getFootfallAudit: async (storeId?: string, monthsBack = 12) => {
    const r = await api.get('/reports/walkouts/footfall-audit', {
      params: { months_back: monthsBack, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      store_id: string;
      period_start: string;
      period_end: string;
      months: Array<{
        month: string;
        walkins_total: number;
        walkouts_total: number;
        walkouts_converted: number;
        orders_total: number;
        hidden_sales: number;
        hidden_sales_pct: number;
        staff_reported_conversion_pct: number;
        true_conversion_pct: number;
      }>;
      rolling: {
        walkins_total: number;
        walkouts_total: number;
        walkouts_converted: number;
        orders_total: number;
        hidden_sales: number;
        hidden_sales_pct: number;
        staff_reported_conversion_pct: number;
        true_conversion_pct: number;
      };
    };
  },

  getPriceBands: async (storeId?: string, fyCount = 3, trendBands = 4) => {
    const r = await api.get('/reports/sales/price-bands', {
      params: { fy_count: fyCount, trend_bands: trendBands, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      store_id: string;
      bands: string[];
      fy_count: number;
      by_fy: Array<{
        fy: string;
        invoices_by_band: number[];
        revenue_by_band: number[];
        atv_by_band: number[];
      }>;
      trend_bands: string[];
      monthly_trend_by_band: Record<string, Array<{ month: string; revenue: number; invoices: number }>>;
      movement_summary: {
        premiumized_pct: number;
        stable_pct: number;
        downgraded_pct: number;
        compared_customers: number;
      };
      total_orders: number;
    };
  },

  getLensDeepDive: async (storeId?: string, monthsBack = 12) => {
    const r = await api.get('/reports/sales/lens-deep-dive', {
      params: { months_back: monthsBack, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      store_id: string;
      period_start: string;
      period_end: string;
      totals: {
        lens_units: number;
        lens_revenue: number;
        atv: number;
        contact_lens_units: number;
        contact_lens_revenue: number;
      };
      by_brand: Array<{ brand: string; units: number; revenue: number; share: number }>;
      by_type: Array<{ type: string; units: number; revenue: number }>;
      by_coating: Array<{ coating: string; units: number; revenue: number }>;
      by_refractive_index: Array<{ index: string; units: number; revenue: number }>;
      parse_rate: number;
      metadata_pending: boolean;
    };
  },

  getSeasonality: async (storeId?: string, yearsBack = 2) => {
    const r = await api.get('/reports/sales/seasonality', {
      params: { years_back: yearsBack, ...(storeId ? { store_id: storeId } : {}) },
    });
    return r.data as {
      store_id: string;
      years_back: number;
      day_of_week: Array<{ dow: string; invoices: number; revenue: number; atv: number }>;
      month_of_year: Array<{ month: string; invoices: number; revenue: number; atv: number }>;
      peak_dow: string | null;
      trough_dow: string | null;
      peak_month: string | null;
      trough_month: string | null;
      peak_dow_lift_pct: number;
      total_orders: number;
    };
  },

  // R2 — Purchase Recommendations
  // Spec: docs/TECHCHERRY_PORT_SCOPE.md §6
  getPurchaseRecommendations: async (
    storeId?: string,
    opts?: {
      lookback_days?: number;
      lead_time_days?: number;
      reorder_cycle_days?: number;
      safety_buffer_days?: number;
      min_velocity?: number;
      limit?: number;
    }
  ) => {
    const r = await api.get('/reports/purchase/recommendations', {
      params: {
        ...(opts ?? {}),
        ...(storeId ? { store_id: storeId } : {}),
      },
    });
    return r.data as {
      recommendations: Array<{
        product_id: string;
        name: string;
        brand: string;
        category: string;
        velocity_90d: number;
        daily_velocity: number;
        current_stock: number;
        reorder_point: number;
        desired_cover: number;
        gap_units: number;
        suggested_order_qty: number;
        avg_selling_price: number;
        cost_price: number;
        unit_margin: number;
        estimated_revenue_impact: number;
        estimated_purchase_cost: number;
        estimated_margin: number;
        confidence: 'HIGH' | 'MEDIUM' | 'LOW';
        reason: string;
      }>;
      by_category: Array<{
        category: string;
        count: number;
        suggested_units: number;
        estimated_revenue_impact: number;
      }>;
      summary: {
        total_recommendations: number;
        total_suggested_units: number;
        estimated_revenue_at_risk: number;
        estimated_purchase_cost: number;
        estimated_margin?: number;
      };
      params: {
        store_id: string;
        lookback_days: number;
        lead_time_days: number;
        reorder_cycle_days: number;
        safety_buffer_days: number;
        cover_days_total: number;
        min_velocity: number;
      };
      as_of: string;
    };
  },

  // R3 — Growth Blueprint (LLM-narrated)
  getGrowthBlueprint: async (
    storeId?: string,
    opts?: { model_id?: string; nocache?: boolean },
  ) => {
    const r = await api.get('/reports/blueprint', {
      params: {
        ...(storeId ? { store_id: storeId } : {}),
        ...(opts?.model_id ? { model_id: opts.model_id } : {}),
        ...(opts?.nocache ? { nocache: true } : {}),
      },
      // LLM call can take 30-90s — bump axios timeout
      timeout: 180_000,
    });
    return r.data as {
      narrative_markdown: string;
      sections: string[];
      model_used: string | null;
      store_id: string;
      month: string;
      generated_at: string;
      from_cache: boolean;
      cache_age_hours?: number;
      error?: string;
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
