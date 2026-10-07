// Purchase & procurement routes.
//
// Wave 1 split: the old /purchase tab container (PurchaseManagementPage) is
// now a layout with one REAL page per section:
//   /purchase/orders · /purchase/invoices · /purchase/variance ·
//   /purchase/suppliers · /purchase/vendor-returns · /purchase/analytics ·
//   /purchase/this-month
// Legacy /purchase?tab=<x> deep-links (bookmarks, WhatsApp'd links, old
// builds) forward via PurchaseTabRedirect below — no dead links.
import { lazy } from 'react';
import { Route, Navigate, useSearchParams } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';
import type { UserRole } from '../types';
import { APPROVE_ROLES } from '../pages/purchase/invoices/shared';

const PurchaseLayout = lazy(() => import('../pages/purchase/PurchaseLayout').then(m => ({ default: m.PurchaseLayout })));
const PurchaseOrdersSection = lazy(() => import('../pages/purchase/PurchaseOrdersSection').then(m => ({ default: m.PurchaseOrdersSection })));
const PurchaseInvoicesSection = lazy(() => import('../pages/purchase/PurchaseInvoicesSection').then(m => ({ default: m.PurchaseInvoicesSection })));
const PurchaseVarianceTab = lazy(() => import('../pages/purchase/PurchaseVarianceTab').then(m => ({ default: m.PurchaseVarianceTab })));
const SuppliersSection = lazy(() => import('../pages/purchase/SuppliersSection').then(m => ({ default: m.SuppliersSection })));
const PurchaseAnalyticsSection = lazy(() => import('../pages/purchase/PurchaseAnalyticsSection').then(m => ({ default: m.PurchaseAnalyticsSection })));
const PurchasesThisMonthSection = lazy(() => import('../pages/purchase/PurchasesThisMonthSection').then(m => ({ default: m.PurchasesThisMonthSection })));
const GoodsReceiptNote = lazy(() => import('../pages/purchase/GoodsReceiptNote').then(m => ({ default: m.GoodsReceiptNote })));
const GoodsReceiptCockpit = lazy(() => import('../pages/purchase/GoodsReceiptCockpit').then(m => ({ default: m.GoodsReceiptCockpit })));
const VendorReturns = lazy(() => import('../pages/purchase/VendorReturns').then(m => ({ default: m.VendorReturns })));
// Purchase S6: Accountant Reconciliation Console
const ReconConsole = lazy(() => import('../pages/purchase/ReconConsole'));
// Owner ruling 2026-10-07 (R3): the screens outside PurchaseLayout take its
// no-shop gate too (lazy: the shop module stays out of the first download).
const PurchaseShopGate = lazy(() => import('../pages/purchase/purchaseShop').then(m => ({ default: m.PurchaseShopGate })));

// The module gate for the section pages — identical to the old /purchase gate.
const PURCHASE_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'];

// Legacy ?tab= mapper: /purchase and /purchase?tab=<x> land on the section
// page, carrying every other query param (grn_id!) along.
const TAB_TO_PATH: Record<string, string> = {
  'purchase-orders': 'orders',
  'purchase-invoices': 'invoices',
  'variance': 'variance',
  'suppliers': 'suppliers',
  'vendor-returns': 'vendor-returns',
  'analytics': 'analytics',
};

function PurchaseTabRedirect() {
  const [searchParams] = useSearchParams();
  const tab = searchParams.get('tab') || '';
  const section = TAB_TO_PATH[tab] ?? 'orders';
  const rest = new URLSearchParams(searchParams);
  rest.delete('tab');
  const suffix = rest.toString() ? `?${rest.toString()}` : '';
  return <Navigate to={`/purchase/${section}${suffix}`} replace />;
}

// Deep-link landing for "book the invoice for this receipt": the express-
// receive accountant task and the PO timeline drawer both link
// /purchase/invoices/book?grn_id=<id>. Land on the Invoices page, which
// auto-opens the from-GRN draft for that grn_id.
function InvoiceBookRedirect() {
  const grnId = new URLSearchParams(window.location.search).get('grn_id') || '';
  const suffix = grnId ? `?grn_id=${encodeURIComponent(grnId)}` : '';
  return <Navigate to={`/purchase/invoices${suffix}`} replace />;
}

export const purchaseRoutes = (
  <>
    {/* Purchase module — layout + one page per section */}
    <Route
      path="purchase"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'WORKSHOP_STAFF']}>
          <PurchaseLayout />
        </ProtectedRoute>
      }
    >
      {/* Bare /purchase (incl. legacy ?tab= links) → the right section */}
      <Route index element={<PurchaseTabRedirect />} />
      <Route
        path="orders"
        element={
          <ProtectedRoute allowedRoles={PURCHASE_ROLES}>
            <PurchaseOrdersSection />
          </ProtectedRoute>
        }
      />
      {/* Purchase invoices are supplier bills -- what we owe and have paid
          (owner ruling 2026-10-01): the accounts roles only, the same set as
          every /vendors/purchase-invoices/* read (APPROVE_ROLES). */}
      <Route
        path="invoices"
        element={
          <ProtectedRoute allowedRoles={APPROVE_ROLES}>
            <PurchaseInvoicesSection />
          </ProtectedRoute>
        }
      />
      <Route
        path="variance"
        element={
          <ProtectedRoute allowedRoles={PURCHASE_ROLES}>
            <PurchaseVarianceTab />
          </ProtectedRoute>
        }
      />
      <Route
        path="suppliers"
        element={
          <ProtectedRoute allowedRoles={PURCHASE_ROLES}>
            <SuppliersSection />
          </ProtectedRoute>
        }
      />
      {/* Vendor Returns keeps its wider historical gate (WORKSHOP_STAFF logs
          defective pairs). ACCOUNTANT reads it too: the supplier credit and
          the GST debit notes on these returns are the accounts roles' to see. */}
      <Route
        path="vendor-returns"
        element={
          <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'WORKSHOP_STAFF']}>
            <VendorReturns />
          </ProtectedRoute>
        }
      />
      <Route
        path="analytics"
        element={
          <ProtectedRoute allowedRoles={PURCHASE_ROLES}>
            <PurchaseAnalyticsSection />
          </ProtectedRoute>
        }
      />
      {/* Audit F56: Purchases this month -- the supplier-balance readers only,
          the same set as GET /vendors/purchases-this-month. */}
      <Route
        path="this-month"
        element={
          <ProtectedRoute allowedRoles={APPROVE_ROLES}>
            <PurchasesThisMonthSection />
          </ProtectedRoute>
        }
      />
    </Route>

    {/* Deep-link: book the invoice for an accepted receipt. */}
    <Route path="purchase/invoices/book" element={<InvoiceBookRedirect />} />

    {/* Retired alias: the old Vendor Management page redirected into the
        suppliers tab; keep the address working at the new URL. */}
    <Route
      path="purchase/vendors"
      element={<Navigate to="/purchase/suppliers" replace />}
    />

    {/* Phase 4: Goods Receipt Notes */}
    <Route
      path="purchase/grn"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <PurchaseShopGate><GoodsReceiptNote /></PurchaseShopGate>
        </ProtectedRoute>
      }
    />

    {/* Procurement Phase 2: Deliveries inbox + guided express
        receive (mandatory attachment gate). ALL receiving roles —
        mirrors the backend /vendors/grn* gate (owner decision:
        express receive for all receiving staff). */}
    <Route
      path="purchase/receive"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <PurchaseShopGate><GoodsReceiptCockpit /></PurchaseShopGate>
        </ProtectedRoute>
      }
    />

    {/* Purchase S6: Accountant Reconciliation Console
        Gated to ACCOUNTANT / ADMIN / SUPERADMIN only.
        Provides 4-tick recon flags per purchase invoice + 4 worklists. */}
    <Route
      path="purchase/recon-console"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
          <PurchaseShopGate><ReconConsole /></PurchaseShopGate>
        </ProtectedRoute>
      }
    />
  </>
);
