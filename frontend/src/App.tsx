// ============================================================================
// IMS 2.0 - Main Application Entry
// ============================================================================

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Suspense, lazy } from 'react';
import { AuthProvider } from './context/AuthContext';
import { ToastProvider } from './context/ToastContext';
import { ModuleProvider } from './context/ModuleContext';
import { AppearanceProvider } from './context/AppearanceContext';
import { AppLayout } from './components/layout/AppLayout';
import { ProtectedRoute } from './components/layout/ProtectedRoute';
import { ErrorBoundary } from './components/layout/ErrorBoundary';
import { SessionExpiryWarning } from './components/common/SessionExpiryWarning';
// SpeedInsights removed — INP overlay disrupts user experience in production
// import { SpeedInsights } from '@vercel/speed-insights/react';
import { Analytics } from '@vercel/analytics/react';

// Lazy load pages for code splitting
const LoginPage = lazy(() => import('./pages/auth/LoginPage').then(m => ({ default: m.LoginPage })));
const StoreSelectPage = lazy(() => import('./pages/auth/StoreSelectPage').then(m => ({ default: m.StoreSelectPage })));
const VendorPortalPage = lazy(() => import('./pages/vendor-portal/VendorPortalPage'));
const OrderTrackingPage = lazy(() => import('./pages/portal/OrderTrackingPage'));
const RxPortalPage = lazy(() => import('./pages/portal/RxPortalPage'));
const DashboardPage = lazy(() => import('./pages/dashboard/HubPage'));
const NotificationsPage = lazy(() => import('./pages/notifications/NotificationsPage'));
const ExecutiveDashboard = lazy(() => import('./pages/dashboard/ExecutiveDashboard').then(m => ({ default: m.ExecutiveDashboard })));
const EnterpriseAnalyticsDashboard = lazy(() => import('./pages/dashboard/EnterpriseAnalyticsDashboard'));
const POSPage = lazy(() => import('./pages/pos/POSPage').then(m => ({ default: m.POSPage })));
const FootfallPage = lazy(() => import('./pages/pos/FootfallPage').then(m => ({ default: m.FootfallPage })));
const CustomersPage = lazy(() => import('./pages/customers/CustomersPage').then(m => ({ default: m.CustomersPage })));
const Customer360Dashboard = lazy(() => import('./pages/customers/Customer360Dashboard').then(m => ({ default: m.Customer360Dashboard })));
const CustomerSegmentation = lazy(() => import('./pages/customers/CustomerSegmentation').then(m => ({ default: m.CustomerSegmentation })));
const VipChurnWatchlistPage = lazy(() => import('./pages/customers/VipChurnWatchlistPage').then(m => ({ default: m.VipChurnWatchlistPage })));
const NBADashboardPage = lazy(() => import('./pages/customers/NBADashboardPage').then(m => ({ default: m.NBADashboardPage })));
const LapsedReactivationPage = lazy(() => import('./pages/customers/LapsedReactivationPage').then(m => ({ default: m.LapsedReactivationPage })));
const LoyaltyProgram = lazy(() => import('./pages/customers/LoyaltyProgram').then(m => ({ default: m.LoyaltyProgram })));
const LoyaltyLedger = lazy(() => import('./pages/customers/LoyaltyLedger'));
const CampaignManager = lazy(() => import('./pages/customers/CampaignManager').then(m => ({ default: m.CampaignManager })));
const PromotionsPage = lazy(() => import('./pages/promotions/PromotionsPage'));
const PromotionsReportPage = lazy(() => import('./pages/promotions/PromotionsReportPage'));
const ReferralTracker = lazy(() => import('./pages/customers/ReferralTracker').then(m => ({ default: m.ReferralTracker })));
const CustomerFeedback = lazy(() => import('./pages/customers/CustomerFeedback').then(m => ({ default: m.CustomerFeedback })));
const FollowUpDashboard = lazy(() => import('./pages/customers/FollowUpDashboard').then(m => ({ default: m.FollowUpDashboard })))
const FamilyWalletPage = lazy(() => import('./pages/customers/FamilyWalletPage').then(m => ({ default: m.FamilyWalletPage })));
const CLRefillWorklistPage = lazy(() => import('./pages/customers/CLRefillWorklistPage').then(m => ({ default: m.CLRefillWorklistPage })));
const WhatsAppInboxPage = lazy(() => import('./pages/customers/WhatsAppInboxPage').then(m => ({ default: m.WhatsAppInboxPage })));
const InventoryPage = lazy(() => import('./pages/inventory/InventoryPage').then(m => ({ default: m.InventoryPage })));
const PowerGridPage = lazy(() => import('./pages/inventory/PowerGridPage'));
const OnlineStockPage = lazy(() => import('./pages/inventory/OnlineStockPage'));
const OnlineStorePage = lazy(() => import('./pages/online-store/OnlineStorePage'));
const OnlineProductsPage = lazy(() => import('./pages/online-store/OnlineProductsPage'));
const OnlineCustomersPage = lazy(() => import('./pages/online-store/OnlineCustomersPage'));
const CollectionsPage = lazy(() => import('./pages/online-store/CollectionsPage'));
const CollectionBrowsePage = lazy(() => import('./pages/online-store/CollectionBrowsePage'));
const MenusPage = lazy(() => import('./pages/online-store/MenusPage'));
const DesignQueuePage = lazy(() => import('./pages/online-store/DesignQueuePage'));
const OnlineOrdersPage = lazy(() => import('./pages/online-store/OnlineOrdersPage'));
const OnlineStockTallyPage = lazy(() => import('./pages/online-store/OnlineStockPage'));
const OndcSellerPage = lazy(() => import('./pages/online-store/OndcSellerPage'));
const OrdersPage = lazy(() => import('./pages/orders/OrdersPage').then(m => ({ default: m.OrdersPage })));
const ClinicalPage = lazy(() => import('./pages/clinical/ClinicalPage').then(m => ({ default: m.ClinicalPage })));
const NewEyeTestPage = lazy(() => import('./pages/clinical/NewEyeTestPage').then(m => ({ default: m.NewEyeTestPage })));
const TestHistoryPage = lazy(() => import('./pages/clinical/TestHistoryPage').then(m => ({ default: m.TestHistoryPage })));
const PrescriptionsPage = lazy(() => import('./pages/clinical/PrescriptionsPage').then(m => ({ default: m.PrescriptionsPage })));
const FamilyRxPage = lazy(() => import('./pages/clinical/FamilyRxPage').then(m => ({ default: m.FamilyRxPage })));
const ContactLensFittingPage = lazy(() => import('./pages/clinical/ContactLensFittingPage').then(m => ({ default: m.ContactLensFittingPage })));
const WorkshopPage = lazy(() => import('./pages/workshop/WorkshopPage').then(m => ({ default: m.WorkshopPage })));
const StationScanPage = lazy(() => import('./pages/workshop/StationScanPage').then(m => ({ default: m.StationScanPage })));
const PurchaseManagementPage = lazy(() => import('./pages/purchase/PurchaseManagementPage').then(m => ({ default: m.PurchaseManagementPage })));
const TaskManagementPage = lazy(() => import('./pages/tasks/TaskManagementPage').then(m => ({ default: m.TaskManagementPage })));
const TasksDashboard = lazy(() => import('./pages/tasks/TasksDashboard').then(m => ({ default: m.TasksDashboard })));
const HRPage = lazy(() => import('./pages/hr/HRPage').then(m => ({ default: m.HRPage })));
const EmployeeSelfServicePage = lazy(() => import('./pages/hr/EmployeeSelfService').then(m => ({ default: m.EmployeeSelfService })));
const AttendancePage = lazy(() => import('./pages/attendance/AttendancePage').then(m => ({ default: m.AttendancePage })));
const PayrollDashboard = lazy(() => import('./pages/hr/PayrollDashboard').then(m => ({ default: m.PayrollDashboard })));
const SalarySetupPage = lazy(() => import('./pages/hr/SalarySetupPage').then(m => ({ default: m.SalarySetupPage })));
const PayrollRunPage = lazy(() => import('./pages/hr/PayrollRunPage').then(m => ({ default: m.PayrollRunPage })));
// EntitiesPage retired from routing — /settings/entities now redirects to the
// canonical /organization screen (COUNCIL RULING §3). The page file is kept for
// a later-release deletion.
const OrganizationPage = lazy(() => import('./pages/settings/OrganizationPage'));
const ReportsPage = lazy(() => import('./pages/reports/ReportsPage').then(m => ({ default: m.ReportsPage })));
const GrowthBlueprintPage = lazy(() => import('./pages/reports/GrowthBlueprintPage').then(m => ({ default: m.GrowthBlueprintPage })));
const SettingsPage = lazy(() => import('./pages/settings/SettingsPage').then(m => ({ default: m.SettingsPage })));
const ApprovalInboxPage = lazy(() => import('./pages/approvals/ApprovalInboxPage').then(m => ({ default: m.ApprovalInboxPage })));
const MyRequestsPage = lazy(() => import('./pages/approvals/MyRequestsPage').then(m => ({ default: m.MyRequestsPage })));
const PendingApprovalsPage = lazy(() => import('./pages/returns/PendingApprovalsPage').then(m => ({ default: m.PendingApprovalsPage })));
const DayEndReport = lazy(() => import('./pages/reports/DayEndReport'));
const OutstandingPaymentsReport = lazy(() => import('./pages/reports/OutstandingPaymentsReport'));
const PrintPage = lazy(() => import('./pages/print/PrintPage'));
const ReturnsPage = lazy(() => import('./pages/orders/ReturnsPage'));
const EstimatesPage = lazy(() => import('./pages/orders/EstimatesPage').then(m => ({ default: m.EstimatesPage })));
const SetupPage = lazy(() => import('./pages/settings/SetupPage'));
const GoLiveChecklistPage = lazy(() => import('./pages/settings/GoLiveChecklistPage').then(m => ({ default: m.GoLiveChecklistPage })));

// Phase 4: Supply Chain & Procurement
// NOTE: PurchaseOrderDashboard + VendorManagement were dead duplicates (read-only
// stubs with no working actions). Retired — /purchase/orders and /purchase/vendors
// now redirect to the real PurchaseManagementPage tabs below.
const GoodsReceiptNote = lazy(() => import('./pages/purchase/GoodsReceiptNote').then(m => ({ default: m.GoodsReceiptNote })));
const GoodsReceiptCockpit = lazy(() => import('./pages/purchase/GoodsReceiptCockpit').then(m => ({ default: m.GoodsReceiptCockpit })));
const VendorReturns = lazy(() => import('./pages/purchase/VendorReturns').then(m => ({ default: m.VendorReturns })));
const VendorRMA = lazy(() => import('./pages/purchase/VendorRMA').then(m => ({ default: m.VendorRMA })));
// Purchase S6: Accountant Reconciliation Console
const ReconConsole = lazy(() => import('./pages/purchase/ReconConsole'));
const StockReplenishment = lazy(() => import('./pages/inventory/StockReplenishment').then(m => ({ default: m.StockReplenishment })));
const StockAudit = lazy(() => import('./pages/inventory/StockAudit').then(m => ({ default: m.StockAudit })));
const OpeningStockImport = lazy(() => import('./pages/inventory/OpeningStockImport').then(m => ({ default: m.OpeningStockImport })));
const JarvisPage = lazy(() => import('./pages/jarvis/JarvisPage').then(m => ({ default: m.JarvisPage })));
const ActivityLogPage = lazy(() => import('./pages/admin/ActivityLogPage'));
// /catalog/add — the single product-add door (Quick Add). Guided + Bulk modes
// were removed; Quick Add absorbed every field/section Guided had.
const QuickAddPage = lazy(() => import('./pages/catalog/QuickAddPage'));
const CatalogAutopilotPage = lazy(() => import('./pages/catalog/CatalogAutopilotPage'));
const BuyDeskPage = lazy(() => import('./pages/catalog/BuyDeskPage'));
const PricingOffersPage = lazy(() => import('./pages/pricing/PricingOffersPage'));
const ExpenseTracker = lazy(() => import('./pages/finance/ExpenseTracker'));
const FinanceDashboard = lazy(() => import('./pages/finance/FinanceDashboard'));
const CashFlowPage = lazy(() => import('./pages/finance/CashFlowPage'));
const ItcReconcilePage = lazy(() => import('./pages/finance/ItcReconcilePage'));
const CashRegisterPage = lazy(() => import('./pages/finance/CashRegisterPage'));
const BlindEodTallyPage = lazy(() => import('./pages/finance/BlindEodTallyPage'));
const CashReconciliationPage = lazy(() => import('./pages/finance/CashReconciliationPage'));
const BudgetingPage = lazy(() => import('./pages/finance/BudgetingPage'));
const B2BTallyExport = lazy(() => import('./pages/finance/B2BTallyExport'));
const B2BTallyWorklist = lazy(() => import('./pages/finance/B2BTallyWorklist'));
const WalkoutsPage = lazy(() => import('./pages/walkouts/WalkoutsPage').then(m => ({ default: m.WalkoutsPage })));
const WalkoutDetailPage = lazy(() => import('./pages/walkouts/WalkoutDetailPage').then(m => ({ default: m.WalkoutDetailPage })));
const WalkoutsDashboardPage = lazy(() => import('./pages/walkouts/WalkoutsDashboardPage').then(m => ({ default: m.WalkoutsDashboardPage })));
const DailyScorecardPage = lazy(() => import('./pages/incentive/DailyScorecardPage').then(m => ({ default: m.DailyScorecardPage })));
const MTDLeaderboardPage = lazy(() => import('./pages/incentive/MTDLeaderboardPage').then(m => ({ default: m.MTDLeaderboardPage })));
const PointsHistoryPage = lazy(() => import('./pages/incentive/PointsHistoryPage').then(m => ({ default: m.PointsHistoryPage })));
const PayoutDashboardPage = lazy(() => import('./pages/incentive/PayoutDashboardPage').then(m => ({ default: m.PayoutDashboardPage })));
const PayoutSnapshotsPage = lazy(() => import('./pages/incentive/PayoutSnapshotsPage').then(m => ({ default: m.PayoutSnapshotsPage })));
const IncentiveSettingsPage = lazy(() => import('./pages/incentive/IncentiveSettingsPage').then(m => ({ default: m.IncentiveSettingsPage })));
// CRM-16: Ad Performance (agency oversight dashboard)
const AdPerformancePage = lazy(() => import('./pages/marketing/AdPerformancePage').then(m => ({ default: m.AdPerformancePage })));

// Loading fallback component
const PageLoader = () => (
  <div className="flex items-center justify-center h-64">
    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
  </div>
);

// Create React Query client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      retry: 1,
    },
  },
});

// Unauthorized page
const UnauthorizedPage = () => (
  <div className="min-h-screen flex items-center justify-center bg-gray-50">
    <div className="text-center">
      <h1 className="text-4xl font-bold text-gray-900 mb-2">403</h1>
      <p className="text-gray-500 mb-4">You don't have permission to access this page.</p>
      <a href="/dashboard" className="btn-primary">
        Go to Dashboard
      </a>
    </div>
  </div>
);

// Not Found page
const NotFoundPage = () => (
  <div className="min-h-screen flex items-center justify-center bg-gray-50">
    <div className="text-center">
      <h1 className="text-4xl font-bold text-gray-900 mb-2">404</h1>
      <p className="text-gray-500 mb-4">Page not found.</p>
      <a href="/dashboard" className="btn-primary">
        Go to Dashboard
      </a>
    </div>
  </div>
);

function App() {
  return (
    <ErrorBoundary>
      <AppearanceProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <ModuleProvider>
            <ToastProvider>
              <BrowserRouter>
                <SessionExpiryWarning />
                <Suspense fallback={<PageLoader />}>
                <Routes>
                  {/* Public routes */}
                  <Route path="/login" element={<LoginPage />} />
                  <Route path="/unauthorized" element={<UnauthorizedPage />} />
                  {/* Vendor Portal — public, token-auth via URL.
                      Mounted OUTSIDE ProtectedRoute because external lens
                      labs hit this without an IMS user account. The
                      tokenId in the URL IS the auth (server-side check). */}
                  <Route path="/vendor-portal/:tokenId" element={<VendorPortalPage />} />
                  {/* Customer self-service — public, no AppLayout/auth.
                      Order tracking is a tokenized link; Rx viewing is
                      OTP-gated (medical data). See pages/portal/. */}
                  <Route path="/track/:token" element={<OrderTrackingPage />} />
                  <Route path="/rx-portal" element={<RxPortalPage />} />

                {/* Post-login store selector — auth-gated but rendered FULL-SCREEN
                    (no AppLayout shell). Multi-store roles land here after login to
                    pick the active store; single-store users auto-proceed. Kept
                    OUTSIDE the AppLayout route so its guard can redirect here
                    without a loop. */}
                <Route
                  path="/select-store"
                  element={
                    <ProtectedRoute>
                      <StoreSelectPage />
                    </ProtectedRoute>
                  }
                />

                {/* Protected routes with layout */}
                <Route
                  path="/"
                  element={
                    <ProtectedRoute>
                      <AppLayout />
                    </ProtectedRoute>
                  }
                >
                  {/* Redirect root to dashboard */}
                  <Route index element={<Navigate to="/dashboard" replace />} />

                  {/* Dashboard */}
                  <Route path="dashboard" element={<DashboardPage />} />

                  {/* Notifications (any authenticated user) */}
                  <Route path="notifications" element={<ProtectedRoute><NotificationsPage /></ProtectedRoute>} />

                  {/* E4 Approvals — inbox (approvers) + my requests (any maker).
                      Route gates mirror the backend rbac_policy: inbox/approve
                      is the approver set; my-requests is any authenticated user. */}
                  <Route
                    path="approvals"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
                        <ApprovalInboxPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="approvals/mine"
                    element={<ProtectedRoute><MyRequestsPage /></ProtectedRoute>}
                  />
                  {/* F27 refund approvals queue — the refund-only slice of the
                      E4 inbox. Gate mirrors the backend approvals inbox roles
                      (ACCOUNTANT is read-only; approve/reject is gated again
                      server-side to the approver set). */}
                  <Route
                    path="returns/approvals"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
                        <PendingApprovalsPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="dashboard/executive"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']}>
                        <ExecutiveDashboard />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="dashboard/analytics"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']}>
                        <EnterpriseAnalyticsDashboard />
                      </ProtectedRoute>
                    }
                  />

                  {/* POS */}
                  <Route
                    path="pos"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF']}
                      >
                        <POSPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* POS: Footfall Tracking (N3 — manual walk-in capture + conversion %) */}
                  <Route
                    path="pos/footfall"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF', 'OPTOMETRIST']}
                      >
                        <FootfallPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Customers */}
                  <Route
                    path="customers"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF']}
                      >
                        <CustomersPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM: Customer 360 - search/picker */}
                  <Route
                    path="customers/360"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF']}
                      >
                        <Customer360Dashboard />
                      </ProtectedRoute>
                    }
                  />
                  {/* CRM: Customer 360 - with customer */}
                  <Route
                    path="customers/:customerId/360"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF']}
                      >
                        <Customer360Dashboard />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM: Customer Segmentation */}
                  <Route
                    path="customers/segmentation"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']}
                      >
                        <CustomerSegmentation />
                      </ProtectedRoute>
                    }
                  />

                  {/* F40: VIP Churn Watchlist — overdue high-LTV customers.
                      Read-only retention oversight: SUPERADMIN / ADMIN only. */}
                  <Route
                    path="customers/vip-churn-watchlist"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
                        <VipChurnWatchlistPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* F39: NBA daily call list — ranked customers to phone today.
                      Store-facing call work-list; in-app only (no message send). */}
                  <Route
                    path="customers/nba"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF']}
                      >
                        <NBADashboardPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* F41: Lapsed-patient reactivation — in-app work-list of
                      clinically lapsed patients to bring back. Store-facing;
                      in-app only (no message send, no voucher mint). */}
                  <Route
                    path="customers/reactivation"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF']}
                      >
                        <LapsedReactivationPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* F49: Family/household loyalty wallet — shared points pool
                      (max 7 members, any member redeems chain-wide). Manage =
                      manager+; redeem mints a store-credit voucher. */}
                  <Route
                    path="customers/family-wallet"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF', 'CASHIER']}
                      >
                        <FamilyWalletPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM-2: Contact-lens refill-due work-list — in-app follow-up
                      for customers running out of lenses. Create-reminders =
                      manager+; in-app only (no message send). */}
                  <Route
                    path="customers/cl-refill"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF', 'OPTOMETRIST']}
                      >
                        <CLRefillWorklistPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM: Loyalty Program */}
                  <Route
                    path="customers/loyalty"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']}
                      >
                        <LoyaltyProgram />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM: Per-customer Loyalty Ledger (audit trail) */}
                  <Route
                    path="customers/:customerId/loyalty"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF']}
                      >
                        <LoyaltyLedger />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM: Campaign Manager */}
                  <Route
                    path="customers/campaigns"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']}
                      >
                        <CampaignManager />
                      </ProtectedRoute>
                    }
                  />

                  {/* F11/F12: Promotions admin (rules) + Offer Tally report.
                      Live POS apply is dark behind PROMO_ENGINE_ENABLED. */}
                  <Route
                    path="promotions"
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
                        <PromotionsPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="reports/promotions"
                    element={
                      <ProtectedRoute
                        allowedRoles={[
                          'SUPERADMIN',
                          'ADMIN',
                          'AREA_MANAGER',
                          'STORE_MANAGER',
                          'ACCOUNTANT',
                        ]}
                      >
                        <PromotionsReportPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM-14: WhatsApp Inbox — inbound messages from Meta Business API */}
                  <Route
                    path="customers/whatsapp-inbox"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']}
                      >
                        <WhatsAppInboxPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM-16: Ad Performance — agency oversight dashboard (Google + Meta).
                      Finance-sensitive spend data: restricted to SUPERADMIN / ADMIN. */}
                  <Route
                    path="marketing/ad-performance"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN']}
                      >
                        <AdPerformancePage />
                      </ProtectedRoute>
                    }
                  />

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

                  {/* Online Store — Products / PIM (BVI Phase 1). Read-only list
                      of the online catalog + per-SKU online status. Same
                      catalog/design role gate as the module shell. */}
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

                  {/* CRM: Referral Tracker */}
                  <Route
                    path="customers/referrals"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']}
                      >
                        <ReferralTracker />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM: Customer Feedback & NPS */}
                  <Route
                    path="customers/feedback"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']}
                      >
                        <CustomerFeedback />
                      </ProtectedRoute>
                    }
                  />

                  {/* CRM: Follow-up Management */}
                  <Route
                    path="customers/follow-ups"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'SALES_STAFF', 'CASHIER']}
                      >
                        <FollowUpDashboard />
                      </ProtectedRoute>
                    }
                  />

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

                  {/* Orders */}
                  <Route
                    path="orders"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF', 'OPTOMETRIST', 'WORKSHOP_STAFF']}
                      >
                        <OrdersPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Estimates / Quotations (non-binding priced quotes) */}
                  <Route
                    path="estimates"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF']}
                      >
                        <EstimatesPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Walkouts (Pune Incentive Module i) */}
                  <Route
                    path="walkouts"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'CASHIER']}
                      >
                        <WalkoutsPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="walkouts/dashboard"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <WalkoutsDashboardPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="walkouts/:walkoutId"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'CASHIER']}
                      >
                        <WalkoutDetailPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Pune Incentive Module ii — Daily Points */}
                  <Route
                    path="incentive"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'CASHIER']}
                      >
                        <DailyScorecardPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="incentive/leaderboard"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <MTDLeaderboardPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="incentive/staff/:staffId"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <PointsHistoryPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="incentive/payout"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <PayoutDashboardPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="incentive/payouts"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <PayoutSnapshotsPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="incentive/settings"
                    element={
                      // SUPERADMIN-only to match the backend: the
                      // /incentive/points/settings/* PATCH endpoints are
                      // SUPERADMIN-only by design (points.py), so other roles
                      // previously saw a fully editable page where every Save
                      // 403'd. (Widen the backend if ADMIN should manage these.)
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN']}
                      >
                        <IncentiveSettingsPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Returns & Exchanges */}
                  <Route
                    path="returns"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF']}
                      >
                        <ReturnsPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Clinical / Eye Tests */}
                  <Route
                    path="clinical"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
                      >
                        <ClinicalPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="clinical/test"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
                      >
                        <NewEyeTestPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="clinical/history"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
                      >
                        <TestHistoryPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="prescriptions"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
                      >
                        <PrescriptionsPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="clinical/family-rx"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
                      >
                        <FamilyRxPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="clinical/contact-lens"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
                      >
                        <ContactLensFittingPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Workshop */}
                  <Route
                    path="workshop"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF']}
                      >
                        <WorkshopPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* F2 -- fullscreen lab-bench station scan terminal. CASHIER is
                      included for the front-desk PICKUP scan (mirrors the backend
                      _LAB_SCAN_ROLES gate). */}
                  <Route
                    path="workshop/station/:stationCode"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'WORKSHOP_STAFF', 'CASHIER']}
                      >
                        <StationScanPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Purchase Management */}
                  <Route
                    path="purchase"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
                        <PurchaseManagementPage />
                      </ProtectedRoute>
                    }
                  />

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

                  {/* Purchase P1/S4: Goods-Receipt Cockpit — vendor-first receiving
                      with mandatory attachment gate (SUPERADMIN/ADMIN/STORE_MANAGER) */}
                  <Route
                    path="purchase/receive"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']}>
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

                  {/* Tasks & SOPs */}
                  <Route
                    path="tasks"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
                        <TaskManagementPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="tasks/dashboard"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF']}>
                        <TasksDashboard />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="tasks/checklists"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF']}>
                        <TasksDashboard />
                      </ProtectedRoute>
                    }
                  />

                  {/* My Work — mobile-first employee self-service. Open to EVERY
                      operational role (incl. floor staff). Reads only the
                      caller's OWN data via /hr/me/* (own attendance / payslip /
                      commission / leave balance). Path is intentionally NOT
                      under /hr (which is module-gated to managers) so it is
                      ungated at the module level; ProtectedRoute lists the staff
                      roles explicitly. */}
                  <Route
                    path="my-work"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF', 'WORKSHOP_STAFF']}
                      >
                        <EmployeeSelfServicePage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Attendance — its own top-level page (was an HR tab).
                      Open to all operational roles: managers get the monthly
                      grid + admin edit, floor staff get the self check-in card.
                      The grid + edit are further role-gated inside the page. */}
                  <Route
                    path="attendance"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF', 'WORKSHOP_STAFF']}
                      >
                        <AttendancePage />
                      </ProtectedRoute>
                    }
                  />

                  {/* HR */}
                  <Route
                    path="hr"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <HRPage />
                      </ProtectedRoute>
                    }
                  />

                  <Route
                    path="hr/payroll"
                    element={(
                      <ProtectedRoute allowedRoles={["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"]}>
                        <PayrollDashboard />
                      </ProtectedRoute>
                    )}
                  />
                  <Route
                    path="hr/salary-setup"
                    element={(
                      <ProtectedRoute allowedRoles={["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"]}>
                        <SalarySetupPage />
                      </ProtectedRoute>
                    )}
                  />
                  <Route
                    path="hr/payroll-run"
                    element={(
                      <ProtectedRoute allowedRoles={["SUPERADMIN", "ADMIN", "ACCOUNTANT"]}>
                        <PayrollRunPage />
                      </ProtectedRoute>
                    )}
                  />

                  {/* Reports */}
                  <Route
                    path="reports"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <ReportsPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* R3 — Growth Blueprint (SUPERADMIN-only — uses LLM tokens) */}
                  <Route
                    path="reports/blueprint"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN']}>
                        <GrowthBlueprintPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Day-End Closing Report */}
                  <Route
                    path="reports/day-end"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF']}
                      >
                        <DayEndReport />
                      </ProtectedRoute>
                    }
                  />

                  {/* Outstanding Payments Report */}
                  <Route
                    path="reports/outstanding"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
                      >
                        <OutstandingPaymentsReport />
                      </ProtectedRoute>
                    }
                  />

                  {/* Print templates index — directory of all printable docs */}
                  <Route
                    path="print"
                    element={
                      <ProtectedRoute
                        allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_STAFF']}
                      >
                        <PrintPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Settings */}
                  <Route
                    path="settings"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'ACCOUNTANT']}>
                        <SettingsPage />
                      </ProtectedRoute>
                    }
                  />
                  {/* COUNCIL RULING §3: Entities are managed on the canonical
                      /organization screen. Redirect the orphaned SPA route
                      instead of shipping a parallel editor (delete the page a
                      release later). */}
                  <Route
                    path="settings/entities"
                    element={<Navigate to="/organization" replace />}
                  />
                  <Route
                    path="organization"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
                        <OrganizationPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Store Setup & Employee Onboarding */}
                  <Route
                    path="setup"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
                        <SetupPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* Go-Live Readiness Checklist */}
                  <Route
                    path="go-live"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN']}>
                        <GoLiveChecklistPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* AI Intelligence — Superadmin only */}
                  <Route
                    path="jarvis"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN']}>
                        <JarvisPage />
                      </ProtectedRoute>
                    }
                  />

                  {/* User Activity Log (audit trail) — Superadmin only */}
                  <Route
                    path="admin/activity-log"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN']}>
                        <ActivityLogPage />
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

                  {/* Catalog Autopilot — brand+model search -> approve -> publish */}
                  <Route
                    path="catalog/autopilot"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']}>
                        <CatalogAutopilotPage />
                      </ProtectedRoute>
                    }
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

                  {/* Expenses — any authenticated user can submit + see their own;
                      ownership scoping + role-gated approval/entry happen inside. */}
                  <Route
                    path="finance/expenses"
                    element={
                      <ProtectedRoute>
                        <ExpenseTracker />
                      </ProtectedRoute>
                    }
                  />

                  {/* Bare /finance → /finance/dashboard. QA 2026-05-27 reported a 404
                      on /finance because no route was defined. Same for the sidebar's
                      old /cash-flow path. Hard 404s are user-hostile when the
                      intent is clearly the canonical module landing screen. */}
                  <Route
                    path="finance"
                    element={<Navigate to="/finance/dashboard" replace />}
                  />
                  <Route
                    path="cash-flow"
                    element={<Navigate to="/finance/cash-flow" replace />}
                  />

                  {/* Finance Dashboard */}
                  <Route
                    path="finance/dashboard"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
                        <FinanceDashboard />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="finance/cash-flow"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
                        <CashFlowPage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="finance/itc"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
                        <ItcReconcilePage />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="finance/cash-register"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
                        <CashRegisterPage />
                      </ProtectedRoute>
                    }
                  />
                  {/* F23 Blind EOD cash tally & Z-Read -- cashiers reach it to
                      open + blind-submit; managers reveal variance + lock. */}
                  <Route
                    path="finance/blind-eod"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_STAFF']}>
                        <BlindEodTallyPage />
                      </ProtectedRoute>
                    }
                  />
                  {/* #7 Manager-facing cash-register vs blind-EOD reconciliation
                      console -- READ-ONLY view across both day-close flows so an
                      owner / store-manager can spot a cash disparity. Store
                      Manager sees own store; HQ roles see all (store-scoped on the
                      backend via resolve_store_scope). */}
                  <Route
                    path="finance/cash-reconciliation"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
                        <CashReconciliationPage />
                      </ProtectedRoute>
                    }
                  />
                  {/* Dual-mode (planned vs actual) budgeting */}
                  <Route
                    path="finance/budgeting"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
                        <BudgetingPage />
                      </ProtectedRoute>
                    }
                  />
                  {/* B2B invoices -> Tally: e-invoice + e-way bill issued in Tally.
                      Export console + reminder worklist. Finance-admin only. */}
                  <Route
                    path="finance/b2b-tally-export"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
                        <B2BTallyExport />
                      </ProtectedRoute>
                    }
                  />
                  <Route
                    path="finance/b2b-tally-worklist"
                    element={
                      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
                        <B2BTallyWorklist />
                      </ProtectedRoute>
                    }
                  />
                </Route>

                  {/* 404 */}
                  <Route path="*" element={<NotFoundPage />} />
                </Routes>
              </Suspense>
            </BrowserRouter>
              <Analytics />
          </ToastProvider>
        </ModuleProvider>
      </AuthProvider>
    </QueryClientProvider>
    </AppearanceProvider>
    </ErrorBoundary>
  );
}

export default App;
