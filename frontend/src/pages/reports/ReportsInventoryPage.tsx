// ============================================================================
// IMS 2.0 - Reports > Inventory  (/reports/inventory)
// ============================================================================
// The old ReportsPage `activeTab === 'inventory'` block on its own URL.
// The Available-Reports grid stays where it rendered before: ABOVE the
// inventory widgets (on the old page it sat between the sales block and the
// inventory block, so every non-sales tab saw it first).

import { Download, Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { exportToCSV } from '../../utils/exportUtils';
import { TaxCodeAuditCard } from './sections/TaxCodeAuditCard';
import { WorkshopProductivityCard } from './sections/WorkshopProductivityCard';
import { ReportCardsGrid } from './ReportCardsGrid';
import { useReportsContext } from './ReportsLayout';
import {
  useBrandSellthrough,
  useNonMovingStock,
  usePurchaseRecommendations,
  useStockCount,
} from './reportsQueries';

export function ReportsInventoryPage() {
  const toast = useToast();
  const { storeId, startDate, endDate, canExport } = useReportsContext();

  const stockQ = useStockCount({ storeId, startDate, endDate });
  const brandQ = useBrandSellthrough({ storeId, startDate, endDate });
  const nmsQ = useNonMovingStock(storeId);
  const recsQ = usePurchaseRecommendations(storeId);

  const stockCount = stockQ.data;
  const brandSellthrough = brandQ.data ?? [];
  const nonMovingStock = nmsQ.data ?? [];
  const purchaseRecs = recsQ.data?.recommendations ?? [];
  const purchaseRecsSummary = recsQ.data?.summary ?? null;

  return (
    <>
      <ReportCardsGrid category="inventory" />

      <div className="grid grid-cols-1 laptop:grid-cols-2 gap-4">
        {/* Tax-Code Audit — go-live readiness (full width, finance roles) */}
        {canExport && (
          <div className="laptop:col-span-2">
            <TaxCodeAuditCard storeId={storeId} canExport={canExport} />
          </div>
        )}
        {/* Stock Count Card */}
        <div className="chart-card">
          <div className="chart-head">
            <h3>Stock Summary</h3>
          </div>
          <div className="chart-body">
          {stockQ.isPending ? (
            <div className="h-32 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : (
            <div className="space-y-3">
              {stockCount ? (
                <>
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500">Total Items:</span>
                    <span className="font-medium text-gray-900">{stockCount.summary?.total_items || 0}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500">Total Quantity:</span>
                    <span className="font-medium text-gray-900">{stockCount.summary?.total_quantity || 0} units</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-gray-500">Total Value:</span>
                    <span className="font-medium text-gray-900">₹{((stockCount.summary?.total_value || 0) / 100000).toFixed(2)}L</span>
                  </div>
                </>
              ) : (
                <p className="text-gray-500">No data available</p>
              )}
            </div>
          )}
          </div>
        </div>

        {/* Brand Sell-through Card */}
        <div className="chart-card">
          <div className="chart-head">
            <h3>Brand Performance</h3>
          </div>
          <div className="chart-body">
          {brandQ.isPending ? (
            <div className="h-32 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : brandSellthrough.length === 0 ? (
            <div className="py-8 text-center text-gray-500">
              <p>No brand data available</p>
            </div>
          ) : (
            <div className="space-y-2">
              {brandSellthrough.slice(0, 5).map((brand, idx: number) => (
                <div key={idx} className="flex items-center justify-between py-2 border-b border-gray-200 last:border-b-0">
                  <span className="text-sm text-gray-600 truncate">{brand.brand}</span>
                  <div className="text-right">
                    <p className="text-sm font-medium text-gray-900">{brand.quantity_sold} units</p>
                    <p className="text-xs text-gray-500">{brand.sellthrough_percent || 0}% sell-through</p>
                  </div>
                </div>
              ))}
            </div>
          )}
          </div>
        </div>

        {/* Non-moving Stock — full-width table below the 2-col grid.
            Surfaces products with no sale in the last 90 days. Never-sold
            SKUs float to the top. Read by store managers making clearance
            + transfer decisions. */}
        <div className="chart-card laptop:col-span-2">
          <div className="chart-head">
            <div>
              <h3>Non-moving Stock (90+ days)</h3>
              <p className="text-xs text-gray-500 mt-1">
                {nonMovingStock.length} product{nonMovingStock.length === 1 ? '' : 's'} with no sales in the last 90 days
              </p>
            </div>
            <div className="spacer" />
            {canExport && nonMovingStock.length > 0 && (
              <button
                onClick={() => {
                  exportToCSV(
                    nonMovingStock.map(p => ({
                      sku: p.sku || '',
                      brand: p.brand || '',
                      model: p.model || '',
                      category: p.category || '',
                      mrp: p.mrp,
                      last_sold_at: p.last_sold_at || 'Never sold',
                      days_since_sold: p.never_sold ? 'Never' : String(p.days_since_sold ?? '-'),
                      total_sold_all_time: p.total_sold_all_time,
                    })),
                    'non_moving_stock_90d',
                    [
                      { key: 'sku', label: 'SKU' },
                      { key: 'brand', label: 'Brand' },
                      { key: 'model', label: 'Model' },
                      { key: 'category', label: 'Category' },
                      { key: 'mrp', label: 'MRP (₹)' },
                      { key: 'last_sold_at', label: 'Last Sold' },
                      { key: 'days_since_sold', label: 'Days Since' },
                      { key: 'total_sold_all_time', label: 'Lifetime Units Sold' },
                    ]
                  );
                  toast.success('Non-moving stock exported');
                }}
                className="btn sm flex items-center gap-1"
              >
                <Download className="w-4 h-4" />
                Export CSV
              </button>
            )}
          </div>
          <div className="chart-body">
          {nmsQ.isPending ? (
            <div className="h-32 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : nonMovingStock.length === 0 ? (
            <div className="py-8 text-center text-gray-500">
              <p>No stale inventory detected — everything has turned over in the last 90 days.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="st-table">
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>Brand · Model</th>
                    <th>Category</th>
                    <th className="right">MRP</th>
                    <th>Last Sold</th>
                    <th className="right">Days Stale</th>
                  </tr>
                </thead>
                <tbody>
                  {nonMovingStock.slice(0, 50).map((p, idx) => (
                    <tr key={p.product_id || p.sku || idx}>
                      <td className="font-mono text-xs text-gray-700">{p.sku || '-'}</td>
                      <td className="text-gray-900">
                        <span className="font-medium">{p.brand || 'Unbranded'}</span>
                        {p.model ? <span className="text-gray-500"> · {p.model}</span> : null}
                      </td>
                      <td className="text-gray-600">{p.category || '-'}</td>
                      <td className="right text-gray-700">₹{(p.mrp || 0).toLocaleString('en-IN')}</td>
                      <td className="text-gray-600">
                        {p.never_sold
                          ? <span className="text-red-600 font-medium">Never sold</span>
                          : (p.last_sold_at ? new Date(p.last_sold_at).toLocaleDateString('en-IN') : '-')}
                      </td>
                      <td className="right">
                        <span className={`font-medium ${
                          p.never_sold || (p.days_since_sold ?? 0) >= 180 ? 'text-red-600'
                          : (p.days_since_sold ?? 0) >= 120 ? 'text-orange-600'
                          : 'text-yellow-600'
                        }`}>
                          {p.never_sold ? '∞' : p.days_since_sold}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {nonMovingStock.length > 50 && (
                <p className="text-xs text-gray-500 mt-3">
                  Showing first 50 of {nonMovingStock.length}. Export CSV for the full list.
                </p>
              )}
            </div>
          )}
          </div>
        </div>

        {/* R2 — Purchase Recommendations.
            Combines velocity (last 90d) × current stock × reorder point
            to surface what the buyer should reorder now, ranked by
            revenue at risk × confidence. Spec: TECHCHERRY_PORT_SCOPE §6. */}
        <div className="chart-card laptop:col-span-2">
          <div className="chart-head">
            <div>
              <h3>Purchase Recommendations</h3>
              <p className="text-xs text-gray-500 mt-1">
                {purchaseRecsSummary
                  ? `${purchaseRecsSummary.total_recommendations} SKU${purchaseRecsSummary.total_recommendations === 1 ? '' : 's'} to reorder · ${purchaseRecsSummary.total_suggested_units} units suggested · ₹${(purchaseRecsSummary.estimated_revenue_at_risk / 100000).toFixed(2)}L revenue at risk`
                  : 'Based on 90-day velocity × current stock × reorder point'}
              </p>
            </div>
            <div className="spacer" />
            {canExport && purchaseRecs.length > 0 && (
              <button
                onClick={() => {
                  exportToCSV(
                    purchaseRecs.map(p => ({
                      name: p.name || '',
                      brand: p.brand || '',
                      category: p.category || '',
                      velocity_90d: p.velocity_90d,
                      current_stock: p.current_stock,
                      reorder_point: p.reorder_point,
                      suggested_order_qty: p.suggested_order_qty,
                      avg_selling_price: p.avg_selling_price,
                      estimated_revenue_impact: p.estimated_revenue_impact,
                      estimated_margin: p.estimated_margin,
                      confidence: p.confidence,
                      reason: p.reason,
                    })),
                    'purchase_recommendations',
                    [
                      { key: 'name', label: 'Product' },
                      { key: 'brand', label: 'Brand' },
                      { key: 'category', label: 'Category' },
                      { key: 'velocity_90d', label: 'Velocity (90d)' },
                      { key: 'current_stock', label: 'Current Stock' },
                      { key: 'reorder_point', label: 'Reorder Point' },
                      { key: 'suggested_order_qty', label: 'Suggested Qty' },
                      { key: 'avg_selling_price', label: 'Avg Price (₹)' },
                      { key: 'estimated_revenue_impact', label: 'Revenue Impact (₹)' },
                      { key: 'estimated_margin', label: 'Est. Margin (₹)' },
                      { key: 'confidence', label: 'Confidence' },
                      { key: 'reason', label: 'Reasoning' },
                    ]
                  );
                  toast.success('Purchase recommendations exported');
                }}
                className="btn sm flex items-center gap-1"
              >
                <Download className="w-4 h-4" />
                Export CSV
              </button>
            )}
          </div>
          <div className="chart-body">
          {recsQ.isPending ? (
            <div className="h-32 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
            </div>
          ) : purchaseRecs.length === 0 ? (
            <div className="py-8 text-center text-gray-500">
              <p>No purchase recommendations — stock is healthy relative to demand.</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="st-table">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Category</th>
                    <th className="right">90d Velocity</th>
                    <th className="right">Stock</th>
                    <th className="right">Reorder Pt</th>
                    <th className="right">Suggest Qty</th>
                    <th className="right">Revenue Impact</th>
                    <th className="right">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {purchaseRecs.slice(0, 50).map((p, idx) => (
                    <tr key={p.product_id || idx}>
                      <td className="text-gray-900">
                        <span className="font-medium">{p.brand || 'Unbranded'}</span>
                        {p.name ? <span className="text-gray-500"> · {p.name}</span> : null}
                      </td>
                      <td className="text-gray-600">{p.category || '-'}</td>
                      <td className="right text-gray-700">{p.velocity_90d}</td>
                      <td className="right">
                        <span className={p.current_stock <= p.reorder_point ? 'text-red-600 font-medium' : 'text-gray-700'}>
                          {p.current_stock}
                        </span>
                      </td>
                      <td className="right text-gray-600">{p.reorder_point || '-'}</td>
                      <td className="right font-medium text-bv-red-600">{p.suggested_order_qty}</td>
                      <td className="right text-gray-900">
                        ₹{(p.estimated_revenue_impact / 1000).toFixed(1)}K
                      </td>
                      <td className="right">
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                          p.confidence === 'HIGH' ? 'bg-green-100 text-green-700'
                          : p.confidence === 'MEDIUM' ? 'bg-yellow-100 text-yellow-700'
                          : 'bg-gray-100 text-gray-600'
                        }`}>
                          {p.confidence}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {purchaseRecs.length > 50 && (
                <p className="text-xs text-gray-500 mt-3">
                  Showing top 50 of {purchaseRecs.length}. Export CSV for the full list.
                </p>
              )}
            </div>
          )}
          </div>
        </div>

        {/* Workshop Productivity — per-technician scorecard (jobs, turnaround,
            QC-fail %, on-time %). Built end-to-end but previously unwired into
            the Reports UI; surfaced here as a full-width operations report. */}
        <div className="laptop:col-span-2">
          <WorkshopProductivityCard storeId={storeId} />
        </div>
      </div>
    </>
  );
}

export default ReportsInventoryPage;
