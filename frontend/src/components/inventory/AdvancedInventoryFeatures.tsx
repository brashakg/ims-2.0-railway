// ============================================================================
// IMS 2.0 - Advanced Inventory Features
// ============================================================================
// Contact Lens Expiry, Power Grid, Sell-Through, Overstock Analysis

import { useState, useEffect } from 'react';
import { AlertTriangle, TrendingUp, Package, Grid3x3 } from 'lucide-react';
import api from '../../services/api';
import clsx from 'clsx';

// ============================================================================
// CONTACT LENS EXPIRY TRACKING
// ============================================================================

interface ContactLensProduct {
  stock_id: string;
  product_id: string;
  product_name: string;
  sku: string;
  quantity: number;
  expiry_date: string;
  days_until_expiry: number;
}

export function ContactLensExpiryWidget() {
  const [expired, setExpired] = useState<ContactLensProduct[]>([]);
  const [expiringSoon, setExpiringSoon] = useState<ContactLensProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [daysThreshold, setDaysThreshold] = useState(90);

  useEffect(() => {
    loadData();
  }, [daysThreshold]);

  const loadData = async () => {
    setLoading(true);
    try {
      const response = await api.get(
        `/inventory/contact-lenses/expiry-status?expiring_within_days=${daysThreshold}`
      );
      setExpired(response.data?.expired || []);
      setExpiringSoon(response.data?.expiring_soon || []);
    } catch (error) {
      // silently handle error
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-red-400" />
          <h3 className="font-semibold text-white">Contact Lens Expiry</h3>
        </div>
        <select
          value={daysThreshold}
          onChange={(e) => setDaysThreshold(Number(e.target.value))}
          className="px-2 py-1 bg-gray-700 border border-gray-600 rounded text-sm text-gray-300"
        >
          <option value={30}>30 days</option>
          <option value={60}>60 days</option>
          <option value={90}>90 days</option>
        </select>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-400">Loading...</div>
      ) : (
        <div className="p-4 space-y-4">
          {expired.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-red-400 mb-2">
                EXPIRED ({expired.length})
              </p>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {expired.map((item) => (
                  <div key={item.stock_id} className="text-xs bg-red-900/20 p-2 rounded border border-red-600/50">
                    <div className="flex justify-between">
                      <span className="text-red-300">{item.product_name}</span>
                      <span className="text-red-400 font-semibold">Qty: {item.quantity}</span>
                    </div>
                    <p className="text-red-500 text-[10px]">
                      Expired: {new Date(item.expiry_date).toLocaleDateString()}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {expiringSoon.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-orange-400 mb-2">
                EXPIRING SOON ({expiringSoon.length})
              </p>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {expiringSoon.map((item) => (
                  <div
                    key={item.stock_id}
                    className="text-xs bg-orange-900/20 p-2 rounded border border-orange-600/50"
                  >
                    <div className="flex justify-between">
                      <span className="text-orange-300">{item.product_name}</span>
                      <span className="text-orange-400 font-semibold">Qty: {item.quantity}</span>
                    </div>
                    <p className="text-orange-500 text-[10px]">
                      {item.days_until_expiry} days remaining
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {expired.length === 0 && expiringSoon.length === 0 && (
            <p className="text-center text-gray-400 text-sm">No expiry concerns</p>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// POWER-WISE LENS STOCK GRID
// ============================================================================

interface PowerGridCell {
  count: number;
  in_stock: boolean;
}

export function LensPowerGridWidget() {
  const [grid, setGrid] = useState<Record<string, Record<string, PowerGridCell>>>({});
  const [sphValues, setSphValues] = useState<string[]>([]);
  const [cylValues, setCylValues] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const response = await api.get('/inventory/lenses/power-grid');
      setGrid(response.data?.grid || {});
      setSphValues(response.data?.sph_range || []);
      setCylValues(response.data?.cyl_range || []);
    } catch (error) {
      // silently handle error
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="p-4 text-center text-gray-400">Loading grid...</div>;
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 shadow-sm overflow-x-auto p-4">
      <h3 className="font-semibold text-white mb-4 flex items-center gap-2">
        <Grid3x3 className="w-5 h-5 text-bv-gold-500" />
        Lens Power Grid (SPH × CYL)
      </h3>

      <div className="inline-block min-w-full">
        <table className="border-collapse">
          <thead>
            <tr>
              <th className="px-2 py-2 text-xs font-semibold text-gray-400 border border-gray-700">
                SPH/CYL
              </th>
              {cylValues.map((cyl) => (
                <th
                  key={cyl}
                  className="px-2 py-2 text-xs font-semibold text-gray-400 border border-gray-700 text-center"
                >
                  {cyl}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sphValues.map((sph) => (
              <tr key={sph}>
                <td className="px-2 py-2 text-xs font-semibold text-gray-300 border border-gray-700 bg-gray-900">
                  {sph}
                </td>
                {cylValues.map((cyl) => {
                  const cell = grid[sph]?.[cyl];
                  const inStock = cell?.in_stock;
                  const count = cell?.count || 0;

                  return (
                    <td
                      key={`${sph}-${cyl}`}
                      className={clsx(
                        'px-2 py-2 text-xs font-semibold text-center border border-gray-700',
                        inStock ? 'bg-green-900/30 text-green-300' : 'bg-red-900/30 text-red-300'
                      )}
                    >
                      {count}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex gap-4 text-xs">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-green-500" />
          <span className="text-gray-300">In Stock</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-red-500" />
          <span className="text-gray-300">Out of Stock</span>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// SELL-THROUGH ANALYSIS
// ============================================================================

interface BrandSellThrough {
  brand: string;
  units_sold: number;
  units_stocked: number;
  sell_through_percent: number;
}

export function SellThroughAnalysisWidget({ days = 30 }: { days?: number }) {
  const [brands, setBrands] = useState<BrandSellThrough[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, [days]);

  const loadData = async () => {
    setLoading(true);
    try {
      const response = await api.get(`/inventory/sell-through-analysis?days=${days}`);
      setBrands(response.data?.brands || []);
    } catch (error) {
      // silently handle error
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-700">
        <h3 className="font-semibold text-white flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-green-400" />
          Sell-Through Analysis ({days}d)
        </h3>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-400">Loading...</div>
      ) : brands.length === 0 ? (
        <div className="p-4 text-center text-gray-400">No data available</div>
      ) : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 border-b border-gray-700 sticky top-0">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold text-gray-400">
                  Brand
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-400">
                  Sold
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-400">
                  Stocked
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-400">
                  Sell-Through %
                </th>
              </tr>
            </thead>
            <tbody>
              {brands.map((brand) => (
                <tr
                  key={brand.brand}
                  className="border-b border-gray-700 hover:bg-gray-900"
                >
                  <td className="px-4 py-2 text-white font-medium">{brand.brand}</td>
                  <td className="px-4 py-2 text-right text-gray-300">
                    {brand.units_sold}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-300">
                    {brand.units_stocked}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <div className="w-16 bg-gray-700 rounded-full h-1.5">
                        <div
                          className="bg-green-500 h-1.5 rounded-full"
                          style={{
                            width: `${Math.min(brand.sell_through_percent, 100)}%`,
                          }}
                        />
                      </div>
                      <span className="text-green-400 font-semibold w-12 text-right">
                        {brand.sell_through_percent.toFixed(1)}%
                      </span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// OVERSTOCK ANALYSIS
// ============================================================================

interface OverstockItem {
  product_id: string;
  product_name: string;
  sku: string;
  current_stock: number;
  avg_monthly_sales: number;
  months_of_stock: number;
  overstock_multiple: number;
}

export function OverstockAnalysisWidget() {
  const [items, setItems] = useState<OverstockItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [threshold, setThreshold] = useState(3.0);

  useEffect(() => {
    loadData();
  }, [threshold]);

  const loadData = async () => {
    setLoading(true);
    try {
      const response = await api.get(
        `/inventory/overstock-analysis?overstocking_threshold=${threshold}`
      );
      setItems(response.data?.items || []);
    } catch (error) {
      // silently handle error
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Package className="w-5 h-5 text-red-400" />
          <h3 className="font-semibold text-white">Overstock Analysis</h3>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-400">Threshold:</label>
          <input
            type="number"
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value))}
            min="1"
            step="0.5"
            className="w-16 px-2 py-1 bg-gray-700 border border-gray-600 rounded text-sm text-gray-300"
          />
          <span className="text-xs text-gray-400">x</span>
        </div>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-400">Loading...</div>
      ) : items.length === 0 ? (
        <div className="p-4 text-center text-gray-400">No overstock detected</div>
      ) : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 border-b border-gray-700 sticky top-0">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold text-gray-400">
                  Product
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-400">
                  Stock
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-400">
                  Avg Monthly
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-400">
                  Months
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.product_id}
                  className="border-b border-gray-700 hover:bg-gray-900"
                >
                  <td className="px-4 py-2">
                    <div>
                      <p className="text-white font-medium text-xs">{item.product_name}</p>
                      <p className="text-gray-500 text-[10px]">{item.sku}</p>
                    </div>
                  </td>
                  <td className="px-4 py-2 text-right text-orange-400 font-semibold">
                    {item.current_stock}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-300">
                    {item.avg_monthly_sales.toFixed(0)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <span className="font-semibold text-red-400">
                      {item.months_of_stock.toFixed(1)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
