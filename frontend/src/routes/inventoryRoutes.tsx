// Inventory routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const InventoryPage = lazy(() => import('../pages/inventory/InventoryPage').then(m => ({ default: m.InventoryPage })));
const PowerGridPage = lazy(() => import('../pages/inventory/PowerGridPage'));
const OnlineStockPage = lazy(() => import('../pages/inventory/OnlineStockPage'));
const StockReplenishment = lazy(() => import('../pages/inventory/StockReplenishment').then(m => ({ default: m.StockReplenishment })));
const StockAudit = lazy(() => import('../pages/inventory/StockAudit').then(m => ({ default: m.StockAudit })));
const OpeningStockImport = lazy(() => import('../pages/inventory/OpeningStockImport').then(m => ({ default: m.OpeningStockImport })));

export const inventoryRoutes = (
  <>
    {/* Inventory */}
    <Route
      path="inventory"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF']}
        >
          <InventoryPage />
        </ProtectedRoute>
      }
    />

    {/* Phase 4: Stock Replenishment */}
    <Route
      path="inventory/replenishment"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER']}>
          <StockReplenishment />
        </ProtectedRoute>
      }
    />

    {/* Phase 4: Stock Audit */}
    <Route
      path="inventory/audit"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER']}>
          <StockAudit />
        </ProtectedRoute>
      }
    />
    {/* Go-live: Opening-Stock Importer */}
    <Route
      path="inventory/opening-stock"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER']}>
          <OpeningStockImport />
        </ProtectedRoute>
      }
    />
    <Route
      path="inventory/power-grid"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'OPTOMETRIST']}>
          <PowerGridPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="inventory/online-sync"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER']}>
          <OnlineStockPage />
        </ProtectedRoute>
      }
    />
  </>
);
