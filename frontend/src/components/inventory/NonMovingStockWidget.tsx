// ============================================================================
// IMS 2.0 - Non-Moving Stock Widget
// ============================================================================
// Shows products with 0 sales in last N days

import { useState, useEffect } from 'react';
import { AlertTriangle, TrendingDown, Package } from 'lucide-react';
import api from '../../services/api';

interface NonMovingProduct {
  product_id: string;
  name: string;
  sku: string;
  current_stock: number;
  last_sold_date: string | null;
  days_since_sale: number;
}

export function NonMovingStockWidget() {
  const [products, setProducts] = useState<NonMovingProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(90);

  useEffect(() => {
    loadData();
  }, [days]);

  const loadData = async () => {
    setLoading(true);
    try {
      const response = await api.get(`/inventory/non-moving?days=${days}`);
      setProducts(response.data?.products || []);
    } catch (error) {
      console.error('Error loading non-moving stock:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingDown className="w-5 h-5 text-orange-400" />
          <h3 className="font-semibold text-white">Non-Moving Stock</h3>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="px-2 py-1 bg-gray-700 border border-gray-600 rounded text-sm text-gray-300"
        >
          <option value={30}>Last 30 days</option>
          <option value={60}>Last 60 days</option>
          <option value={90}>Last 90 days</option>
          <option value={180}>Last 180 days</option>
        </select>
      </div>

      <div className="max-h-96 overflow-y-auto">
        {loading ? (
          <div className="p-4 text-center text-gray-400">Loading...</div>
        ) : products.length === 0 ? (
          <div className="p-4 text-center text-gray-400">
            No non-moving products found
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-900 border-b border-gray-700 sticky top-0">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold text-gray-400">
                  Product
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-400">
                  Stock
                </th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-gray-400">
                  Last Sold
                </th>
              </tr>
            </thead>
            <tbody>
              {products.map((product) => (
                <tr
                  key={product.product_id}
                  className="border-b border-gray-700 hover:bg-gray-900"
                >
                  <td className="px-4 py-2">
                    <div>
                      <p className="font-medium text-white">{product.name}</p>
                      <p className="text-xs text-gray-500">{product.sku}</p>
                    </div>
                  </td>
                  <td className="px-4 py-2 text-right text-orange-400 font-semibold">
                    {product.current_stock}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-400">
                    {product.last_sold_date
                      ? new Date(product.last_sold_date).toLocaleDateString()
                      : 'Never'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
