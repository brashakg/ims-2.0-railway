// ============================================================================
// IMS 2.0 - Advanced Inventory Features
// ============================================================================
// Contact Lens Expiry, Power Grid, Sell-Through, Overstock Analysis

import { useState, useEffect } from 'react';
import { AlertTriangle, TrendingUp, Package, Grid3x3, ArrowLeftRight, ShieldAlert } from 'lucide-react';
import api from '../../services/api';
import { useAuth } from '../../context/AuthContext';
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
  const { user } = useAuth();
  const [expired, setExpired] = useState<ContactLensProduct[]>([]);
  const [expiringSoon, setExpiringSoon] = useState<ContactLensProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [daysThreshold, setDaysThreshold] = useState(90);

  useEffect(() => {
    loadData();
  }, [daysThreshold, user?.activeStoreId]);

  const loadData = async () => {
    setLoading(true);
    try {
      const storeParam = user?.activeStoreId ? `&store_id=${user.activeStoreId}` : '';
      const response = await api.get(
        `/inventory/contact-lenses/expiry-status?expiring_within_days=${daysThreshold}${storeParam}`
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
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-red-600" />
          <h3 className="font-semibold text-gray-900">Contact Lens Expiry</h3>
        </div>
        <select
          value={daysThreshold}
          onChange={(e) => setDaysThreshold(Number(e.target.value))}
          className="px-2 py-1 bg-gray-100 border border-gray-300 rounded text-sm text-gray-700"
        >
          <option value={30}>30 days</option>
          <option value={60}>60 days</option>
          <option value={90}>90 days</option>
        </select>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-500">Loading...</div>
      ) : (
        <div className="p-4 space-y-4">
          {expired.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-red-600 mb-2">
                EXPIRED ({expired.length})
              </p>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {expired.map((item) => (
                  <div key={item.stock_id} className="text-xs bg-red-50/20 p-2 rounded border border-red-600/50">
                    <div className="flex justify-between">
                      <span className="text-red-700">{item.product_name}</span>
                      <span className="text-red-600 font-semibold">Qty: {item.quantity}</span>
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
              <p className="text-xs font-semibold text-orange-600 mb-2">
                EXPIRING SOON ({expiringSoon.length})
              </p>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {expiringSoon.map((item) => (
                  <div
                    key={item.stock_id}
                    className="text-xs bg-orange-50/20 p-2 rounded border border-orange-600/50"
                  >
                    <div className="flex justify-between">
                      <span className="text-orange-700">{item.product_name}</span>
                      <span className="text-orange-600 font-semibold">Qty: {item.quantity}</span>
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
            <p className="text-center text-gray-500 text-sm">No expiry concerns</p>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// CONTACT LENS INVENTORY (brand x power x BC x modality)
// ============================================================================

interface CLLine {
  product_id: string;
  sku: string;
  brand: string;
  model: string;
  cl_series: string | null;
  modality: string | null;
  base_curve: number | null;
  diameter: number | null;
  cl_power: number | null;
  cl_cyl: number | null;
  cl_axis: number | null;
  color: string | null;
  pack_size: number | null;
  batch_code: string | null;
  expiry_date: string | null;
  on_hand: number;
  days_until_expiry: number | null;
}

const NEAR_EXPIRY_DAYS = 90;

function fmtPower(v: number | null): string {
  if (v === null || v === undefined) return '-';
  // Explicit sign, 2 decimals — matches Rx power formatting (e.g. -2.00, +1.25).
  const sign = v > 0 ? '+' : v < 0 ? '' : '';
  return `${sign}${v.toFixed(2)}`;
}

export function ContactLensInventoryWidget() {
  const { user } = useAuth();
  const [lines, setLines] = useState<CLLine[]>([]);
  const [loading, setLoading] = useState(true);
  const [brand, setBrand] = useState('');
  const [modality, setModality] = useState('');
  const [nearExpiryOnly, setNearExpiryOnly] = useState(false);

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.activeStoreId, brand, modality, nearExpiryOnly]);

  const loadData = async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = {};
      if (user?.activeStoreId) params.store_id = user.activeStoreId;
      if (brand) params.brand = brand;
      if (modality) params.modality = modality;
      if (nearExpiryOnly) params.near_expiry_days = NEAR_EXPIRY_DAYS;
      const response = await api.get('/inventory/contact-lenses', { params });
      setLines(response.data?.items || []);
    } catch {
      // fail-soft: show empty
      setLines([]);
    } finally {
      setLoading(false);
    }
  };

  // Distinct brands for the filter dropdown (from the loaded rows).
  const brands = Array.from(new Set(lines.map((l) => l.brand).filter(Boolean))).sort();
  const totalUnits = lines.reduce((sum, l) => sum + (l.on_hand || 0), 0);

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-200 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Package className="w-5 h-5 text-gray-700" />
          <h3 className="font-semibold text-gray-900">Contact Lens Inventory</h3>
          <span className="text-xs text-gray-500">
            {lines.length} lines · {totalUnits} units
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={brand}
            onChange={(e) => setBrand(e.target.value)}
            className="px-2 py-1 bg-gray-100 border border-gray-300 rounded text-sm text-gray-700"
          >
            <option value="">All brands</option>
            {brands.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
          <select
            value={modality}
            onChange={(e) => setModality(e.target.value)}
            className="px-2 py-1 bg-gray-100 border border-gray-300 rounded text-sm text-gray-700"
          >
            <option value="">All modalities</option>
            {['DAILY', 'FORTNIGHTLY', 'MONTHLY', 'QUARTERLY', 'YEARLY', 'COLOR'].map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={nearExpiryOnly}
              onChange={(e) => setNearExpiryOnly(e.target.checked)}
            />
            Near expiry (&lt;{NEAR_EXPIRY_DAYS}d)
          </label>
        </div>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-500">Loading...</div>
      ) : lines.length === 0 ? (
        <p className="p-6 text-center text-gray-500 text-sm">No contact-lens stock found</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-gray-500 border-b border-gray-200">
                <th className="px-3 py-2">Brand / Series</th>
                <th className="px-3 py-2">SKU</th>
                <th className="px-3 py-2">Power</th>
                <th className="px-3 py-2">BC / DIA</th>
                <th className="px-3 py-2">Modality</th>
                <th className="px-3 py-2 text-right">Pack</th>
                <th className="px-3 py-2 text-right">On hand</th>
                <th className="px-3 py-2">Batch</th>
                <th className="px-3 py-2">Nearest expiry</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => {
                const near =
                  l.days_until_expiry !== null && l.days_until_expiry < NEAR_EXPIRY_DAYS;
                const expired = l.days_until_expiry !== null && l.days_until_expiry < 0;
                return (
                  <tr
                    key={`${l.product_id}-${l.batch_code ?? 'nobatch'}-${i}`}
                    className="border-b border-gray-100 hover:bg-gray-50"
                  >
                    <td className="px-3 py-2">
                      <div className="font-medium text-gray-900">{l.brand || '-'}</div>
                      <div className="text-xs text-gray-500">{l.cl_series || l.model}</div>
                    </td>
                    <td className="px-3 py-2 text-gray-700">{l.sku || '-'}</td>
                    <td className="px-3 py-2 text-gray-900 font-mono">{fmtPower(l.cl_power)}</td>
                    <td className="px-3 py-2 text-gray-700">
                      {l.base_curve ?? '-'} / {l.diameter ?? '-'}
                    </td>
                    <td className="px-3 py-2 text-gray-700">{l.modality || '-'}</td>
                    <td className="px-3 py-2 text-right text-gray-700">{l.pack_size ?? '-'}</td>
                    <td className="px-3 py-2 text-right font-semibold text-gray-900">{l.on_hand}</td>
                    <td className="px-3 py-2 text-gray-600">{l.batch_code || '-'}</td>
                    <td className="px-3 py-2">
                      {l.expiry_date ? (
                        <span
                          className={clsx(
                            'text-xs',
                            expired
                              ? 'text-red-700 font-semibold'
                              : near
                                ? 'text-red-600'
                                : 'text-gray-600'
                          )}
                        >
                          {new Date(l.expiry_date).toLocaleDateString()}
                          {l.days_until_expiry !== null && (
                            <span className="ml-1 text-[10px]">
                              ({expired ? 'expired' : `${l.days_until_expiry}d`})
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-xs text-gray-400">-</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
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
  const { user } = useAuth();
  const [grid, setGrid] = useState<Record<string, Record<string, PowerGridCell>>>({});
  const [sphValues, setSphValues] = useState<string[]>([]);
  const [cylValues, setCylValues] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, [user?.activeStoreId]);

  const loadData = async () => {
    setLoading(true);
    try {
      const storeParam = user?.activeStoreId ? `?store_id=${user.activeStoreId}` : '';
      const response = await api.get(`/inventory/lenses/power-grid${storeParam}`);
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
    return <div className="p-4 text-center text-gray-500">Loading grid...</div>;
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-x-auto p-4">
      <h3 className="font-semibold text-gray-900 mb-4 flex items-center gap-2">
        <Grid3x3 className="w-5 h-5 text-bv-red-500" />
        Lens Power Grid (SPH × CYL)
      </h3>

      <div className="inline-block min-w-full">
        <table className="border-collapse">
          <thead>
            <tr>
              <th className="px-2 py-2 text-xs font-semibold text-gray-500 border border-gray-200">
                SPH/CYL
              </th>
              {cylValues.map((cyl) => (
                <th
                  key={cyl}
                  className="px-2 py-2 text-xs font-semibold text-gray-500 border border-gray-200 text-center"
                >
                  {cyl}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sphValues.map((sph) => (
              <tr key={sph}>
                <td className="px-2 py-2 text-xs font-semibold text-gray-700 border border-gray-200 bg-white">
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
                        'px-2 py-2 text-xs font-semibold text-center border border-gray-200',
                        inStock ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
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
          <span className="text-gray-700">In Stock</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-red-500" />
          <span className="text-gray-700">Out of Stock</span>
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
  const { user } = useAuth();
  const [brands, setBrands] = useState<BrandSellThrough[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, [days, user?.activeStoreId]);

  const loadData = async () => {
    setLoading(true);
    try {
      const storeParam = user?.activeStoreId ? `&store_id=${user.activeStoreId}` : '';
      const response = await api.get(`/inventory/sell-through-analysis?days=${days}${storeParam}`);
      setBrands(response.data?.brands || []);
    } catch (error) {
      // silently handle error
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-200">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-green-600" />
          Sell-Through Analysis ({days}d)
        </h3>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-500">Loading...</div>
      ) : brands.length === 0 ? (
        <div className="p-4 text-center text-gray-500">No data available</div>
      ) : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-white border-b border-gray-200 sticky top-0">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold text-gray-500">
                  Brand
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">
                  Sold
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">
                  Stocked
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">
                  Sell-Through %
                </th>
              </tr>
            </thead>
            <tbody>
              {brands.map((brand) => (
                <tr
                  key={brand.brand}
                  className="border-b border-gray-200 hover:bg-white"
                >
                  <td className="px-4 py-2 text-gray-900 font-medium">{brand.brand}</td>
                  <td className="px-4 py-2 text-right text-gray-700">
                    {brand.units_sold}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-700">
                    {brand.units_stocked}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <div className="w-16 bg-gray-100 rounded-full h-1.5">
                        <div
                          className="bg-green-500 h-1.5 rounded-full"
                          style={{
                            width: `${Math.min(brand.sell_through_percent, 100)}%`,
                          }}
                        />
                      </div>
                      <span className="text-green-600 font-semibold w-12 text-right">
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
  const { user } = useAuth();
  const [items, setItems] = useState<OverstockItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [threshold, setThreshold] = useState(3.0);

  useEffect(() => {
    loadData();
  }, [threshold, user?.activeStoreId]);

  const loadData = async () => {
    setLoading(true);
    try {
      const storeParam = user?.activeStoreId ? `&store_id=${user.activeStoreId}` : '';
      const response = await api.get(
        `/inventory/overstock-analysis?overstocking_threshold=${threshold}${storeParam}`
      );
      setItems(response.data?.items || []);
    } catch (error) {
      // silently handle error
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Package className="w-5 h-5 text-red-600" />
          <h3 className="font-semibold text-gray-900">Overstock Analysis</h3>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500">Threshold:</label>
          <input
            type="number"
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value))}
            min="1"
            step="0.5"
            className="w-16 px-2 py-1 bg-gray-100 border border-gray-300 rounded text-sm text-gray-700"
          />
          <span className="text-xs text-gray-500">x</span>
        </div>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-500">Loading...</div>
      ) : items.length === 0 ? (
        <div className="p-4 text-center text-gray-500">No overstock detected</div>
      ) : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-white border-b border-gray-200 sticky top-0">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold text-gray-500">
                  Product
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">
                  Stock
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">
                  Avg Monthly
                </th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">
                  Months
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.product_id}
                  className="border-b border-gray-200 hover:bg-white"
                >
                  <td className="px-4 py-2">
                    <div>
                      <p className="text-gray-900 font-medium text-xs">{item.product_name}</p>
                      <p className="text-gray-500 text-[10px]">{item.sku}</p>
                    </div>
                  </td>
                  <td className="px-4 py-2 text-right text-orange-600 font-semibold">
                    {item.current_stock}
                  </td>
                  <td className="px-4 py-2 text-right text-gray-700">
                    {item.avg_monthly_sales.toFixed(0)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <span className="font-semibold text-red-600">
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

// ============================================================================
// INTER-STORE TRANSFER RECOMMENDATIONS + STOCK ACCOUNTABILITY
// ============================================================================

interface TransferRec {
  product_id: string;
  product_name?: string;
  from_store: string;
  to_store: string;
  quantity: number;
  to_store_qty: number;
  from_store_qty: number;
}
interface ShrinkRow {
  store_id: string;
  audit_number?: string;
  shrinkage_percentage: number;
  custodian_name?: string | null;
}

export function TransferRecommendationsWidget() {
  const { user } = useAuth();
  const storeId = user?.activeStoreId;
  const [recs, setRecs] = useState<TransferRec[]>([]);
  const [shrink, setShrink] = useState<ShrinkRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      const sp = storeId ? `?store_id=${encodeURIComponent(storeId)}` : '';
      try {
        const [r, s] = await Promise.all([
          api.get(`/inventory/transfer-recommendations${sp}`).then((x) => x.data).catch(() => ({ recommendations: [] })),
          api.get(`/inventory/accountability/shrinkage${sp}`).then((x) => x.data).catch(() => ({ rows: [] })),
        ]);
        if (!alive) return;
        setRecs(r.recommendations || []);
        setShrink(s.rows || []);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [storeId]);

  if (loading) return <div className="p-6 text-center text-gray-500">Loading recommendations...</div>;

  return (
    <div className="space-y-6">
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="text-lg font-semibold text-gray-900 mb-1 flex items-center gap-2">
          <ArrowLeftRight className="w-5 h-5" /> Suggested inter-store transfers
        </h3>
        <p className="text-sm text-gray-500 mb-4">Refill this store's low/out products from stores holding a surplus.</p>
        {recs.length === 0 ? (
          <p className="text-sm text-gray-400 py-4">No transfers suggested — nothing is below reorder, or no other store has spare stock.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left text-xs font-semibold text-gray-500 uppercase">
                  <th className="px-3 py-2">Product</th>
                  <th className="px-3 py-2">From store</th>
                  <th className="px-3 py-2">To store</th>
                  <th className="px-3 py-2 text-right">Move qty</th>
                  <th className="px-3 py-2 text-right">Here now</th>
                </tr>
              </thead>
              <tbody>
                {recs.map((r) => (
                  <tr key={`${r.product_id}-${r.from_store}`} className="border-b border-gray-100">
                    <td className="px-3 py-2 text-gray-900">{r.product_name || r.product_id}</td>
                    <td className="px-3 py-2"><span className="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 text-xs">{r.from_store} ({r.from_store_qty})</span></td>
                    <td className="px-3 py-2"><span className="px-2 py-0.5 rounded bg-blue-50 text-blue-700 text-xs">{r.to_store}</span></td>
                    <td className="px-3 py-2 text-right font-semibold text-gray-900">{r.quantity}</td>
                    <td className="px-3 py-2 text-right text-red-600">{r.to_store_qty}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="text-lg font-semibold text-gray-900 mb-1 flex items-center gap-2">
          <ShieldAlert className="w-5 h-5" /> Stock accountability — recent shrinkage
        </h3>
        <p className="text-sm text-gray-500 mb-4">Completed-count shrinkage attributed to each store's assigned custodian.</p>
        {shrink.length === 0 ? (
          <p className="text-sm text-gray-400 py-4">No completed counts in the window.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-xs font-semibold text-gray-500 uppercase">
                <th className="px-3 py-2">Audit</th>
                <th className="px-3 py-2">Store</th>
                <th className="px-3 py-2">Custodian</th>
                <th className="px-3 py-2 text-right">Shrinkage %</th>
              </tr>
            </thead>
            <tbody>
              {shrink.map((s, i) => (
                <tr key={s.audit_number || i} className="border-b border-gray-100">
                  <td className="px-3 py-2 text-gray-600">{s.audit_number || '-'}</td>
                  <td className="px-3 py-2 text-gray-600">{s.store_id}</td>
                  <td className="px-3 py-2 text-gray-900">{s.custodian_name || <span className="text-gray-400">unassigned</span>}</td>
                  <td className={clsx('px-3 py-2 text-right font-semibold', (s.shrinkage_percentage || 0) >= 2 ? 'text-red-600' : 'text-gray-700')}>
                    {(s.shrinkage_percentage || 0).toFixed(1)}%
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
