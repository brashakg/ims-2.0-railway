// ============================================================================
// IMS 2.0 - Stock Replenishment
// ============================================================================
// Auto-replenishment dashboard, EOQ, ABC/XYZ analysis, dead stock, inter-store transfers

import { useState } from 'react';
import { Plus, TrendingDown, Zap, AlertTriangle } from 'lucide-react';
import clsx from 'clsx';

interface ReplenishmentItem {
  product_id: string;
  product_name: string;
  current_stock: number;
  reorder_level: number;
  eoq: number;
  abc_category: 'A' | 'B' | 'C';
  xyz_category: 'X' | 'Y' | 'Z';
  preferred_vendor_id: string;
  preferred_vendor_name: string;
  estimated_cost: number;
  last_purchase_price: number;
  stock_status: 'critical' | 'low' | 'normal' | 'excess';
}

const MOCK_SUGGESTIONS: ReplenishmentItem[] = [
  {
    product_id: 'prod-001',
    product_name: 'Frame Model A',
    current_stock: 25,
    reorder_level: 50,
    eoq: 150,
    abc_category: 'A',
    xyz_category: 'Y',
    preferred_vendor_id: 'v-001',
    preferred_vendor_name: 'Optical Frames Ltd',
    estimated_cost: 75000,
    last_purchase_price: 500,
    stock_status: 'critical',
  },
  {
    product_id: 'prod-002',
    product_name: 'Premium Lens Coating',
    current_stock: 120,
    reorder_level: 100,
    eoq: 500,
    abc_category: 'A',
    xyz_category: 'Z',
    preferred_vendor_id: 'v-002',
    preferred_vendor_name: 'Lens Manufacturers Inc',
    estimated_cost: 150000,
    last_purchase_price: 300,
    stock_status: 'normal',
  },
  {
    product_id: 'prod-003',
    product_name: 'Lens Case',
    current_stock: 45,
    reorder_level: 80,
    eoq: 200,
    abc_category: 'B',
    xyz_category: 'X',
    preferred_vendor_id: 'v-003',
    preferred_vendor_name: 'Accessories Wholesale',
    estimated_cost: 12000,
    last_purchase_price: 60,
    stock_status: 'low',
  },
];

const DEAD_STOCK = [
  {
    product_id: 'prod-010',
    product_name: 'Vintage Frame Design',
    current_stock: 120,
    last_sold: '2023-08-15',
    days_inactive: 167,
    estimated_value: 36000,
  },
  {
    product_id: 'prod-011',
    product_name: 'Discontinued Lens Type',
    current_stock: 45,
    last_sold: '2023-07-20',
    days_inactive: 193,
    estimated_value: 13500,
  },
];

const getStockStatusColor = (status: string) => {
  switch (status) {
    case 'critical':
      return 'bg-red-900 text-red-300';
    case 'low':
      return 'bg-orange-900 text-orange-300';
    case 'normal':
      return 'bg-green-900 text-green-300';
    case 'excess':
      return 'bg-blue-900 text-blue-300';
    default:
      return 'bg-gray-700 text-gray-300';
  }
};

const getCategoryColor = (category: string) => {
  switch (category) {
    case 'A':
      return 'text-red-400';
    case 'B':
      return 'text-yellow-400';
    case 'C':
      return 'text-green-400';
    default:
      return 'text-gray-400';
  }
};

export function StockReplenishment() {
  const [activeTab, setActiveTab] = useState<'suggestions' | 'abc-analysis' | 'dead-stock' | 'eoq'>('suggestions');
  const [selectedItems, setSelectedItems] = useState<string[]>([]);

  const toggleSelection = (productId: string) => {
    setSelectedItems(prev =>
      prev.includes(productId) ? prev.filter(id => id !== productId) : [...prev, productId]
    );
  };

  const selectedSuggestions = MOCK_SUGGESTIONS.filter(s => selectedItems.includes(s.product_id));
  const totalEstimatedCost = selectedSuggestions.reduce((sum, s) => sum + s.estimated_cost, 0);

  const criticalItems = MOCK_SUGGESTIONS.filter(s => s.stock_status === 'critical');
  const lowItems = MOCK_SUGGESTIONS.filter(s => s.stock_status === 'low');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Stock Replenishment</h1>
          <p className="text-gray-400">Auto-replenishment suggestions and inventory optimization</p>
        </div>
        {selectedItems.length > 0 && (
          <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-semibold flex items-center gap-2">
            <Plus className="w-5 h-5" />
            Create PO for {selectedItems.length} items
          </button>
        )}
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Critical Items</p>
          <p className="text-2xl font-bold text-red-400">{criticalItems.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Low Stock Items</p>
          <p className="text-2xl font-bold text-orange-400">{lowItems.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Dead Stock Items</p>
          <p className="text-2xl font-bold text-purple-400">{DEAD_STOCK.length}</p>
        </div>
        <div className="bg-gray-800 rounded-lg p-4">
          <p className="text-gray-400 text-sm mb-1">Est. Replenish Cost</p>
          <p className="text-2xl font-bold text-green-400">₹{(totalEstimatedCost / 100000).toFixed(1)}L</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-gray-700">
        {(['suggestions', 'abc-analysis', 'dead-stock', 'eoq'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={clsx(
              'px-4 py-3 font-medium border-b-2 transition-colors',
              activeTab === tab
                ? 'border-blue-500 text-blue-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            )}
          >
            {tab === 'suggestions' ? 'Suggestions' : tab === 'abc-analysis' ? 'ABC/XYZ Analysis' : tab === 'dead-stock' ? 'Dead Stock' : 'EOQ Calculation'}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'suggestions' && (
        <div className="space-y-4">
          {criticalItems.length > 0 && (
            <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-red-300 font-semibold">{criticalItems.length} Critical Items</p>
                <p className="text-red-300 text-sm">These items are below critical stock level and require immediate replenishment.</p>
              </div>
            </div>
          )}

          <div className="space-y-3">
            {MOCK_SUGGESTIONS.map((item) => (
              <div
                key={item.product_id}
                className={clsx(
                  'rounded-lg p-4 border transition-colors cursor-pointer',
                  selectedItems.includes(item.product_id)
                    ? 'bg-blue-900/30 border-blue-600'
                    : 'bg-gray-800 border-gray-700 hover:border-gray-600'
                )}
                onClick={() => toggleSelection(item.product_id)}
              >
                <div className="flex items-start gap-4">
                  <input
                    type="checkbox"
                    checked={selectedItems.includes(item.product_id)}
                    onChange={() => toggleSelection(item.product_id)}
                    className="w-5 h-5 rounded border-gray-500 mt-1"
                  />

                  <div className="flex-1">
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <p className="text-white font-semibold">{item.product_name}</p>
                        <p className="text-gray-400 text-sm">Product ID: {item.product_id}</p>
                      </div>
                      <span className={clsx('px-2 py-1 rounded text-xs font-semibold', getStockStatusColor(item.stock_status))}>
                        {item.stock_status.charAt(0).toUpperCase() + item.stock_status.slice(1)}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-3 pb-3 border-b border-gray-700">
                      <div>
                        <p className="text-gray-400 text-xs mb-1">Current Stock</p>
                        <p className="text-white font-semibold">{item.current_stock}</p>
                      </div>
                      <div>
                        <p className="text-gray-400 text-xs mb-1">Reorder Level</p>
                        <p className="text-white font-semibold">{item.reorder_level}</p>
                      </div>
                      <div>
                        <p className="text-gray-400 text-xs mb-1">EOQ</p>
                        <p className="text-white font-semibold">{item.eoq}</p>
                      </div>
                      <div>
                        <p className="text-gray-400 text-xs mb-1">Category</p>
                        <p className={clsx('font-semibold', getCategoryColor(item.abc_category))}>
                          {item.abc_category}-{item.xyz_category}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-400 text-xs mb-1">Est. Cost</p>
                        <p className="text-green-400 font-semibold">₹{(item.estimated_cost / 1000).toFixed(0)}K</p>
                      </div>
                    </div>

                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-gray-400 text-sm">{item.preferred_vendor_name}</p>
                        <p className="text-gray-500 text-xs">Last price: ₹{item.last_purchase_price}</p>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                        }}
                        className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded font-semibold"
                      >
                        Create PO
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'abc-analysis' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* ABC Analysis */}
          <div className="lg:col-span-2 space-y-4">
            <h3 className="text-lg font-semibold text-white mb-4">ABC Classification</h3>
            {[
              { category: 'A', color: 'text-red-400', bgColor: 'bg-red-900/30', desc: 'High-value items (80% value, 20% items)' },
              { category: 'B', color: 'text-yellow-400', bgColor: 'bg-yellow-900/30', desc: 'Medium-value items (15% value, 30% items)' },
              { category: 'C', color: 'text-green-400', bgColor: 'bg-green-900/30', desc: 'Low-value items (5% value, 50% items)' },
            ].map((cat) => (
              <div key={cat.category} className={clsx('rounded-lg p-4 border', cat.bgColor, cat.bgColor === 'bg-red-900/30' ? 'border-red-700' : cat.bgColor === 'bg-yellow-900/30' ? 'border-yellow-700' : 'border-green-700')}>
                <p className={clsx('font-semibold mb-1', cat.color)}>Category {cat.category}</p>
                <p className="text-gray-400 text-sm">{cat.desc}</p>
                <p className="text-white font-semibold mt-2">
                  {MOCK_SUGGESTIONS.filter(s => s.abc_category === cat.category).length} items
                </p>
              </div>
            ))}
          </div>

          {/* XYZ Analysis */}
          <div className="space-y-4">
            <h3 className="text-lg font-semibold text-white mb-4">XYZ Classification</h3>
            {[
              { category: 'X', label: 'Predictable', desc: 'Stable demand' },
              { category: 'Y', label: 'Moderate', desc: 'Variable demand' },
              { category: 'Z', label: 'Unpredictable', desc: 'Uncertain demand' },
            ].map((cat) => (
              <div key={cat.category} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p className="text-white font-semibold">{cat.category} - {cat.label}</p>
                <p className="text-gray-400 text-xs mt-1">{cat.desc}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'dead-stock' && (
        <div className="space-y-4">
          <div className="bg-purple-900/30 border border-purple-700 rounded-lg p-4 flex items-start gap-3">
            <TrendingDown className="w-5 h-5 text-purple-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-purple-300 font-semibold">Dead Stock Identification</p>
              <p className="text-purple-300 text-sm">Items with no sales activity for 6+ months</p>
            </div>
          </div>

          <div className="space-y-3">
            {DEAD_STOCK.map((item) => (
              <div key={item.product_id} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <div className="flex items-start justify-between mb-3">
                  <div>
                    <p className="text-white font-semibold">{item.product_name}</p>
                    <p className="text-gray-400 text-sm">Product ID: {item.product_id}</p>
                  </div>
                  <span className="px-3 py-1 bg-purple-900 text-purple-300 rounded text-xs font-semibold">
                    {item.days_inactive} days inactive
                  </span>
                </div>

                <div className="grid grid-cols-3 gap-4 mb-3 pb-3 border-b border-gray-700">
                  <div>
                    <p className="text-gray-400 text-xs mb-1">Current Stock</p>
                    <p className="text-white font-semibold">{item.current_stock}</p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-xs mb-1">Last Sold</p>
                    <p className="text-white font-semibold">{new Date(item.last_sold).toLocaleDateString()}</p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-xs mb-1">Estimated Value</p>
                    <p className="text-orange-400 font-semibold">₹{item.estimated_value.toLocaleString('en-IN')}</p>
                  </div>
                </div>

                <div className="flex gap-2">
                  <button className="flex-1 px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded font-semibold">
                    Promote
                  </button>
                  <button className="flex-1 px-3 py-2 bg-red-600 hover:bg-red-700 text-white text-sm rounded font-semibold">
                    Clearance
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === 'eoq' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-gray-800 rounded-lg p-6 border border-gray-700">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Zap className="w-5 h-5" />
              Economic Order Quantity (EOQ)
            </h3>
            <div className="space-y-4">
              <p className="text-gray-400 text-sm">
                EOQ = √(2DS/H) where:
              </p>
              <ul className="text-gray-300 text-sm space-y-2">
                <li><span className="text-blue-400">D</span> = Annual demand</li>
                <li><span className="text-blue-400">S</span> = Ordering cost per order</li>
                <li><span className="text-blue-400">H</span> = Holding cost per unit</li>
              </ul>
              <p className="text-gray-400 text-sm pt-4 border-t border-gray-700">
                EOQ helps minimize the total cost of inventory by balancing ordering costs and holding costs.
              </p>
            </div>
          </div>

          <div className="space-y-4">
            {MOCK_SUGGESTIONS.map((item) => (
              <div key={item.product_id} className="bg-gray-800 rounded-lg p-4 border border-gray-700">
                <p className="text-white font-semibold mb-2">{item.product_name}</p>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <p className="text-gray-400">EOQ Quantity</p>
                    <p className="text-green-400 font-semibold">{item.eoq} units</p>
                  </div>
                  <div>
                    <p className="text-gray-400">Cost/Order</p>
                    <p className="text-blue-400 font-semibold">₹{(item.estimated_cost / 1000).toFixed(0)}K</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default StockReplenishment;
