// Online Store (BVI merge) + Collections merchandising routes. Moved verbatim
// from App.tsx (route-registry split); paths, elements and role gates are
// unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const OnlineStorePage = lazy(() => import('../pages/online-store/OnlineStorePage'));
const OnlineProductsPage = lazy(() => import('../pages/online-store/OnlineProductsPage'));
const OnlineCustomersPage = lazy(() => import('../pages/online-store/OnlineCustomersPage'));
const CollectionsPage = lazy(() => import('../pages/online-store/CollectionsPage'));
const CollectionBrowsePage = lazy(() => import('../pages/online-store/CollectionBrowsePage'));
// Collections Phase 1 — merchandising surface (list / chip builder / KPI detail)
const CollectionsListPage = lazy(() => import('../pages/collections/CollectionsListPage'));
const CollectionNewPage = lazy(() => import('../pages/collections/CollectionNewPage'));
const CollectionDetailPage = lazy(() => import('../pages/collections/CollectionDetailPage'));
const MenusPage = lazy(() => import('../pages/online-store/MenusPage'));
const DiscountRulesPage = lazy(() => import('../pages/online-store/DiscountRulesPage'));
const DesignQueuePage = lazy(() => import('../pages/online-store/DesignQueuePage'));
const OnlineOrdersPage = lazy(() => import('../pages/online-store/OnlineOrdersPage'));
const RefundReviewsPage = lazy(() => import('../pages/online-store/RefundReviewsPage'));
const OnlineStockTallyPage = lazy(() => import('../pages/online-store/OnlineStockPage'));
const OnlineStoreHealthPage = lazy(() => import('../pages/online-store/OnlineStoreHealthPage'));
const OndcSellerPage = lazy(() => import('../pages/online-store/OndcSellerPage'));
const OnlineShopifySyncPage = lazy(() => import('../pages/online-store/OnlineShopifySyncPage'));

export const onlineStoreRoutes = (
  <>
    {/* Online Store — consolidated e-commerce (BVI merge) module shell.
        Phase 1 foundation; gated to the catalog/design roles. */}
    <Route
      path="online-store"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <OnlineStorePage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Products / PIM. Server-paged list of the
        catalog_products master with truthful per-row website
        state; admin-only per-row push. Same catalog/design role
        gate as the module shell. */}
    <Route
      path="online-store/products"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <OnlineProductsPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Collections editor (BVI Phase 2). Same
        catalog/design role gate as the module shell. */}
    <Route
      path="online-store/collections"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <CollectionsPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Collection BROWSE (unification step-13).
        Read-only fast-path over materialised membership
        (/api/v1/collections). Same module role gate. */}
    <Route
      path="online-store/collections/browse"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <CollectionBrowsePage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Discount Rules (rebuild of BVI DiscountRule).
        Owner-editable automatic ONLINE storefront discount rules.
        Pricing surface -> SUPERADMIN/ADMIN/CATALOG_MANAGER only
        (DESIGN_MANAGER excluded), matching the backend gate. */}
    <Route
      path="online-store/discount-rules"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']}
        >
          <DiscountRulesPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Menus / Mega-menu editor (BVI Phase 3). Same
        catalog/design role gate as the module shell. */}
    <Route
      path="online-store/menus"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <MenusPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Image design queue (BVI Phase 4). Same
        catalog/design role gate as the module shell; in-page
        Approve/Reject is further gated to ADMIN/DESIGN_MANAGER. */}
    <Route
      path="online-store/images"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <DesignQueuePage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Online orders (BVI Phase 3b). Read-only list
        of storefront orders flowing into the IMS books; the in-page
        Re-map action is further gated to SUPERADMIN/ADMIN. Same
        catalog/design role gate as the module shell. */}
    <Route
      path="online-store/orders"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <OnlineOrdersPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Refund reviews (Shopify refund -> GST credit
        note). The ACCOUNTANT-facing consumer for the refund review
        queue: confirm posts the credit note + restock, reject
        declines. Gated SUPERADMIN / ADMIN / ACCOUNTANT to match the
        backend router (the books are the accountant's). */}
    <Route
      path="online-store/refund-reviews"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}
        >
          <RefundReviewsPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Customers (BVI Phase 3). Read-only list of
        the online-origin (Shopify-joined) customer segment. Same
        catalog/design role gate as the module shell. */}
    <Route
      path="online-store/customers"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <OnlineCustomersPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Stock tally (BVI Phase 5). READ-ONLY
        reconciliation of online-listed qty vs real on-hand vs
        reserved, flagging oversell-risk. No stock is reserved /
        mutated here (that write-path allocation is a deferred
        follow-up). Same catalog/design role gate as the shell. */}
    <Route
      path="online-store/stock-tally"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <OnlineStockTallyPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Store health (BVI Phase 5). Read-only
        pre-cutover readiness dashboard: orphan SKUs, attribute
        coverage, barcode match + a composite score. Same
        catalog/design role gate as the module shell. */}
    <Route
      path="online-store/store-health"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <OnlineStoreHealthPage />
        </ProtectedRoute>
      }
    />

    {/* ONDC Seller Node (BVI-20): India open commerce network admin page.
        DARK by default; gated to SUPERADMIN / ADMIN. */}
    <Route
      path="online-store/ondc"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN']}
        >
          <OndcSellerPage />
        </ProtectedRoute>
      }
    />

    {/* Online Store — Shopify sync control panel (BVI Phase 6).
        STATUS + DRY-RUN cockpit for the (dark) IMS -> Shopify push
        engine. Same catalog/design module role gate as the shell;
        the live "Go live" cutover is further gated to ADMIN/
        SUPERADMIN in-page AND stays owner-armed behind the backend
        triple-gate (this UI never arms/bypasses it). */}
    <Route
      path="online-store/shopify"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER']}
        >
          <OnlineShopifySyncPage />
        </ProtectedRoute>
      }
    />

    {/* Collections Phase 1 — merchandising surface over the ecom
        collections system: KPI list, governed chip builder, and
        per-collection insights. STORE_MANAGER is view-only (the
        builder button is hidden in-page; backend enforces write
        authz). */}
    <Route
      path="collections"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER']}
        >
          <CollectionsListPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="collections/new"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER']}
        >
          <CollectionNewPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="collections/:id"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER']}
        >
          <CollectionDetailPage />
        </ProtectedRoute>
      }
    />
  </>
);
