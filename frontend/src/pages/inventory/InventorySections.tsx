// ============================================================================
// IMS 2.0 - Inventory sections: the thin pages
// ============================================================================
// Wave 2 split of the old InventoryPage. Each export below is one former tab
// on its own URL under the InventoryLayout. They are thin on purpose - the
// real screens are the widgets in components/inventory, which fetch their own
// data; the tab blocks in the old page were exactly these wrappers.
//
// ponytail: one file, not thirteen. The layout lazy-imports this module once,
// which is the same chunking the old mega-page gave these widgets. Split a
// section out into its own file when it grows real logic of its own.
//
// NOT here:
//   stock-count  -> /inventory/audit (StockAudit, the standalone page - the
//                   tab was a second address for it with a LOOSER role gate)
//   power-grid   -> /inventory/power-grid (PowerGridPage - the tab rendered
//                   the OLD products-based widget instead)

import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, Package } from 'lucide-react';
import { StockAlertsOverview } from '../../components/inventory/StockAlertsOverview';
import { StockTransferManagement } from '../../components/inventory/StockTransferManagement';
import { ReorderDashboard } from '../../components/inventory/ReorderDashboard';
import { SerialNumberTracker } from '../../components/inventory/SerialNumberTracker';
import { StockAgingReport } from '../../components/inventory/StockAgingReport';
import { NonMovingStockWidget } from '../../components/inventory/NonMovingStockWidget';
import {
  ContactLensExpiryWidget,
  ContactLensInventoryWidget,
  OverstockAnalysisWidget,
  SellThroughAnalysisWidget,
  TransferRecommendationsWidget,
} from '../../components/inventory/AdvancedInventoryFeatures';
import { QuarantineQueue } from '../../components/inventory/QuarantineQueue';
import { BrandInsightsWidget } from '../../components/inventory/BrandInsightsWidget';
import { CollectionInsightsWidget } from '../../components/inventory/CollectionInsightsWidget';
import { DisplayLayoutPanel } from '../../components/inventory/DisplayLayoutPanel';
import { useInventoryContext } from './InventoryLayout';
import { useLowStock } from './inventoryQueries';

/** /inventory/alerts - stock alert overview. */
export function InventoryAlertsPage() {
  return (
    <div className="card">
      <StockAlertsOverview />
    </div>
  );
}

/** /inventory/display-layout - floor map of fixtures + side detail panel.
 *  ?fixture=<id> deep-links a fixture pre-selected (the Stock ledger Zone
 *  cell links it); the param is dropped once consumed so re-navigation works. */
export function InventoryDisplayLayoutPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const fixtureParam = searchParams.get('fixture');
  return (
    <DisplayLayoutPanel
      initialFixtureId={fixtureParam}
      onFixtureSelectionConsumed={() => {
        if (!fixtureParam) return;
        const next = new URLSearchParams(searchParams);
        next.delete('fixture');
        setSearchParams(next, { replace: true });
      }}
    />
  );
}

/** /inventory/low-stock - items at or below their reorder threshold. */
export function InventoryLowStockPage() {
  const { storeId } = useInventoryContext();
  const lowStockQ = useLowStock(storeId || undefined);
  const lowStockItems = lowStockQ.data ?? [];
  return (
    <div className="card">
      {lowStockQ.isPending ? (
        <div className="flex items-center justify-center py-12">
          <span className="w-8 h-8 border-2 border-bv-red-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : lowStockItems.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No low stock items</p>
        </div>
      ) : (
        <div className="space-y-3">
          {lowStockItems.map(item => (
            <div
              key={item.id}
              className="flex items-center justify-between p-4 bg-amber-50 border border-amber-200 rounded-lg"
            >
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-amber-100 rounded-lg flex items-center justify-center">
                  <AlertTriangle className="w-5 h-5 text-amber-600" />
                </div>
                <div>
                  <p className="font-medium text-gray-900">{item.name}</p>
                  <p className="text-sm text-gray-500">{item.sku} • {item.brand}</p>
                </div>
              </div>
              <div className="text-right">
                <p className="text-lg font-bold text-amber-600">{item.stock} left</p>
                <p className="text-xs text-gray-500">Min: {item.lowStockThreshold || item.minStock || 5}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** /inventory/reorders - reorder dashboard. */
export function InventoryReordersPage() {
  return (
    <div className="space-y-4">
      <ReorderDashboard />
    </div>
  );
}

/** /inventory/serial-numbers - serial number tracker. */
export function InventorySerialNumbersPage() {
  return (
    <div className="space-y-4">
      <SerialNumberTracker />
    </div>
  );
}

/** /inventory/aging - stock aging report. */
export function InventoryAgingPage() {
  return (
    <div className="space-y-4">
      <StockAgingReport />
    </div>
  );
}

/** /inventory/transfers - inter-store transfer management. */
export function InventoryTransfersPage() {
  return (
    <div className="space-y-4">
      <StockTransferManagement />
    </div>
  );
}

/** /inventory/non-moving - non-moving stock (90-day window). */
export function InventoryNonMovingPage() {
  return <NonMovingStockWidget />;
}

/** /inventory/contact-lens - CL inventory + expiry (FEFO). */
export function InventoryContactLensPage() {
  return (
    <div className="space-y-4">
      <ContactLensInventoryWidget />
      <ContactLensExpiryWidget />
    </div>
  );
}

/** /inventory/sell-through - sell-through analysis. */
export function InventorySellThroughPage() {
  return <SellThroughAnalysisWidget />;
}

/** /inventory/overstock - overstock analysis. */
export function InventoryOverstockPage() {
  return <OverstockAnalysisWidget />;
}

/** /inventory/brand-insights - brand-wise KPI insights. */
export function InventoryBrandInsightsPage() {
  return <BrandInsightsWidget />;
}

/** /inventory/collection-insights - collection-wise KPI insights. */
export function InventoryCollectionInsightsPage() {
  return <CollectionInsightsWidget />;
}

/** /inventory/rebalance - inter-store transfer suggestions. */
export function InventoryRebalancePage() {
  return <TransferRecommendationsWidget />;
}

/** /inventory/quarantine - F21 defective quarantine queue. */
export function InventoryQuarantinePage() {
  return <QuarantineQueue />;
}
