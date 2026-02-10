// ============================================================================
// IMS 2.0 - Inventory Valuation Report
// ============================================================================
// Detailed inventory valuation methods: FIFO, LIFO, Weighted Average, ABC

import { Package, Download, Printer, BarChart3 } from 'lucide-react';
import clsx from 'clsx';

export type ValuationMethod = 'FIFO' | 'LIFO' | 'WeightedAvg' | 'ABC';

interface InventoryItem {
  sku: string;
  productName: string;
  category: string;
  quantity: number;
  unitPrice: number;
  fifoValue: number;
  lifoValue: number;
  weightedAvgValue: number;
  abcCategory: 'A' | 'B' | 'C';
  turnoverRatio: number;
  daysInStock: number;
  stockStatus: 'healthy' | 'slow' | 'dead';
}

interface InventoryValuationData {
  period: string;
  valuationMethod: ValuationMethod;
  items: InventoryItem[];
  totals: {
    totalQuantity: number;
    fifoTotal: number;
    lifoTotal: number;
    weightedAvgTotal: number;
  };
  analysis: {
    categoryWiseValue: { category: string; value: number; percentage: number }[];
    abcAnalysis: { category: 'A' | 'B' | 'C'; count: number; value: number; percentage: number }[];
    stockHealth: { healthy: number; slow: number; dead: number };
  };
}

interface InventoryValuationReportProps {
  data: InventoryValuationData;
  selectedMethod?: ValuationMethod;
  onMethodChange?: (method: ValuationMethod) => void;
  onExport?: () => void;
  onPrint?: () => void;
}

export function InventoryValuationReport({
  data,
  selectedMethod = 'FIFO',
  onMethodChange,
  onExport,
  onPrint,
}: InventoryValuationReportProps) {
  const formatCurrency = (amount: number) =>
    new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      minimumFractionDigits: 2,
    }).format(amount);

  const getValuationTotal = () => {
    switch (selectedMethod) {
      case 'FIFO':
        return data.totals.fifoTotal;
      case 'LIFO':
        return data.totals.lifoTotal;
      case 'WeightedAvg':
        return data.totals.weightedAvgTotal;
      case 'ABC':
        return data.totals.weightedAvgTotal; // Use weighted average for ABC
      default:
        return data.totals.fifoTotal;
    }
  };

  const getStockStatusColor = (status: string) => {
    switch (status) {
      case 'healthy':
        return 'bg-green-100 text-green-800';
      case 'slow':
        return 'bg-yellow-100 text-yellow-800';
      case 'dead':
        return 'bg-red-100 text-red-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  const getAbcCategoryLabel = (category: 'A' | 'B' | 'C') => {
    switch (category) {
      case 'A':
        return 'Fast Moving (80/20)';
      case 'B':
        return 'Medium Moving (15/30)';
      case 'C':
        return 'Slow Moving (5/50)';
    }
  };

  const getItemValue = (item: InventoryItem): number => {
    switch (selectedMethod) {
      case 'FIFO':
        return item.fifoValue;
      case 'LIFO':
        return item.lifoValue;
      case 'WeightedAvg':
        return item.weightedAvgValue;
      case 'ABC':
        return item.weightedAvgValue;
      default:
        return item.fifoValue;
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Inventory Valuation Report</h2>
          <p className="text-sm text-gray-500 mt-1">{data.period}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onPrint}
            className="px-4 py-2 bg-gray-600 hover:bg-gray-700 text-white rounded-lg flex items-center gap-2 transition-colors"
          >
            <Printer className="w-4 h-4" />
            Print
          </button>
          <button
            onClick={onExport}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg flex items-center gap-2 transition-colors"
          >
            <Download className="w-4 h-4" />
            Export
          </button>
        </div>
      </div>

      {/* Valuation Method Selector */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-4">
        <h3 className="font-semibold text-gray-900">Valuation Method</h3>
        <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3">
          {['FIFO', 'LIFO', 'WeightedAvg', 'ABC'].map((method) => (
            <button
              key={method}
              onClick={() => onMethodChange?.(method as ValuationMethod)}
              className={clsx(
                'px-4 py-2 rounded-lg font-medium transition-colors',
                selectedMethod === method
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              )}
            >
              {method === 'WeightedAvg' ? 'Weighted Avg' : method}
            </button>
          ))}
        </div>
        <p className="text-sm text-gray-600">
          {selectedMethod === 'FIFO' &&
            'First In, First Out: Assumes oldest inventory is sold first'}
          {selectedMethod === 'LIFO' &&
            'Last In, First Out: Assumes newest inventory is sold first'}
          {selectedMethod === 'WeightedAvg' &&
            'Weighted Average: Uses average cost weighted by quantity'}
          {selectedMethod === 'ABC' && 'ABC Analysis: Categorizes by value contribution (80/15/5)'}
        </p>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-blue-700 uppercase tracking-wider">
            Total Items
          </p>
          <p className="text-3xl font-bold text-blue-900 mt-2">
            {data.totals.totalQuantity.toLocaleString('en-IN')}
          </p>
          <p className="text-xs text-blue-600 mt-1">SKUs in inventory</p>
        </div>
        <div className="bg-green-50 border border-green-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-green-700 uppercase tracking-wider">
            Inventory Value
          </p>
          <p className="text-3xl font-bold text-green-900 mt-2">
            {formatCurrency(getValuationTotal())}
          </p>
          <p className="text-xs text-green-600 mt-1">Current valuation</p>
        </div>
        <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
          <p className="text-xs font-semibold text-purple-700 uppercase tracking-wider">
            Avg Unit Cost
          </p>
          <p className="text-3xl font-bold text-purple-900 mt-2">
            {formatCurrency(getValuationTotal() / Math.max(1, data.totals.totalQuantity))}
          </p>
          <p className="text-xs text-purple-600 mt-1">Per item</p>
        </div>
      </div>

      {/* Category-wise Analysis */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-4">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <Package className="w-5 h-5" />
          Category-wise Inventory Value
        </h3>
        <div className="space-y-3">
          {data.analysis.categoryWiseValue.map((cat) => (
            <div key={cat.category} className="space-y-1">
              <div className="flex justify-between text-sm">
                <span className="font-medium text-gray-900">{cat.category}</span>
                <span className="text-gray-600">{cat.percentage.toFixed(1)}%</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2 overflow-hidden">
                <div
                  className="h-full bg-blue-600 rounded-full"
                  style={{ width: `${cat.percentage}%` }}
                />
              </div>
              <p className="text-xs text-gray-500">{formatCurrency(cat.value)}</p>
            </div>
          ))}
        </div>
      </div>

      {/* ABC Analysis */}
      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
        {data.analysis.abcAnalysis.map((abc) => (
          <div key={abc.category} className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
            <h4 className="font-semibold text-gray-900">
              Category {abc.category}: {getAbcCategoryLabel(abc.category)}
            </h4>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-600">Items</span>
                <span className="font-medium">{abc.count}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-600">Value</span>
                <span className="font-medium">{formatCurrency(abc.value)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-600">Percentage</span>
                <span className="font-medium">{abc.percentage.toFixed(1)}%</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Stock Health Analysis */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-4">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <BarChart3 className="w-5 h-5" />
          Stock Health Distribution
        </h3>
        <div className="grid grid-cols-3 gap-4">
          <div className="text-center p-4 bg-green-50 rounded-lg">
            <p className="text-3xl font-bold text-green-900">
              {data.analysis.stockHealth.healthy}
            </p>
            <p className="text-sm text-green-700 mt-1">Healthy Items</p>
          </div>
          <div className="text-center p-4 bg-yellow-50 rounded-lg">
            <p className="text-3xl font-bold text-yellow-900">
              {data.analysis.stockHealth.slow}
            </p>
            <p className="text-sm text-yellow-700 mt-1">Slow Moving</p>
          </div>
          <div className="text-center p-4 bg-red-50 rounded-lg">
            <p className="text-3xl font-bold text-red-900">
              {data.analysis.stockHealth.dead}
            </p>
            <p className="text-sm text-red-700 mt-1">Dead Stock</p>
          </div>
        </div>
      </div>

      {/* Inventory Items Table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600">
                  SKU
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600">
                  Product
                </th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-gray-600">
                  Qty
                </th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">
                  Unit Cost
                </th>
                <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600">
                  Value
                </th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-gray-600">
                  ABC
                </th>
                <th className="px-4 py-3 text-center text-xs font-semibold text-gray-600">
                  Status
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {data.items.slice(0, 20).map((item) => (
                <tr key={item.sku} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm font-medium text-gray-900">
                    {item.sku}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {item.productName}
                  </td>
                  <td className="px-4 py-3 text-center text-sm text-gray-600">
                    {item.quantity}
                  </td>
                  <td className="px-4 py-3 text-right text-sm text-gray-600">
                    {formatCurrency(item.unitPrice)}
                  </td>
                  <td className="px-4 py-3 text-right text-sm font-medium text-gray-900">
                    {formatCurrency(getItemValue(item))}
                  </td>
                  <td className="px-4 py-3 text-center text-sm">
                    <span className="px-2 py-1 bg-blue-100 text-blue-800 rounded text-xs font-medium">
                      {item.abcCategory}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center text-sm">
                    <span
                      className={clsx(
                        'px-2 py-1 rounded text-xs font-medium',
                        getStockStatusColor(item.stockStatus)
                      )}
                    >
                      {item.stockStatus}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.items.length > 20 && (
          <div className="bg-gray-50 px-4 py-3 text-sm text-gray-600 border-t border-gray-200">
            Showing 20 of {data.items.length} items
          </div>
        )}
      </div>
    </div>
  );
}

export default InventoryValuationReport;
