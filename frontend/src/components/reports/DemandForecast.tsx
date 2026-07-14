// ============================================================================
// IMS 2.0 - AI-Powered Demand Forecasting Dashboard
// ============================================================================
// Demand forecasting for Indian optical retail.
// Forecasts are computed from the real GET /analytics-v2/demand-forecast endpoint
// (90-day sales velocity per store). The Seasonal Trends tab is a static planning
// reference, not applied to the numeric forecasts.

import { useState, useMemo, useEffect, useCallback } from 'react';
import {
  TrendingUp,
  TrendingDown,
  Minus,
  AlertTriangle,
  Sun,
  CloudRain,
  PartyPopper,
  Snowflake,
  Download,
  Package,
  ShoppingCart,
  BarChart3,
  Clock,
  ShieldCheck,
  ShieldAlert,
  Shield,
  RefreshCw,
  Calendar,
} from 'lucide-react';
import { exportToCSV } from '../../utils/exportUtils';
import { analyticsV2Api } from '../../services/api/analytics';
import { useAuth } from '../../context/AuthContext';

// ============================================================================
// Types
// ============================================================================

interface ForecastItem {
  category: string;
  currentStock: number;
  avgDailySales: number;
  projectedDemand: number;
  daysUntilStockout: number;
  reorderQty: number;
  confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  trend: 'UP' | 'DOWN' | 'STABLE';
}

interface SeasonalTrend {
  season: string;
  months: string;
  monthRange: [number, number]; // 0-indexed month start/end
  icon: React.ReactNode;
  color: string;
  bgColor: string;
  borderColor: string;
  description: string;
  impactedProducts: { name: string; change: string; direction: 'UP' | 'DOWN' | 'STABLE' }[];
}

interface ReorderSuggestion {
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

type ForecastRange = 30 | 60 | 90;

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

// ============================================================================
// Helpers
// ============================================================================

function getCurrentSeason(month: number): string {
  if (month >= 3 && month <= 5) return 'Summer';
  if (month >= 6 && month <= 8) return 'Monsoon';
  if (month >= 9 && month <= 11) return 'Festival';
  return 'Winter';
}

function getConfidenceBadge(confidence: 'HIGH' | 'MEDIUM' | 'LOW') {
  switch (confidence) {
    case 'HIGH':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
          <ShieldCheck className="w-3 h-3" /> High
        </span>
      );
    case 'MEDIUM':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800">
          <Shield className="w-3 h-3" /> Medium
        </span>
      );
    case 'LOW':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
          <ShieldAlert className="w-3 h-3" /> Low
        </span>
      );
  }
}

function getTrendIcon(trend: 'UP' | 'DOWN' | 'STABLE') {
  switch (trend) {
    case 'UP':
      return <TrendingUp className="w-4 h-4 text-green-600" />;
    case 'DOWN':
      return <TrendingDown className="w-4 h-4 text-red-600" />;
    case 'STABLE':
      return <Minus className="w-4 h-4 text-gray-500" />;
  }
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
// Seasonal Trends Data
// ============================================================================

const SEASONAL_TRENDS: SeasonalTrend[] = [
  {
    season: 'Summer',
    months: 'April - June',
    monthRange: [3, 5],
    icon: <Sun className="w-5 h-5" />,
    color: 'text-orange-700',
    bgColor: 'bg-orange-50',
    borderColor: 'border-orange-200',
    description: 'Peak sunglasses season. UV protection drives demand. Outdoor activities increase footfall.',
    impactedProducts: [
      { name: 'Sunglasses (all segments)', change: '+40%', direction: 'UP' },
      { name: 'Polarized lenses', change: '+25%', direction: 'UP' },
      { name: 'UV-coating accessories', change: '+15%', direction: 'UP' },
      { name: 'Prescription sunglasses', change: '+20%', direction: 'UP' },
    ],
  },
  {
    season: 'Monsoon',
    months: 'July - September',
    monthRange: [6, 8],
    icon: <CloudRain className="w-5 h-5" />,
    color: 'text-blue-700',
    bgColor: 'bg-blue-50',
    borderColor: 'border-blue-200',
    description: 'Eye infections rise. Contact lens solution demand spikes. Anti-fog coatings popular.',
    impactedProducts: [
      { name: 'Contact lens solutions', change: '+35%', direction: 'UP' },
      { name: 'Anti-fog lens coatings', change: '+20%', direction: 'UP' },
      { name: 'Sunglasses', change: '-15%', direction: 'DOWN' },
      { name: 'Eye drops & care', change: '+30%', direction: 'UP' },
    ],
  },
  {
    season: 'Festival',
    months: 'October - December',
    monthRange: [9, 11],
    icon: <PartyPopper className="w-5 h-5" />,
    color: 'text-purple-700',
    bgColor: 'bg-purple-50',
    borderColor: 'border-purple-200',
    description: 'Diwali, Navratri, Christmas drive premium purchases. Gift-buying increases AOV.',
    impactedProducts: [
      { name: 'Premium designer frames', change: '+25%', direction: 'UP' },
      { name: 'Branded sunglasses', change: '+15%', direction: 'UP' },
      { name: 'Gift sets & bundles', change: '+30%', direction: 'UP' },
      { name: 'Gold/titanium frames', change: '+20%', direction: 'UP' },
    ],
  },
  {
    season: 'Winter',
    months: 'January - March',
    monthRange: [0, 2],
    icon: <Snowflake className="w-5 h-5" />,
    color: 'text-cyan-700',
    bgColor: 'bg-cyan-50',
    borderColor: 'border-cyan-200',
    description: 'Progressive lenses peak for older customers. New year health checkups drive eye exams.',
    impactedProducts: [
      { name: 'Progressive lenses', change: '+20%', direction: 'UP' },
      { name: 'Reading glasses', change: '+15%', direction: 'UP' },
      { name: 'Blue-light filter lenses', change: '+18%', direction: 'UP' },
      { name: 'Sunglasses', change: '-30%', direction: 'DOWN' },
    ],
  },
];

// ============================================================================
// Component
// ============================================================================

export function DemandForecast() {
  const { user } = useAuth();
  const isSuperadmin = (user?.roles || []).includes('SUPERADMIN');
  const [forecastRange, setForecastRange] = useState<ForecastRange>(30);
  const [activeSection, setActiveSection] = useState<'category' | 'seasonal' | 'reorder'>('category');
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
  // Export handlers
  // ------------------------------------------------------------------

  const handleExportCategoryForecast = () => {
    const data = categoryForecasts.map(f => ({
      category: f.category,
      currentStock: f.currentStock,
      avgDailySales: f.avgDailySales,
      projectedDemand: f.projectedDemand,
      daysUntilStockout: f.daysUntilStockout,
      reorderQty: f.reorderQty,
      confidence: f.confidence,
      trend: f.trend,
    }));

    exportToCSV(data, `demand_forecast_${forecastRange}d`, [
      { key: 'category', label: 'Category' },
      { key: 'currentStock', label: 'Current Stock' },
      { key: 'avgDailySales', label: 'Avg Daily Sales' },
      { key: 'projectedDemand', label: `Projected Demand (${forecastRange}d)` },
      { key: 'daysUntilStockout', label: 'Days Until Stockout' },
      { key: 'reorderQty', label: 'Recommended Reorder Qty' },
      { key: 'confidence', label: 'Confidence' },
      { key: 'trend', label: 'Trend' },
    ]);
  };

  const handleExportReorderSuggestions = () => {
    const data = reorderSuggestions.map(s => ({
      productName: s.productName,
      sku: s.sku,
      category: s.category,
      currentStock: s.currentStock,
      avgDailySales: s.avgDailySales,
      daysUntilStockout: s.daysUntilStockout,
      suggestedAction: s.suggestedAction.replace(/_/g, ' '),
      suggestedQty: s.suggestedQty,
      reason: s.reason,
      confidence: s.confidence,
      trend: s.trend,
    }));

    exportToCSV(data, 'reorder_suggestions', [
      { key: 'productName', label: 'Product Name' },
      { key: 'sku', label: 'SKU' },
      { key: 'category', label: 'Category' },
      { key: 'currentStock', label: 'Current Stock' },
      { key: 'avgDailySales', label: 'Avg Daily Sales' },
      { key: 'daysUntilStockout', label: 'Days Until Stockout' },
      { key: 'suggestedAction', label: 'Suggested Action' },
      { key: 'suggestedQty', label: 'Suggested Qty' },
      { key: 'reason', label: 'Reason' },
      { key: 'confidence', label: 'Confidence' },
      { key: 'trend', label: 'Trend' },
    ]);
  };

  // ------------------------------------------------------------------
  // Section: Category Demand Forecast
  // ------------------------------------------------------------------

  const renderCategoryForecast = () => (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-600">
          Projected demand for next <strong>{forecastRange} days</strong> based on historical
          sales patterns and seasonal adjustments ({currentSeason} season).
        </p>
        <button
          onClick={handleExportCategoryForecast}
          className="btn-outline text-sm flex items-center gap-2"
        >
          <Download className="w-4 h-4" />
          Export CSV
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-gray-100 border-b border-gray-200">
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-700">Category</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Current Stock</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Avg Daily Sales</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Projected Demand</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Days to Stockout</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-700">Reorder Qty</th>
              <th className="px-4 py-3 text-center text-sm font-medium text-gray-700">Trend</th>
              <th className="px-4 py-3 text-center text-sm font-medium text-gray-700">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {categoryForecasts.map((item, idx) => {
              const willStockOut = item.projectedDemand > item.currentStock;
              return (
                <tr
                  key={idx}
                  className={`border-b border-gray-100 hover:bg-gray-50 ${
                    willStockOut ? 'bg-red-50' : ''
                  }`}
                >
                  <td className="px-4 py-3 text-sm font-medium text-gray-900 flex items-center gap-2">
                    <Package className="w-4 h-4 text-gray-500" />
                    {item.category}
                    {willStockOut && (
                      <AlertTriangle className="w-4 h-4 text-red-500" />
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-right text-gray-900">
                    {item.currentStock.toLocaleString('en-IN')}
                  </td>
                  <td className="px-4 py-3 text-sm text-right text-gray-600">
                    {item.avgDailySales}
                  </td>
                  <td className={`px-4 py-3 text-sm text-right font-medium ${
                    willStockOut ? 'text-red-700' : 'text-gray-900'
                  }`}>
                    {item.projectedDemand.toLocaleString('en-IN')}
                  </td>
                  <td className={`px-4 py-3 text-sm text-right font-medium ${
                    item.daysUntilStockout <= 14
                      ? 'text-red-700'
                      : item.daysUntilStockout <= 30
                        ? 'text-yellow-700'
                        : 'text-green-700'
                  }`}>
                    {item.daysUntilStockout} days
                  </td>
                  <td className="px-4 py-3 text-sm text-right text-gray-900 font-medium">
                    {item.reorderQty > 0 ? item.reorderQty.toLocaleString('en-IN') : '--'}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="flex items-center justify-center gap-1">
                      {getTrendIcon(item.trend)}
                      <span className="text-xs text-gray-500">{item.trend}</span>
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    {getConfidenceBadge(item.confidence)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-6 text-xs text-gray-500 pt-2 border-t border-gray-100">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded bg-red-100 border border-red-300" />
          Will stock out within forecast period
        </span>
        <span className="flex items-center gap-1">
          <AlertTriangle className="w-3 h-3 text-red-500" />
          Demand exceeds current stock
        </span>
        <span>Reorder Qty includes 15-day safety buffer</span>
      </div>
    </div>
  );

  // ------------------------------------------------------------------
  // Section: Seasonal Trends
  // ------------------------------------------------------------------

  const renderSeasonalTrends = () => (
    <div className="space-y-4">
      <p className="text-sm text-gray-600">
        Seasonal demand patterns for the Indian optical retail market. Products are adjusted
        based on weather, festivals, and consumer behaviour cycles.
      </p>

      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        {SEASONAL_TRENDS.map((trend) => {
          const isActive = trend.season === currentSeason;
          return (
            <div
              key={trend.season}
              className={`rounded-lg border-2 p-5 transition-all ${
                isActive
                  ? `${trend.bgColor} ${trend.borderColor} ring-2 ring-offset-1 ring-bv-red-300`
                  : 'bg-white border-gray-200'
              }`}
            >
              {/* Season header */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className={isActive ? trend.color : 'text-gray-500'}>
                    {trend.icon}
                  </span>
                  <h3 className={`text-lg font-semibold ${isActive ? trend.color : 'text-gray-800'}`}>
                    {trend.season}
                  </h3>
                  <span className="text-xs text-gray-500">({trend.months})</span>
                </div>
                {isActive && (
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-bv-red-600 text-white">
                    <Clock className="w-3 h-3" />
                    CURRENT
                  </span>
                )}
              </div>

              <p className={`text-sm mb-3 ${isActive ? trend.color : 'text-gray-600'}`}>
                {trend.description}
              </p>

              {/* Product impacts */}
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
                  Products to Stock
                </p>
                {trend.impactedProducts.map((product, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between text-sm"
                  >
                    <span className={isActive ? 'text-gray-900 font-medium' : 'text-gray-700'}>
                      {product.name}
                    </span>
                    <span className={`font-medium flex items-center gap-1 ${
                      product.direction === 'UP'
                        ? 'text-green-700'
                        : product.direction === 'DOWN'
                          ? 'text-red-600'
                          : 'text-gray-500'
                    }`}>
                      {getTrendIcon(product.direction)}
                      {product.change}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );

  // ------------------------------------------------------------------
  // Section: Reorder Suggestions
  // ------------------------------------------------------------------

  const getActionBadge = (action: ReorderSuggestion['suggestedAction']) => {
    switch (action) {
      case 'URGENT_REORDER':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-800">
            <AlertTriangle className="w-3 h-3" /> Urgent Reorder
          </span>
        );
      case 'INCREASE_ORDER':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-800">
            <TrendingUp className="w-3 h-3" /> Increase Order
          </span>
        );
      case 'REDUCE_ORDER':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold bg-blue-100 text-blue-800">
            <TrendingDown className="w-3 h-3" /> Reduce Order
          </span>
        );
      case 'MONITOR':
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold bg-gray-100 text-gray-700">
            <Clock className="w-3 h-3" /> Monitor
          </span>
        );
    }
  };

  const renderReorderSuggestions = () => {
    // Sort: urgent first, then increase, reduce, monitor
    const actionOrder: Record<string, number> = {
      URGENT_REORDER: 0,
      INCREASE_ORDER: 1,
      REDUCE_ORDER: 2,
      MONITOR: 3,
    };
    const sorted = [...reorderSuggestions].sort(
      (a, b) => actionOrder[a.suggestedAction] - actionOrder[b.suggestedAction]
    );

    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-600">
            Smart reorder recommendations based on current stock levels, sales velocity,
            and seasonal demand for the {currentSeason} season.
          </p>
          <button
            onClick={handleExportReorderSuggestions}
            className="btn-outline text-sm flex items-center gap-2"
          >
            <Download className="w-4 h-4" />
            Export CSV
          </button>
        </div>

        <div className="space-y-3">
          {sorted.map((suggestion, idx) => (
            <div
              key={idx}
              className={`rounded-lg border p-4 ${
                suggestion.suggestedAction === 'URGENT_REORDER'
                  ? 'border-red-200 bg-red-50'
                  : suggestion.suggestedAction === 'INCREASE_ORDER'
                    ? 'border-amber-200 bg-amber-50'
                    : 'border-gray-200 bg-white'
              }`}
            >
              <div className="flex flex-col tablet:flex-row tablet:items-start gap-3">
                {/* Product info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h4 className="font-medium text-gray-900">{suggestion.productName}</h4>
                    <span className="text-xs font-mono text-gray-500">{suggestion.sku}</span>
                    {getActionBadge(suggestion.suggestedAction)}
                    {getConfidenceBadge(suggestion.confidence)}
                  </div>
                  <p className="text-sm text-gray-600 mt-1">{suggestion.reason}</p>
                </div>

                {/* Metrics */}
                <div className="flex items-center gap-6 text-sm flex-shrink-0">
                  <div className="text-center">
                    <p className="text-xs text-gray-500">Stock</p>
                    <p className="font-semibold text-gray-900">{suggestion.currentStock}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-xs text-gray-500">Daily Sales</p>
                    <p className="font-semibold text-gray-900">{suggestion.avgDailySales}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-xs text-gray-500">Days Left</p>
                    <p className={`font-semibold ${
                      suggestion.daysUntilStockout <= 7
                        ? 'text-red-700'
                        : suggestion.daysUntilStockout <= 14
                          ? 'text-yellow-700'
                          : 'text-green-700'
                    }`}>
                      {suggestion.daysUntilStockout}
                    </p>
                  </div>
                  <div className="text-center">
                    <p className="text-xs text-gray-500">Trend</p>
                    <span className="flex items-center justify-center">
                      {getTrendIcon(suggestion.trend)}
                    </span>
                  </div>
                  {suggestion.suggestedQty > 0 && (
                    <div className="text-center">
                      <p className="text-xs text-gray-500">Order Qty</p>
                      <p className="font-bold text-bv-red-700">{suggestion.suggestedQty}</p>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  };

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
            <button
              onClick={() => setActiveSection('category')}
              className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors flex items-center gap-2 ${
                activeSection === 'category'
                  ? 'bg-bv-red-50 text-bv-red-700'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
            >
              <Package className="w-4 h-4" />
              Category Forecast
            </button>
            <button
              onClick={() => setActiveSection('seasonal')}
              className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors flex items-center gap-2 ${
                activeSection === 'seasonal'
                  ? 'bg-bv-red-50 text-bv-red-700'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
            >
              <Sun className="w-4 h-4" />
              Seasonal Trends
            </button>
            <button
              onClick={() => setActiveSection('reorder')}
              className={`px-4 py-2 rounded-lg font-medium text-sm transition-colors flex items-center gap-2 ${
                activeSection === 'reorder'
                  ? 'bg-bv-red-50 text-bv-red-700'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
            >
              <ShoppingCart className="w-4 h-4" />
              Reorder Suggestions
              {urgentReorders > 0 && (
                <span className="bg-red-500 text-white text-xs font-bold px-1.5 py-0.5 rounded-full">
                  {urgentReorders}
                </span>
              )}
            </button>
          </div>

          {/* Time range selector (only for category forecast) */}
          {activeSection === 'category' && (
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
          {activeSection === 'category' && renderCategoryForecast()}
          {activeSection === 'seasonal' && renderSeasonalTrends()}
          {activeSection === 'reorder' && renderReorderSuggestions()}
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
