// ============================================================================
// IMS 2.0 - AI-Powered Demand Forecasting Dashboard
// ============================================================================
// Demand forecasting for Indian optical retail.
// Forecasts are computed from the real GET /analytics-v2/demand-forecast endpoint
// (90-day sales velocity per store). The Seasonal Trends panel is a static planning
// reference, not applied to the numeric forecasts.
//
// Wave 6 B13: the three panels (category / seasonal / reorder) used to sit
// behind this one URL in useState. They are child routes of /reports/forecast
// now (pages/reports/ForecastSections.tsx). This component is the shell: the
// one forecast load, the header, the summary cards, the panel nav and the
// methodology note - and <Outlet context> where the active panel was.

import { useState, useMemo, useEffect, useCallback } from 'react';
import { NavLink, Outlet, useMatch, useOutletContext } from 'react-router-dom';
import {
  TrendingUp,
  AlertTriangle,
  Sun,
  Package,
  ShoppingCart,
  BarChart3,
  RefreshCw,
  Calendar,
} from 'lucide-react';
import { analyticsV2Api } from '../../services/api/analytics';
import { useAuth } from '../../context/AuthContext';

// ============================================================================
// Types
// ============================================================================

export interface ForecastItem {
  category: string;
  currentStock: number;
  avgDailySales: number;
  projectedDemand: number;
  daysUntilStockout: number;
  reorderQty: number;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  trend: 'UP' | 'DOWN' | 'STABLE';
}

export interface ReorderSuggestion {
  productName: string;
  sku: string;
  category: string;
  currentStock: number;
  avgDailySales: number;
  daysUntilStockout: number;
  suggestedAction: 'URGENT_REORDER' | 'INCREASE_ORDER' | 'REDUCE_ORDER' | 'MONITOR';
  suggestedQty: number;
  reason: string;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  trend: 'UP' | 'DOWN' | 'STABLE';
}

export type ForecastRange = 30 | 60 | 90;

// Shape returned by GET /analytics-v2/demand-forecast (one row per top product).
interface ApiForecast {
  product_id: string;
  product_name: string;
  brand: string;
  category: string;
  avg_daily_sales: number;
  trend: 'increasing' | 'decreasing' | 'stable';
  predicted_30_day: number;
  current_stock: number;
  reorder_recommended: number;
}

function mapTrend(t: string): 'UP' | 'DOWN' | 'STABLE' {
  return t === 'increasing' ? 'UP' : t === 'decreasing' ? 'DOWN' : 'STABLE';
}

/** What the shell hands its panel pages through <Outlet context>: the rows
 *  each old render closure closed over. */
export interface ForecastOutletContext {
  forecastRange: ForecastRange;
  currentSeason: string;
  categoryForecasts: ForecastItem[];
  reorderSuggestions: ReorderSuggestion[];
}

export const useForecastContext = () => useOutletContext<ForecastOutletContext>();

// The panel tab's look, unchanged; `isActive` is what `activeSection === x` was.
const tabClass = ({ isActive }: { isActive: boolean }) =>
  `px-4 py-2 rounded-lg font-medium text-sm transition-colors flex items-center gap-2 ${
    isActive
      ? 'bg-bv-red-50 text-bv-red-700'
      : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
  }`;

// ============================================================================
// Helpers
// ============================================================================

function getCurrentSeason(month: number): string {
  if (month >= 3 && month <= 5) return 'Summer';
  if (month >= 6 && month <= 8) return 'Monsoon';
  if (month >= 9 && month <= 11) return 'Festival';
  return 'Winter';
}

// ============================================================================
// Derivations from the real /demand-forecast API response
// ============================================================================

function deriveCategoryForecasts(apiForecasts: ApiForecast[], days: ForecastRange): ForecastItem[] {
  const byCat: Record<
    string,
    { stock: number; daily: number; up: number; down: number; n: number }
  > = {};
  for (const f of apiForecasts) {
    const cat = f.category || 'Uncategorised';
    const c = (byCat[cat] ??= { stock: 0, daily: 0, up: 0, down: 0, n: 0 });
    c.stock += f.current_stock || 0;
    c.daily += f.avg_daily_sales || 0;
    if (f.trend === 'increasing') c.up += 1;
    else if (f.trend === 'decreasing') c.down += 1;
    c.n += 1;
  }
  return Object.entries(byCat)
    .map(([category, c]) => {
      const avgDailySales = Math.round(c.daily * 10) / 10;
      const projectedDemand = Math.round(c.daily * days);
      const daysUntilStockout = c.daily > 0 ? Math.round(c.stock / c.daily) : 999;
      // Reorder qty covers the forecast period plus a 15-day safety buffer.
      const reorderQty = Math.max(0, projectedDemand - c.stock + Math.round(c.daily * 15));
      const trend: 'UP' | 'DOWN' | 'STABLE' =
        c.up > c.down ? 'UP' : c.down > c.up ? 'DOWN' : 'STABLE';
      const confidence: 'HIGH' | 'MEDIUM' | 'LOW' =
        c.n >= 5 ? 'HIGH' : c.n >= 2 ? 'MEDIUM' : 'LOW';
      return {
        category,
        currentStock: c.stock,
        avgDailySales,
        projectedDemand,
        daysUntilStockout,
        reorderQty,
        confidence,
        trend,
      };
    })
    .sort((a, b) => b.projectedDemand - a.projectedDemand);
}

function deriveReorderSuggestions(apiForecasts: ApiForecast[]): ReorderSuggestion[] {
  const actionOrder: Record<ReorderSuggestion['suggestedAction'], number> = {
    URGENT_REORDER: 0,
    INCREASE_ORDER: 1,
    MONITOR: 2,
    REDUCE_ORDER: 3,
  };
  return apiForecasts
    .map((f) => {
      const avgDailySales = Math.round((f.avg_daily_sales || 0) * 10) / 10;
      const currentStock = f.current_stock || 0;
      const daysUntilStockout = avgDailySales > 0 ? Math.round(currentStock / avgDailySales) : 999;
      const trend = mapTrend(f.trend);
      const reorderQty = f.reorder_recommended || 0;

      let suggestedAction: ReorderSuggestion['suggestedAction'];
      let reason: string;
      if (reorderQty > 0 && daysUntilStockout <= 7) {
        suggestedAction = 'URGENT_REORDER';
        reason = `Projected to stock out in ~${daysUntilStockout} day(s); reorder ${reorderQty} to cover 30-day demand.`;
      } else if (trend === 'UP' && reorderQty > 0) {
        suggestedAction = 'INCREASE_ORDER';
        reason = `Demand trending up; current stock covers ~${daysUntilStockout} day(s).`;
      } else if (trend === 'DOWN') {
        suggestedAction = 'REDUCE_ORDER';
        reason = `Demand trending down; ~${daysUntilStockout} day(s) of stock on hand.`;
      } else {
        suggestedAction = 'MONITOR';
        reason = `Stable demand; ~${daysUntilStockout} day(s) of stock on hand.`;
      }

      const confidence: 'HIGH' | 'MEDIUM' | 'LOW' =
        avgDailySales >= 2 ? 'HIGH' : avgDailySales >= 0.5 ? 'MEDIUM' : 'LOW';

      return {
        productName: f.product_name || f.product_id,
        sku: f.product_id,
        category: f.category || 'Uncategorised',
        currentStock,
        avgDailySales,
        daysUntilStockout,
        suggestedAction,
        suggestedQty: reorderQty,
        reason,
        confidence,
        trend,
      };
    })
    .sort(
      (a, b) =>
        actionOrder[a.suggestedAction] - actionOrder[b.suggestedAction] ||
        a.daysUntilStockout - b.daysUntilStockout
    );
}

// ============================================================================
// Component
// ============================================================================

export function DemandForecast() {
  const { user } = useAuth();
  const isSuperadmin = (user?.roles || []).includes('SUPERADMIN');
  const [forecastRange, setForecastRange] = useState<ForecastRange>(30);
  // The range picker belongs to the category panel alone - the index route.
  const onCategory = useMatch('/reports/forecast') !== null;
  const [apiForecasts, setApiForecasts] = useState<ApiForecast[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadForecast = useCallback(async () => {
    if (!isSuperadmin) return;
    setLoading(true);
    setError(null);
    try {
      const res = await analyticsV2Api.getDemandForecast({ store_id: user?.activeStoreId || undefined });
      setApiForecasts(Array.isArray(res?.forecasts) ? res.forecasts : []);
    } catch {
      setError('Failed to load demand forecast');
      setApiForecasts([]);
    } finally {
      setLoading(false);
    }
  }, [isSuperadmin, user?.activeStoreId]);

  useEffect(() => {
    loadForecast();
  }, [loadForecast]);

  const now = new Date();
  const currentMonth = now.getMonth();
  const currentSeason = getCurrentSeason(currentMonth);

  const categoryForecasts = useMemo(
    () => deriveCategoryForecasts(apiForecasts, forecastRange),
    [apiForecasts, forecastRange]
  );

  const reorderSuggestions = useMemo(
    () => deriveReorderSuggestions(apiForecasts),
    [apiForecasts]
  );

  // Summary stats
  const totalProjectedDemand = categoryForecasts.reduce((sum, f) => sum + f.projectedDemand, 0);
  const categoriesAtRisk = categoryForecasts.filter(f => f.projectedDemand > f.currentStock).length;
  const urgentReorders = reorderSuggestions.filter(s => s.suggestedAction === 'URGENT_REORDER').length;

  // ------------------------------------------------------------------
  // Main render
  // ------------------------------------------------------------------

  if (!isSuperadmin) {
    return (
      <div className="card text-center text-gray-500 py-10">
        Demand forecasting is available to Superadmin only.
      </div>
    );
  }
  if (loading && apiForecasts.length === 0) {
    return <div className="card text-center text-gray-500 py-10">Loading demand forecast…</div>;
  }

  const ctx: ForecastOutletContext = { forecastRange, currentSeason, categoryForecasts, reorderSuggestions };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <BarChart3 className="w-6 h-6 text-bv-red-600" />
            Demand Forecasting
          </h1>
          <p className="text-gray-500">
            AI-powered demand predictions for optical retail inventory management
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Calendar className="w-4 h-4 text-gray-500" />
          <span className="text-sm text-gray-600">
            {now.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })}
          </span>
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-bv-red-50 text-bv-red-700">
            {currentSeason} Season
          </span>
          <button
            onClick={loadForecast}
            disabled={loading}
            className="flex items-center gap-1 px-2 py-1 rounded-lg text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="card bg-red-50 border-red-200 text-sm text-red-700">{error}</div>
      )}
      {!error && !loading && apiForecasts.length === 0 && (
        <div className="card bg-gray-50 border-gray-200 text-sm text-gray-600">
          No sales in the last 90 days for this store yet — forecasts will appear once there is order history.
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-600">Forecast Period</p>
            <ShoppingCart className="w-4 h-4 text-bv-red-500" />
          </div>
          <p className="text-2xl font-bold text-gray-900">{forecastRange} Days</p>
          <p className="text-xs text-gray-500 mt-1">
            {new Date(now.getTime() + forecastRange * 86400000).toLocaleDateString('en-IN', {
              day: 'numeric',
              month: 'short',
            })}
          </p>
        </div>
        <div className="card">
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-600">Total Projected Demand</p>
            <TrendingUp className="w-4 h-4 text-blue-500" />
          </div>
          <p className="text-2xl font-bold text-gray-900">
            {totalProjectedDemand.toLocaleString('en-IN')}
          </p>
          <p className="text-xs text-gray-500 mt-1">units across all categories</p>
        </div>
        <div className={`card ${categoriesAtRisk > 0 ? 'bg-red-50 border-red-200' : 'bg-green-50 border-green-200'}`}>
          <div className="flex items-center justify-between">
            <p className={`text-sm ${categoriesAtRisk > 0 ? 'text-red-600' : 'text-green-600'}`}>
              Categories at Risk
            </p>
            <AlertTriangle className={`w-4 h-4 ${categoriesAtRisk > 0 ? 'text-red-500' : 'text-green-500'}`} />
          </div>
          <p className={`text-2xl font-bold ${categoriesAtRisk > 0 ? 'text-red-900' : 'text-green-900'}`}>
            {categoriesAtRisk} / {categoryForecasts.length}
          </p>
          <p className={`text-xs mt-1 ${categoriesAtRisk > 0 ? 'text-red-600' : 'text-green-600'}`}>
            {categoriesAtRisk > 0 ? 'will stock out in forecast period' : 'all categories sufficiently stocked'}
          </p>
        </div>
        <div className={`card ${urgentReorders > 0 ? 'bg-amber-50 border-amber-200' : ''}`}>
          <div className="flex items-center justify-between">
            <p className={`text-sm ${urgentReorders > 0 ? 'text-amber-600' : 'text-gray-600'}`}>
              Urgent Reorders
            </p>
            <RefreshCw className={`w-4 h-4 ${urgentReorders > 0 ? 'text-amber-500' : 'text-gray-500'}`} />
          </div>
          <p className={`text-2xl font-bold ${urgentReorders > 0 ? 'text-amber-900' : 'text-gray-900'}`}>
            {urgentReorders}
          </p>
          <p className={`text-xs mt-1 ${urgentReorders > 0 ? 'text-amber-600' : 'text-gray-500'}`}>
            products need immediate reorder
          </p>
        </div>
      </div>

      {/* Time Range Selector + Section Tabs */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row tablet:items-center justify-between gap-4 pb-4 border-b border-gray-200">
          {/* Section tabs */}
          <div className="flex border-b-0 gap-1">
            <NavLink to="/reports/forecast" end className={tabClass}>
              <Package className="w-4 h-4" />
              Category Forecast
            </NavLink>
            <NavLink to="/reports/forecast/seasonal" className={tabClass}>
              <Sun className="w-4 h-4" />
              Seasonal Trends
            </NavLink>
            <NavLink to="/reports/forecast/reorder" className={tabClass}>
              <ShoppingCart className="w-4 h-4" />
              Reorder Suggestions
              {urgentReorders > 0 && (
                <span className="bg-red-500 text-white text-xs font-bold px-1.5 py-0.5 rounded-full">
                  {urgentReorders}
                </span>
              )}
            </NavLink>
          </div>

          {/* Time range selector (only for category forecast) */}
          {onCategory && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-500">Forecast:</span>
              {([30, 60, 90] as ForecastRange[]).map((range) => (
                <button
                  key={range}
                  onClick={() => setForecastRange(range)}
                  className={forecastRange === range ? 'ims-chip ims-chip--on' : 'ims-chip'}
                >
                  {range} Days
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="p-4">
          <Outlet context={ctx} />
        </div>
      </div>

      {/* Methodology Note */}
      <div className="card bg-gray-50 border-gray-200">
        <div className="flex gap-3">
          <BarChart3 className="w-5 h-5 text-gray-500 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-gray-600">
            <p className="font-medium text-gray-700 mb-1">Forecasting Methodology</p>
            <ul className="list-disc list-inside space-y-1 text-gray-500">
              <li>Forecasts are computed from this store's real sales over the last 90 days (per-product daily velocity, projected forward).</li>
              <li>Trend compares the most recent 45 days against the prior 45 days.</li>
              <li>Confidence reflects how many products contributed: <strong>High</strong> = 5+, <strong>Medium</strong> = 2-4, <strong>Low</strong> = 1.</li>
              <li>Reorder quantities include a 15-day safety buffer above projected demand.</li>
              <li>The Seasonal Trends tab is a static planning reference for Indian optical retail and is <strong>not</strong> applied to the numeric forecasts above.</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

export default DemandForecast;
