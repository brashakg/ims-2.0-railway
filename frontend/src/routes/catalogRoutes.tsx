// Catalog + pricing routes. Moved verbatim from App.tsx (route-registry
// split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route, Navigate } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

// /catalog — the Catalog Manager: photo-grid browse over the product spine +
// the imported-needs-review queue, with the slide-over drawer (view / edit /
// approve). The sidebar "Catalog" item lands here; "+ Add product" links on.
const CatalogManagerPage = lazy(() => import('../pages/catalog/CatalogManagerPage'));
// /catalog/add — the single product-add door (Quick Add). Guided + Bulk modes
// were removed; Quick Add absorbed every field/section Guided had.
const QuickAddPage = lazy(() => import('../pages/catalog/QuickAddPage'));
// Catalog Autopilot was removed entirely (owner 2026-08-30: unused feature);
// /catalog/autopilot redirects to /catalog/add. The old page file
// is kept in the tree but no longer imported here.
const BuyDeskPage = lazy(() => import('../pages/catalog/BuyDeskPage'));
// /catalog/scorecard — per-user cataloguing performance (volume, approvals,
// corrections, QC error rate) + the random-sample QC review workflow.
const CataloguingScorecardPage = lazy(() => import('../pages/catalog/CataloguingScorecardPage'));
// /catalog/quick-share — pick products -> share as a branded PDF / save a
// temporary (auto-expiring) set. Broad staff surface (anyone helping a customer).
const QuickSharePage = lazy(() => import('../pages/catalogue/QuickSharePage'));
const PricingOffersPage = lazy(() => import('../pages/pricing/PricingOffersPage'));

export const catalogRoutes = (
  <>
    {/* Catalog Manager — browse the spine + review the imported
        queue; roles mirror the navConfig 'catalog' item. */}
    <Route
      path="catalog"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']}>
          <CatalogManagerPage />
        </ProtectedRoute>
      }
    />

    {/* Catalog / Add Product — single door (Quick Add) */}
    <Route
      path="catalog/add"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']}>
          <QuickAddPage />
        </ProtectedRoute>
      }
    />

    {/* Cataloguing Scorecard + QC review — manager ladder; gate
        mirrors the backend rbac rows for /products/cataloguing-
        scorecard + /products/qc-samples*. */}
    <Route
      path="catalog/scorecard"
      element={
        <ProtectedRoute
          allowedRoles={[
            'SUPERADMIN',
            'ADMIN',
            'AREA_MANAGER',
            'STORE_MANAGER',
            'CATALOG_MANAGER',
          ]}
        >
          <CataloguingScorecardPage />
        </ProtectedRoute>
      }
    />

    {/* Quick Share — pick products -> share as PDF / temp set.
        Broad staff gate (anyone helping a customer); backend
        /catalogue routes are AUTHENTICATED. */}
    <Route
      path="catalog/quick-share"
      element={
        <ProtectedRoute
          allowedRoles={[
            'SUPERADMIN',
            'ADMIN',
            'AREA_MANAGER',
            'STORE_MANAGER',
            'CATALOG_MANAGER',
            'SALES_STAFF',
            'SALES_CASHIER',
            'OPTOMETRIST',
            'CASHIER',
          ]}
        >
          <QuickSharePage />
        </ProtectedRoute>
      }
    />

    {/* Buy Desk — the one-screen catalog -> purchase landing */}
    <Route
      path="catalog/buy-desk"
      element={
        <ProtectedRoute
          allowedRoles={[
            'SUPERADMIN',
            'ADMIN',
            'CATALOG_MANAGER',
            'AREA_MANAGER',
            'STORE_MANAGER',
            'ACCOUNTANT',
          ]}
        >
          <BuyDeskPage />
        </ProtectedRoute>
      }
    />

    {/* Catalog Autopilot was removed (owner 2026-08-30). Old bookmarks
        land on /catalog/add. */}
    <Route
      path="catalog/autopilot"
      element={<Navigate to="/catalog/add" replace />}
    />

    {/* Pricing & Offers — bulk price + bulk offer (cap-enforced, dry-run-first) */}
    <Route
      path="catalog/pricing"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']}>
          <PricingOffersPage />
        </ProtectedRoute>
      }
    />
  </>
);
