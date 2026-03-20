// ============================================================================
// IMS 2.0 - Stock Aging Report
// ============================================================================
// Identify slow-moving inventory and optimize stock levels

import { useState, useEffect } from 'react';
import {
  TrendingDown,
  TrendingUp,
  Clock,
  Package,
  Loader2,
  Download,
  Filter,
  BarChart3,
  AlertTriangle,
  Calendar,
  RefreshCw,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';

interface AgingProduct {
  id: string;
  sku: string;
  name: string;
  brand: string;
  category: string;
  quantity: number;
  value: number;
  daysInStock: number;
  lastSaleDate?: string;
  salesLast30Days: number;
  salesLast90Days: number;
  turnoverRate: number;
  classification: 'A' | 'B' | 'C'; // A=Fast, B=Medium, C=Slow
  ageCategory: '0-30' | '31-60' | '61-90' | '91-180' | '180+';
}

type ClassificationFilter = 'all' | 'A' | 'B' | 'C';
type AgeCategoryFilter = 'all' | '0-30' | '31-60' | '61-90' | '91-180' | '180+';

export function StockAgingReport() {
  const { user } = useAuth();
  const toast = useToast();

  const [products, setProducts] = useState<AgingProduct[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [classificationFilter, setClassificationFilter] = useState<ClassificationFilter>('all');
  const [ageCategoryFilter, setAgeCategoryFilter] = useState<AgeCategoryFilter>('all');
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    loadAgingData();
  }, [user?.activeStoreId]);

  const loadAgingData = async () => {
    setIsLoading(true);
    try {
      const storeId = user?.activeStoreId || '';
      if (!storeId) {
        setProducts([]);
        return;
      }
      const result = await inventoryApi.getAgingReport(storeId);
      const items: AgingProduct[] = (result?.products || []).map((p: any) => ({
        id: p.id || p.product_id || '',
        sku: p.sku || '',
        name: p.name || '',
        brand: p.brand || '',
        category: p.category || '',
        quantity: p.quantity || 0,
        value: p.value || 0,
        daysInStock: p.daysInStock || 0,
        lastSaleDate: p.lastSaleDate || undefined,
        salesLast30Days: p.salesLast30Days || 0,
        salesLast90Days: p.salesLast90Days || 0,
        turnoverRate: p.turnoverRate || 0,
        classification: p.classification || 'C',
        ageCategory: p.ageCategory || '0-30',
      }));
      setProducts(items);
    } catch (error: any) {
      toast.error('Failed to load aging data');
      setProducts([]);
    } finally {
      setIsLoading(false);
    }
  };

  const getFilteredProducts = () => {
    return products.filter((product) => {
      const matchesClassification =
        classificationFilter === 'all' || product.classification === classificationFilter;
      const matchesAge =
        ageCategoryFilter === 'all' || product.ageCategory === ageCategoryFilter;
      const matchesSearch =
        searchQuery === '' ||
        product.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        product.sku.toLowerCase().includes(searchQuery.toLowerCase()) ||
        product.brand.toLowerCase().includes(searchQuery.toLowerCase());

      return matchesClassification && matchesAge && matchesSearch;
    });
  };

  const filteredProducts = getFilteredProducts();

  // Statistics
  const totalProducts = products.length;
  const classACount = products.filter((p) => p.classification === 'A').length;
  const classBCount = products.filter((p) => p.classification === 'B').length;
  const classCCount = products.filter((p) => p.classification === 'C').length;
  const slowMovingValue = products
    .filter((p) => p.classification === 'C')
    .reduce((sum, p) => sum + p.value, 0);
  const averageAge =
    products.reduce((sum, p) => sum + p.daysInStock, 0) / products.length || 0;
  const oldStockCount = products.filter((p) => p.daysInStock > 90).length;

  const getClassificationBadge = (classification: AgingProduct['classification']) => {
    const config = {
      A: { label: 'Fast Mover', color: 'bg-green-100 text-green-800 border-green-200' },
      B: { label: 'Medium Mover', color: 'bg-yellow-100 text-yellow-800 border-yellow-200' },
      C: { label: 'Slow Mover', color: 'bg-red-100 text-red-800 border-red-200' },
    };

    return (
      <span
        className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border ${config[classification].color}`}
      >
        {config[classification].label}
      </span>
    );
  };

  const getAgeBadge = (days: number) => {
    if (days <= 30) return { color: 'text-green-600', icon: '✓' };
    if (days <= 60) return { color: 'text-blue-600', icon: '○' };
    if (days <= 90) return { color: 'text-yellow-600', icon: '△' };
    if (days <= 180) return { color: 'text-orange-600', icon: '▲' };
    return { color: 'text-red-600', icon: '⬤' };
  };

  const exportToCSV = (data: AgingProduct[]) => {
    if (data.length === 0) return;

    const headers = [
      'SKU',
      'Name',
      'Brand',
      'Category',
      'Classification',
      'Age Category',
      'Days In Stock',
      'Quantity',
      'Value (Rs)',
      'Sales Last 30 Days',
      'Sales Last 90 Days',
      'Turnover Rate (x/yr)',
      'Last Sale Date',
    ];

    const rows = data.map(p => [
      p.sku,
      `"${p.name.replace(/"/g, '""')}"`,
      `"${p.brand.replace(/"/g, '""')}"`,
      p.category,
      p.classification,
      p.ageCategory,
      p.daysInStock,
      p.quantity,
      p.value,
      p.salesLast30Days,
      p.salesLast90Days,
      p.turnoverRate.toFixed(2),
      p.lastSaleDate ? new Date(p.lastSaleDate).toLocaleDateString() : 'No sales',
    ]);

    const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `stock-aging-report-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success(`Exported ${data.length} products to CSV`);
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
            <Clock className="w-6 h-6 text-orange-600" />
            Stock Aging Report
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            Analyze inventory age and identify slow-moving items
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={loadAgingData}
            disabled={isLoading}
            className="btn-outline text-sm flex items-center gap-2"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Refresh
          </button>
          <button
            onClick={() => exportToCSV(filteredProducts)}
            disabled={isLoading || filteredProducts.length === 0}
            className="btn-outline text-sm flex items-center gap-2"
          >
            <Download className="w-4 h-4" />
            Export CSV
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 tablet:grid-cols-6 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-purple-100 rounded-lg flex items-center justify-center">
              <Package className="w-5 h-5 text-purple-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Total Items</p>
              <p className="text-2xl font-bold text-gray-900">{totalProducts}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <TrendingUp className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Fast (A)</p>
              <p className="text-2xl font-bold text-green-600">{classACount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-yellow-100 rounded-lg flex items-center justify-center">
              <BarChart3 className="w-5 h-5 text-yellow-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Medium (B)</p>
              <p className="text-2xl font-bold text-yellow-600">{classBCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
              <TrendingDown className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Slow (C)</p>
              <p className="text-2xl font-bold text-red-600">{classCCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Old Stock (90d+)</p>
              <p className="text-2xl font-bold text-orange-600">{oldStockCount}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Clock className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Avg Age</p>
              <p className="text-2xl font-bold text-blue-600">{Math.round(averageAge)}d</p>
            </div>
          </div>
        </div>
      </div>

      {/* ABC Analysis Summary */}
      <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
        <div className="card bg-gradient-to-br from-green-50 to-emerald-50 border-green-200">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-green-900">Class A - Fast Movers</h3>
            <TrendingUp className="w-5 h-5 text-green-600" />
          </div>
          <p className="text-sm text-green-800 mb-2">
            High turnover, frequent sales. Priority stock.
          </p>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-bold text-green-900">{classACount}</span>
            <span className="text-sm text-green-700">
              ({((classACount / totalProducts) * 100).toFixed(0)}%)
            </span>
          </div>
        </div>
        <div className="card bg-gradient-to-br from-yellow-50 to-amber-50 border-yellow-200">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-yellow-900">Class B - Medium Movers</h3>
            <BarChart3 className="w-5 h-5 text-yellow-600" />
          </div>
          <p className="text-sm text-yellow-800 mb-2">
            Moderate turnover, regular sales. Monitor closely.
          </p>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-bold text-yellow-900">{classBCount}</span>
            <span className="text-sm text-yellow-700">
              ({((classBCount / totalProducts) * 100).toFixed(0)}%)
            </span>
          </div>
        </div>
        <div className="card bg-gradient-to-br from-red-50 to-rose-50 border-red-200">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-red-900">Class C - Slow Movers</h3>
            <TrendingDown className="w-5 h-5 text-red-600" />
          </div>
          <p className="text-sm text-red-800 mb-2">
            Low turnover, infrequent sales. Consider discount/return.
          </p>
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-bold text-red-900">{classCCount}</span>
            <span className="text-sm text-red-700">
              ({((classCCount / totalProducts) * 100).toFixed(0)}%)
            </span>
          </div>
          <div className="mt-2 pt-2 border-t border-red-200">
            <p className="text-xs text-red-700">
              Tied Capital: ₹{(slowMovingValue / 100000).toFixed(1)}L
            </p>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          {/* Search */}
          <div className="flex-1">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by product, SKU, or brand..."
              className="input-field w-full"
            />
          </div>

          {/* Classification Filter */}
          <div className="flex items-center gap-2">
            <Filter className="w-5 h-5 text-gray-500" />
            <select
              value={classificationFilter}
              onChange={(e) => setClassificationFilter(e.target.value as ClassificationFilter)}
              className="input-field"
            >
              <option value="all">All Classes</option>
              <option value="A">Class A (Fast)</option>
              <option value="B">Class B (Medium)</option>
              <option value="C">Class C (Slow)</option>
            </select>
          </div>

          {/* Age Filter */}
          <div className="flex items-center gap-2">
            <Calendar className="w-5 h-5 text-gray-500" />
            <select
              value={ageCategoryFilter}
              onChange={(e) => setAgeCategoryFilter(e.target.value as AgeCategoryFilter)}
              className="input-field"
            >
              <option value="all">All Ages</option>
              <option value="0-30">0-30 days</option>
              <option value="31-60">31-60 days</option>
              <option value="61-90">61-90 days</option>
              <option value="91-180">91-180 days</option>
              <option value="180+">180+ days</option>
            </select>
          </div>
        </div>
      </div>

      {/* Products Table */}
      {isLoading ? (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-purple-600" />
        </div>
      ) : filteredProducts.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p className="font-medium">No products found</p>
          <p className="text-sm">Try adjusting your filters</p>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Product
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Category
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Classification
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">
                    Quantity
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase">
                    Value
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Days in Stock
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Turnover Rate
                  </th>
                  <th className="px-4 py-3 text-center text-xs font-medium text-gray-500 uppercase">
                    Sales (30d)
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Last Sale
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {filteredProducts.map((product) => {
                  const ageBadge = getAgeBadge(product.daysInStock);
                  return (
                    <tr key={product.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <div>
                          <p className="font-medium text-gray-900">{product.name}</p>
                          <p className="text-sm text-gray-500">
                            {product.brand} • {product.sku}
                          </p>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">{product.category}</td>
                      <td className="px-4 py-3 text-center">
                        {getClassificationBadge(product.classification)}
                      </td>
                      <td className="px-4 py-3 text-right text-sm font-medium text-gray-900">
                        {product.quantity}
                      </td>
                      <td className="px-4 py-3 text-right text-sm text-gray-900">
                        ₹{product.value.toLocaleString('en-IN')}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <div className="flex flex-col items-center gap-1">
                          <span
                            className={`text-xl font-bold ${ageBadge.color}`}
                            title={`${product.daysInStock} days`}
                          >
                            {ageBadge.icon}
                          </span>
                          <span className="text-xs text-gray-500">{product.daysInStock}d</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <div className="flex flex-col items-center">
                          <span className="text-sm font-medium text-gray-900">
                            {product.turnoverRate.toFixed(1)}x
                          </span>
                          <span className="text-xs text-gray-500">/year</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center text-sm font-medium text-gray-900">
                        {product.salesLast30Days}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">
                        {product.lastSaleDate
                          ? new Date(product.lastSaleDate).toLocaleDateString()
                          : 'No sales'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="card bg-gray-50">
        <h3 className="font-semibold text-gray-900 mb-3">Age Indicators Legend</h3>
        <div className="grid grid-cols-2 tablet:grid-cols-5 gap-4">
          <div className="flex items-center gap-2">
            <span className="text-xl text-green-600">✓</span>
            <span className="text-sm text-gray-700">0-30 days (Fresh)</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xl text-blue-600">○</span>
            <span className="text-sm text-gray-700">31-60 days (Good)</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xl text-yellow-600">△</span>
            <span className="text-sm text-gray-700">61-90 days (Monitor)</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xl text-orange-600">▲</span>
            <span className="text-sm text-gray-700">91-180 days (Aging)</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xl text-red-600">⬤</span>
            <span className="text-sm text-gray-700">180+ days (Old)</span>
          </div>
        </div>
      </div>
    </div>
  );
}
