// Purchase & procurement routes. Moved verbatim from App.tsx (route-registry
// split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route, Navigate } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const PurchaseManagementPage = lazy(() => import('../pages/purchase/PurchaseManagementPage').then(m => ({ default: m.PurchaseManagementPage })));
// Phase 4: Supply Chain & Procurement
// NOTE: PurchaseOrderDashboard + VendorManagement were dead duplicates (read-only
// stubs with no working actions). Retired — /purchase/orders and /purchase/vendors
// now redirect to the real PurchaseManagementPage tabs below.
const GoodsReceiptNote = lazy(() => import('../pages/purchase/GoodsReceiptNote').then(m => ({ default: m.GoodsReceiptNote })));
const GoodsReceiptCockpit = lazy(() => import('../pages/purchase/GoodsReceiptCockpit').then(m => ({ default: m.GoodsReceiptCockpit })));
const VendorReturns = lazy(() => import('../pages/purchase/VendorReturns').then(m => ({ default: m.VendorReturns })));
const VendorRMA = lazy(() => import('../pages/purchase/VendorRMA').then(m => ({ default: m.VendorRMA })));
// Purchase S6: Accountant Reconciliation Console
const ReconConsole = lazy(() => import('../pages/purchase/ReconConsole'));

// Deep-link landing for "book the invoice for this receipt": the express-
// receive accountant task and the PO timeline drawer both link
// /purchase/invoices/book?grn_id=<id>. Redirect into the Purchase page's
// Invoices tab, which auto-opens the from-GRN draft for that grn_id.
function InvoiceBookRedirect() {
  const grnId = new URLSearchParams(window.location.search).get('grn_id') || '';
  const suffix = grnId ? `&grn_id=${encodeURIComponent(grnId)}` : '';
  return <Navigate to={`/purchase?tab=purchase-invoices${suffix}`} replace />;
}

export const purchaseRoutes = (
  <>
    {/* Purchase Management */}
    <Route
      path="purchase"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <PurchaseManagementPage />
        </ProtectedRoute>
      }
    />

    {/* Deep-link: book the invoice for an accepted receipt. */}
    <Route path="purchase/invoices/book" element={<InvoiceBookRedirect />} />

    {/* Phase 4: Purchase Orders — retired dead-duplicate dashboard.
        Redirect to the real Purchase Management module (POs tab). */}
    <Route
      path="purchase/orders"
      element={<Navigate to="/purchase?tab=purchase-orders" replace />}
    />

    {/* Phase 4: Vendor Management — retired dead-duplicate page.
        Redirect to the real Purchase Management module (Suppliers tab). */}
    <Route
      path="purchase/vendors"
      element={<Navigate to="/purchase?tab=suppliers" replace />}
    />

    {/* Phase 4: Goods Receipt Notes */}
    <Route
      path="purchase/grn"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <GoodsReceiptNote />
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
          <GoodsReceiptCockpit />
        </ProtectedRoute>
      }
    />

    {/* Vendor Returns (was orphaned — page existed, never routed) */}
    <Route
      path="purchase/vendor-returns"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'WORKSHOP_STAFF']}>
          <VendorReturns />
        </ProtectedRoute>
      }
    />

    {/* N4: Vendor RMA + credit-note reconciliation (vendor/AP roles) */}
    <Route
      path="purchase/vendor-rma"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <VendorRMA />
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
          <ReconConsole />
        </ProtectedRoute>
      }
    />
  </>
);
