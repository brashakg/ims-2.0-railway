// Customers / CRM + promotions + marketing routes. Moved verbatim from App.tsx
// (route-registry split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const CustomersPage = lazy(() => import('../pages/customers/CustomersPage').then(m => ({ default: m.CustomersPage })));
const Customer360Dashboard = lazy(() => import('../pages/customers/Customer360Dashboard').then(m => ({ default: m.Customer360Dashboard })));
const CustomerSegmentation = lazy(() => import('../pages/customers/CustomerSegmentation').then(m => ({ default: m.CustomerSegmentation })));
const VipChurnWatchlistPage = lazy(() => import('../pages/customers/VipChurnWatchlistPage').then(m => ({ default: m.VipChurnWatchlistPage })));
const NBADashboardPage = lazy(() => import('../pages/customers/NBADashboardPage').then(m => ({ default: m.NBADashboardPage })));
const LapsedReactivationPage = lazy(() => import('../pages/customers/LapsedReactivationPage').then(m => ({ default: m.LapsedReactivationPage })));
const LoyaltyProgram = lazy(() => import('../pages/customers/LoyaltyProgram').then(m => ({ default: m.LoyaltyProgram })));
const LoyaltyLedger = lazy(() => import('../pages/customers/LoyaltyLedger'));
const CampaignManager = lazy(() => import('../pages/customers/CampaignManager').then(m => ({ default: m.CampaignManager })));
const PromotionsPage = lazy(() => import('../pages/promotions/PromotionsPage'));
const PromotionsReportPage = lazy(() => import('../pages/promotions/PromotionsReportPage'));
const ReferralTracker = lazy(() => import('../pages/customers/ReferralTracker').then(m => ({ default: m.ReferralTracker })));
const CustomerFeedback = lazy(() => import('../pages/customers/CustomerFeedback').then(m => ({ default: m.CustomerFeedback })));
const FollowUpDashboard = lazy(() => import('../pages/customers/FollowUpDashboard').then(m => ({ default: m.FollowUpDashboard })))
const FamilyWalletPage = lazy(() => import('../pages/customers/FamilyWalletPage').then(m => ({ default: m.FamilyWalletPage })));
const CLRefillWorklistPage = lazy(() => import('../pages/customers/CLRefillWorklistPage').then(m => ({ default: m.CLRefillWorklistPage })));
const WhatsAppInboxPage = lazy(() => import('../pages/customers/WhatsAppInboxPage').then(m => ({ default: m.WhatsAppInboxPage })));
// CRM-16: Ad Performance (agency oversight dashboard)
const AdPerformancePage = lazy(() => import('../pages/marketing/AdPerformancePage').then(m => ({ default: m.AdPerformancePage })));

export const customerRoutes = (
  <>
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

    {/* CRM: Customer profile — the bookmarkable/shareable address for ONE
        customer. Renders the same Customer360Dashboard as the /360 form (it
        already keys entirely off :customerId), so /customers/:customerId/360
        keeps working for every existing bookmark and in-app link. Role gate is
        the /360 gate, character-for-character.
        Static children (/customers/segmentation, /loyalty, /nba, ...) still win
        over this: React Router ranks a literal segment above a dynamic one. */}
    <Route
      path="customers/:customerId"
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

    {/* F41: Lapsed-patient reactivation — salvaged + WIRED 2026-08-31
        (page was finished but no menu ever linked it). */}
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
  </>
);
