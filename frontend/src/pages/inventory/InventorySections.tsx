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
//   low-stock, reorders -> /inventory/reorders (ReorderListPage, S5: THE one
//                   Reorder list; /inventory/low-stock forwards to it)

import { useSearchParams } from 'react-router-dom';
import { StockAlertsOverview } from '../../components/inventory/StockAlertsOverview';
import { StockTransferManagement } from '../../components/inventory/StockTransferManagement';
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
