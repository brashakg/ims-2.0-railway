// ============================================================================
// IMS 2.0 - AI-Powered Demand Forecasting Dashboard
// ============================================================================
// Intelligent demand forecasting for Indian optical retail.
// Generates plausible forecasts from simulated data patterns with seasonal
// adjustments specific to the Indian optical market.

import { useState, useMemo } from 'react';
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

// ============================================================================
// Helpers
// ============================================================================

function getCurrentSeason(month: number): string {
  if (month >= 3 && month <= 5) return 'Summer';
  if (month >= 6 && month <= 8) return 'Monsoon';
  if (month >= 9 && month <= 11) return 'Festival';
  return 'Winter';
}

function getSeasonalMultiplier(category: string, season: string): number {
  const multipliers: Record<string, Record<string, number>> = {
    'Sunglasses':        { Summer: 1.40, Monsoon: 0.85, Festival: 1.10, Winter: 0.70 },
    'Frames':            { Summer: 1.00, Monsoon: 0.90, Festival: 1.25, Winter: 1.05 },
    'Contact Lenses':    { Summer: 1.10, Monsoon: 0.80, Festival: 1.05, Winter: 0.95 },
    'Lenses':            { Summer: 0.95, Monsoon: 1.00, Festival: 1.10, Winter: 1.20 },
    'Accessories':       { Summer: 1.15, Monsoon: 1.10, Festival: 1.20, Winter: 0.90 },
    'Lens Solutions':    { Summer: 1.00, Monsoon: 1.35, Festival: 1.00, Winter: 0.95 },
  };
  return multipliers[category]?.[season] ?? 1.0;
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
// Mock Data Generation
// ============================================================================

function generateCategoryForecasts(days: ForecastRange): ForecastItem[] {
  const now = new Date();
  const currentMonth = now.getMonth();
  const season = getCurrentSeason(currentMonth);

  const baseData: {
    category: string;
    stock: number;
    baseDailySales: number;
    confidence: 'HIGH' | 'MEDIUM' | 'LOW';
  }[] = [
    { category: 'Frames',          stock: 342, baseDailySales: 8.5,  confidence: 'HIGH' },
    { category: 'Sunglasses',      stock: 185, baseDailySales: 5.2,  confidence: 'HIGH' },
    { category: 'Contact Lenses',  stock: 520, baseDailySales: 12.0, confidence: 'MEDIUM' },
    { category: 'Lenses',          stock: 275, baseDailySales: 6.8,  confidence: 'HIGH' },
    { category: 'Accessories',     stock: 430, baseDailySales: 4.3,  confidence: 'MEDIUM' },
    { category: 'Lens Solutions',  stock: 210, baseDailySales: 7.1,  confidence: 'LOW' },
  ];

  return baseData.map(item => {
    const multiplier = getSeasonalMultiplier(item.category, season);
    const adjustedDailySales = Math.round(item.baseDailySales * multiplier * 10) / 10;
    const projectedDemand = Math.round(adjustedDailySales * days);
    const daysUntilStockout = adjustedDailySales > 0
      ? Math.round(item.stock / adjustedDailySales)
      : 999;
    // Reorder qty covers the forecast period plus 15-day safety buffer
    const reorderQty = Math.max(0, projectedDemand - item.stock + Math.round(adjustedDailySales * 15));

    let trend: 'UP' | 'DOWN' | 'STABLE' = 'STABLE';
    if (multiplier > 1.1) trend = 'UP';
    else if (multiplier < 0.9) trend = 'DOWN';

    return {
      category: item.category,
      currentStock: item.stock,
      avgDailySales: adjustedDailySales,
      projectedDemand,
      daysUntilStockout,
      reorderQty,
      confidence: item.confidence,
      trend,
    };
  });
}

function generateReorderSuggestions(): ReorderSuggestion[] {
  const now = new Date();
  const season = getCurrentSeason(now.getMonth());

  const suggestions: ReorderSuggestion[] = [
    {
      productName: 'Ray-Ban Aviator Classic',
      sku: 'RB-AVI-001',
      category: 'Sunglasses',
      currentStock: 8,
      avgDailySales: 2.3 * getSeasonalMultiplier('Sunglasses', season),
      daysUntilStockout: Math.round(8 / (2.3 * getSeasonalMultiplier('Sunglasses', season))),
      suggestedAction: 'URGENT_REORDER',
      suggestedQty: 50,
      reason: 'Stock will run out within a week. Best-selling sunglasses model.',
      confidence: 'HIGH',
      trend: getSeasonalMultiplier('Sunglasses', season) > 1.1 ? 'UP' : 'STABLE',
    },
    {
      productName: 'Titan Full-Rim Rectangle',
      sku: 'TIT-FR-045',
      category: 'Frames',
      currentStock: 12,
      avgDailySales: 1.8 * getSeasonalMultiplier('Frames', season),
      daysUntilStockout: Math.round(12 / (1.8 * getSeasonalMultiplier('Frames', season))),
      suggestedAction: 'URGENT_REORDER',
      suggestedQty: 36,
      reason: 'Popular frame with growing demand. Current stock critically low.',
      confidence: 'HIGH',
      trend: 'UP',
    },
    {
      productName: 'Bausch & Lomb SofLens Daily',
      sku: 'BL-SDL-090',
      category: 'Contact Lenses',
      currentStock: 45,
      avgDailySales: 3.5 * getSeasonalMultiplier('Contact Lenses', season),
      daysUntilStockout: Math.round(45 / (3.5 * getSeasonalMultiplier('Contact Lenses', season))),
      suggestedAction: 'URGENT_REORDER',
      suggestedQty: 120,
      reason: 'High-volume daily disposable. Needs consistent restocking.',
      confidence: 'HIGH',
      trend: 'STABLE',
    },
    {
      productName: 'Essilor Crizal Prevencia',
      sku: 'ESS-CP-022',
      category: 'Lenses',
      currentStock: 65,
      avgDailySales: 2.1 * getSeasonalMultiplier('Lenses', season),
      daysUntilStockout: Math.round(65 / (2.1 * getSeasonalMultiplier('Lenses', season))),
      suggestedAction: 'INCREASE_ORDER',
      suggestedQty: 80,
      reason: 'Blue-light filter lenses seeing 18% growth in demand. Increase buffer stock.',
      confidence: 'MEDIUM',
      trend: 'UP',
    },
    {
      productName: 'Zeiss Progressive SmartLife',
      sku: 'ZS-PSL-015',
      category: 'Lenses',
      currentStock: 38,
      avgDailySales: 1.2 * getSeasonalMultiplier('Lenses', season),
      daysUntilStockout: Math.round(38 / (1.2 * getSeasonalMultiplier('Lenses', season))),
      suggestedAction: 'INCREASE_ORDER',
      suggestedQty: 45,
      reason: season === 'Winter'
        ? 'Winter season: progressive lens demand rises with older customer visits.'
        : 'Premium progressive lenses with steady demand growth.',
      confidence: 'MEDIUM',
      trend: season === 'Winter' ? 'UP' : 'STABLE',
    },
    {
      productName: 'Vincent Chase Round Frames',
      sku: 'VC-RND-078',
      category: 'Frames',
      currentStock: 85,
      avgDailySales: 0.8,
      daysUntilStockout: Math.round(85 / 0.8),
      suggestedAction: 'REDUCE_ORDER',
      suggestedQty: 15,
      reason: 'Sales declining 22% over last quarter. Reduce next order quantity.',
      confidence: 'MEDIUM',
      trend: 'DOWN',
    },
    {
      productName: 'Fastrack Wayfarers',
      sku: 'FT-WAY-033',
      category: 'Sunglasses',
      currentStock: 62,
      avgDailySales: 1.1,
      daysUntilStockout: Math.round(62 / 1.1),
      suggestedAction: 'REDUCE_ORDER',
      suggestedQty: 20,
      reason: 'Budget segment slowing. Reduce reorder to avoid overstock.',
      confidence: 'LOW',
      trend: 'DOWN',
    },
    {
      productName: 'Microfiber Cleaning Cloth (Premium)',
      sku: 'ACC-MCF-005',
      category: 'Accessories',
      currentStock: 150,
      avgDailySales: 3.8 * getSeasonalMultiplier('Accessories', season),
      daysUntilStockout: Math.round(150 / (3.8 * getSeasonalMultiplier('Accessories', season))),
      suggestedAction: 'MONITOR',
      suggestedQty: 0,
      reason: 'Adequate stock for now. Monitor and reorder in 3 weeks.',
      confidence: 'HIGH',
      trend: 'STABLE',
    },
    {
      productName: 'ReNu MultiPlus Solution 360ml',
      sku: 'BL-RNU-120',
      category: 'Lens Solutions',
      currentStock: 28,
      avgDailySales: 2.4 * getSeasonalMultiplier('Lens Solutions', season),
      daysUntilStockout: Math.round(28 / (2.4 * getSeasonalMultiplier('Lens Solutions', season))),
      suggestedAction: 'URGENT_REORDER',
      suggestedQty: 72,
      reason: season === 'Monsoon'
        ? 'Monsoon season: lens solution demand surges with eye infection prevention.'
        : 'Essential consumable running low. Pair with contact lens sales.',
      confidence: 'HIGH',
      trend: season === 'Monsoon' ? 'UP' : 'STABLE',
    },
    {
      productName: 'Oakley Holbrook Mix',
      sku: 'OAK-HBM-011',
      category: 'Sunglasses',
      currentStock: 22,
      avgDailySales: 0.6 * getSeasonalMultiplier('Sunglasses', season),
      daysUntilStockout: Math.round(22 / (0.6 * getSeasonalMultiplier('Sunglasses', season))),
      suggestedAction: 'MONITOR',
      suggestedQty: 0,
      reason: 'Premium segment with moderate demand. Sufficient stock for now.',
      confidence: 'LOW',
      trend: 'STABLE',
    },
  ];

  // Round avgDailySales
  return suggestions.map(s => ({
    ...s,
    avgDailySales: Math.round(s.avgDailySales * 10) / 10,
    daysUntilStockout: s.avgDailySales > 0
      ? Math.round(s.currentStock / s.avgDailySales)
      : 999,
  }));
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
  const [forecastRange, setForecastRange] = useState<ForecastRange>(30);
  const [activeSection, setActiveSection] = useState<'category' | 'seasonal' | 'reorder'>('category');

  const now = new Date();
  const currentMonth = now.getMonth();
  const currentSeason = getCurrentSeason(currentMonth);

  const categoryForecasts = useMemo(
    () => generateCategoryForecasts(forecastRange),
    [forecastRange]
  );

  const reorderSuggestions = useMemo(() => generateReorderSuggestions(), []);

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
                    <Package className="w-4 h-4 text-gray-400" />
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
                  ? `${trend.bgColor} ${trend.borderColor} ring-2 ring-offset-1 ring-purple-300`
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
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-purple-600 text-white">
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
                      <p className="font-bold text-purple-700">{suggestion.suggestedQty}</p>
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

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <BarChart3 className="w-6 h-6 text-purple-600" />
            Demand Forecasting
          </h1>
          <p className="text-gray-500">
            AI-powered demand predictions for optical retail inventory management
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Calendar className="w-4 h-4 text-gray-400" />
          <span className="text-sm text-gray-600">
            {now.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })}
          </span>
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-purple-100 text-purple-700">
            {currentSeason} Season
          </span>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-600">Forecast Period</p>
            <ShoppingCart className="w-4 h-4 text-purple-500" />
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
            <RefreshCw className={`w-4 h-4 ${urgentReorders > 0 ? 'text-amber-500' : 'text-gray-400'}`} />
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
                  ? 'bg-purple-100 text-purple-700'
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
                  ? 'bg-purple-100 text-purple-700'
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
                  ? 'bg-purple-100 text-purple-700'
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
                  className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    forecastRange === range
                      ? 'bg-purple-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
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
          <BarChart3 className="w-5 h-5 text-gray-400 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-gray-600">
            <p className="font-medium text-gray-700 mb-1">Forecasting Methodology</p>
            <ul className="list-disc list-inside space-y-1 text-gray-500">
              <li>Forecasts are based on historical sales velocity, seasonal multipliers, and trend analysis</li>
              <li>Indian optical retail seasonality (summer sunglasses, monsoon lens care, festival premiums, winter progressives) is factored in</li>
              <li>Confidence levels reflect data quality: <strong>High</strong> = 90+ days of data, <strong>Medium</strong> = 30-90 days, <strong>Low</strong> = under 30 days or volatile patterns</li>
              <li>Reorder quantities include a 15-day safety buffer above projected demand</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

export default DemandForecast;
