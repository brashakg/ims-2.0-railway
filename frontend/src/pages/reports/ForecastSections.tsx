// ============================================================================
// IMS 2.0 - Reports > Forecast: the three panels, one URL each
// ============================================================================
// Wave 6 B13: DemandForecast kept its three panels (category / seasonal /
// reorder) behind one URL in useState. Each panel is a page now:
//   /reports/forecast (Category Forecast, the index) ·
//   /reports/forecast/seasonal · /reports/forecast/reorder
// DemandForecast (components/reports) is the shell: it owns the one forecast
// load, the header, the summary cards and the panel nav, and hands the derived
// rows to these pages through <Outlet context>. The panel bodies below are the
// old render closures, moved byte-identical; a page reads off the context what
// the closure used to close over.

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
  Clock,
  ShieldCheck,
  ShieldAlert,
  Shield,
} from 'lucide-react';
import { exportToCSV } from '../../utils/exportUtils';
import { useForecastContext, type ReorderSuggestion } from '../../components/reports/DemandForecast';

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
// /reports/forecast - Category Demand Forecast
// ============================================================================

export function ForecastCategorySection() {
  const { forecastRange, currentSeason, categoryForecasts } = useForecastContext();

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

  return (
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
}

// ============================================================================
// /reports/forecast/seasonal - Seasonal Trends
// ============================================================================

export function ForecastSeasonalSection() {
  const { currentSeason } = useForecastContext();

  return (
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
}

// ============================================================================
// /reports/forecast/reorder - Reorder Suggestions
// ============================================================================

export function ForecastReorderSection() {
  const { currentSeason, reorderSuggestions } = useForecastContext();

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
}
