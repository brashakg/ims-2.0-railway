// Inventory routes.
//
// Wave 2 split: the InventoryPage mega-page is gone. It held NINETEEN
// sections in useState (?tab= deep links only), while the menu linked three
// screens - most of the module was finished work nobody could reach. Each
// section is now a real page under the InventoryLayout:
//   /inventory/stock · /inventory/display-layout ·
//   /inventory/non-moving · /inventory/aging · /inventory/alerts ·
//   /inventory/transfers · /inventory/movements ·
//   /inventory/rebalance · /inventory/quarantine · /inventory/serial-numbers ·
//   /inventory/contact-lens · /inventory/sell-through · /inventory/overstock ·
//   /inventory/brand-insights · /inventory/collection-insights
//
// Two former tabs are NOT re-created, because each was a second
// implementation of a screen that already had a real route:
//   ?tab=stock-count -> /inventory/audit    (same StockAudit component; the
//                       tab leaked it to WORKSHOP_STAFF past the route gate)
//   ?tab=power-grid  -> /inventory/power-grid (the tab rendered the OLD
//                       products-based widget; the page is the typed
//                       lens-catalog grid that knows real lens stock)
//
// Legacy /inventory?tab=<x> deep links (QuickAdd, ReportCardsGrid, old
// bookmarks) forward via InventoryTabRedirect below.
//
// Stock redesign S5 (owner 2026-10-22/23): Buy Desk, Replenishment and Low
// stock became ONE Reorder list at /inventory/reorders (its own page, outside
// the layout: the accountant who raises POs opens it too). /inventory/low-stock,
// /inventory/replenishment and /catalog/buy-desk forward to it.
//
// Role gates come from ONE list, pages/inventory/inventoryRoles.ts. The same
// manager-ladder list used to be hand-typed four times in this file.
import { lazy } from 'react';
import { Route, Navigate, useSearchParams } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';
import { legacyTabTarget } from '../pages/inventory/legacyTabRedirect';
import {
  INVENTORY_MODULE_ROLES,
  INVENTORY_MANAGE_ROLES,
  POWER_GRID_ROLES,
  REORDER_LIST_ROLES,
  PRODUCT_STOCK_ROLES,
  STOCK_LOOKUP_ROLES,
} from '../pages/inventory/inventoryRoles';

const InventoryLayout = lazy(() => import('../pages/inventory/InventoryLayout').then(m => ({ default: m.InventoryLayout })));
const InventoryStockPage = lazy(() => import('../pages/inventory/InventoryStockPage').then(m => ({ default: m.InventoryStockPage })));
const InventoryMovementsPage = lazy(() => import('../pages/inventory/InventoryMovementsPage').then(m => ({ default: m.InventoryMovementsPage })));
const InventoryAlertsPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryAlertsPage })));
const InventoryDisplayLayoutPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryDisplayLayoutPage })));
const InventorySerialNumbersPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventorySerialNumbersPage })));
const InventoryAgingPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryAgingPage })));
const InventoryTransfersPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryTransfersPage })));
const InventoryNonMovingPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryNonMovingPage })));
const InventoryContactLensPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryContactLensPage })));
const InventorySellThroughPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventorySellThroughPage })));
const InventoryOverstockPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryOverstockPage })));
const InventoryBrandInsightsPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryBrandInsightsPage })));
const InventoryCollectionInsightsPage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryCollectionInsightsPage })));
const InventoryRebalancePage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryRebalancePage })));
const InventoryQuarantinePage = lazy(() => import('../pages/inventory/InventorySections').then(m => ({ default: m.InventoryQuarantinePage })));
const PowerGridPage = lazy(() => import('../pages/inventory/PowerGridPage'));
const OnlineStockPage = lazy(() => import('../pages/inventory/OnlineStockPage'));
const StockLookupPage = lazy(() => import('../pages/inventory/StockLookupPage'));
const ReorderListPage = lazy(() => import('../pages/inventory/ReorderListPage').then(m => ({ default: m.ReorderListPage })));
const StockAudit = lazy(() => import('../pages/inventory/StockAudit').then(m => ({ default: m.StockAudit })));
const StockCountResult = lazy(() => import('../pages/inventory/StockCountResult').then(m => ({ default: m.StockCountResult })));
const ProductStockPage = lazy(() => import('../pages/inventory/ProductStockPage').then(m => ({ default: m.ProductStockPage })));
const OpeningStockImport = lazy(() => import('../pages/inventory/OpeningStockImport').then(m => ({ default: m.OpeningStockImport })));

// Legacy ?tab= mapper - logic in pages/inventory/legacyTabRedirect.ts so it
// is testable without mounting the router.
function InventoryTabRedirect() {
  const [searchParams] = useSearchParams();
  return <Navigate to={legacyTabTarget(searchParams)} replace />;
}

/** The former mega-page tabs: every module member saw every tab, so each
 *  section keeps the module gate (the two tighter screens live below). */
const section = (path: string, El: ReturnType<typeof lazy>) => (
  <Route
    key={path}
    path={path}
    element={
      <ProtectedRoute allowedRoles={INVENTORY_MODULE_ROLES}>
        <El />
      </ProtectedRoute>
    }
  />
);

export const inventoryRoutes = (
  <>
    {/* Inventory module - layout + one page per section */}
    <Route
      path="inventory"
      element={
        <ProtectedRoute allowedRoles={INVENTORY_MODULE_ROLES}>
          <InventoryLayout />
        </ProtectedRoute>
      }
    >
      {/* Bare /inventory (incl. legacy ?tab= links) -> the right section */}
      <Route index element={<InventoryTabRedirect />} />
      {section('stock', InventoryStockPage)}
      {section('display-layout', InventoryDisplayLayoutPage)}
      {section('non-moving', InventoryNonMovingPage)}
      {section('aging', InventoryAgingPage)}
      {section('alerts', InventoryAlertsPage)}
      {section('transfers', InventoryTransfersPage)}
      {section('movements', InventoryMovementsPage)}
      {section('rebalance', InventoryRebalancePage)}
      {section('quarantine', InventoryQuarantinePage)}
      {section('serial-numbers', InventorySerialNumbersPage)}
      {section('contact-lens', InventoryContactLensPage)}
      {section('sell-through', InventorySellThroughPage)}
      {section('overstock', InventoryOverstockPage)}
      {section('brand-insights', InventoryBrandInsightsPage)}
      {section('collection-insights', InventoryCollectionInsightsPage)}
    </Route>

    {/* Standalone full pages - paths and gates unchanged from before the
        split; the role lists now come from inventoryRoles.ts instead of
        being re-typed per route. */}

    {/* S5: THE Reorder list - what to buy, per product and shop. */}
    <Route
      path="inventory/reorders"
      element={
        <ProtectedRoute allowedRoles={REORDER_LIST_ROLES}>
          <ReorderListPage />
        </ProtectedRoute>
      }
    />
    {/* Retired into the Reorder list (S5): old bookmarks forward. */}
    <Route path="inventory/replenishment" element={<Navigate to="/inventory/reorders" replace />} />
    <Route path="inventory/low-stock" element={<Navigate to="/inventory/reorders" replace />} />

    {/* Phase 4: Stock Audit (the blind day-end count - also where the old
        ?tab=stock-count deep link lands now) */}
    <Route
      path="inventory/audit"
      element={
        <ProtectedRoute allowedRoles={INVENTORY_MANAGE_ROLES}>
          <StockAudit />
        </ProtectedRoute>
      }
    />

    {/* S1: a finished count's result - coverage, moved styles, the exact
        missing pieces, the extras, and the write-off (admin) / send for
        write-off (manager) door. Its own address, not a hidden panel. */}
    <Route
      path="inventory/audit/:countId"
      element={
        <ProtectedRoute allowedRoles={INVENTORY_MANAGE_ROLES}>
          <StockCountResult />
        </ProtectedRoute>
      }
    />

    {/* S4: one product's stock and its ONE average cost + cost history
        (its own address). Outside the module layout: the accountant, who
        accepts bills and corrects the average, is not a module member. */}
    <Route
      path="inventory/stock/:productId"
      element={
        <ProtectedRoute allowedRoles={PRODUCT_STOCK_ROLES}>
          <ProductStockPage />
        </ProtectedRoute>
      }
    />

    {/* Go-live: Opening-Stock Importer */}
    <Route
      path="inventory/opening-stock"
      element={
        <ProtectedRoute allowedRoles={INVENTORY_MANAGE_ROLES}>
          <OpeningStockImport />
        </ProtectedRoute>
      }
    />

    <Route
      path="inventory/power-grid"
      element={
        <ProtectedRoute allowedRoles={POWER_GRID_ROLES}>
          <PowerGridPage />
        </ProtectedRoute>
      }
    />

    {/* D7b: the counter's read-only stock lookup - outside the module
        layout, which counter roles cannot open. */}
    <Route
      path="inventory/lookup"
      element={
        <ProtectedRoute allowedRoles={STOCK_LOOKUP_ROLES}>
          <StockLookupPage />
        </ProtectedRoute>
      }
    />

    <Route
      path="inventory/online-sync"
      element={
        <ProtectedRoute allowedRoles={INVENTORY_MANAGE_ROLES}>
          <OnlineStockPage />
        </ProtectedRoute>
      }
    />
  </>
);
