// Settings + admin + Jarvis routes.
//
// Wave 1 split: the old /settings tab container (SettingsPage) is now a
// layout with one REAL page per section — /settings/profile,
// /settings/hsn-rates, /settings/users, … Legacy /settings?tab=<x> links
// forward via SettingsTabRedirect. Per-section role gates come from the same
// SETTINGS_SECTIONS table that drives the nav, so visibility and access can
// never drift apart.
import { lazy } from 'react';
import type React from 'react';
import { Route, Navigate, useSearchParams } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';
import { useAuth } from '../context/AuthContext';
import type { UserRole } from '../types';
import { SETTINGS_SECTIONS } from '../pages/settings/settingsSections';
import type { SettingsTab } from '../pages/settings/settingsTypes';

const SettingsLayout = lazy(() => import('../pages/settings/SettingsLayout').then(m => ({ default: m.SettingsLayout })));

// Section pages — each its own chunk, downloaded only when opened.
const ProfileSection = lazy(() => import('../pages/settings/SettingsProfile').then(m => ({ default: m.ProfileSection })));
const BusinessSection = lazy(() => import('../pages/settings/SettingsProfile').then(m => ({ default: m.BusinessSection })));
const UserManagementSection = lazy(() => import('../pages/settings/SettingsAuth').then(m => ({ default: m.UserManagementSection })));
const CategorySection = lazy(() => import('../pages/settings/SettingsStore').then(m => ({ default: m.CategorySection })));
const BrandSection = lazy(() => import('../pages/settings/SettingsStore').then(m => ({ default: m.BrandSection })));
const DiscountSection = lazy(() => import('../pages/settings/SettingsStore').then(m => ({ default: m.DiscountSection })));
const LensMasterSection = lazy(() => import('../pages/settings/SettingsLens').then(m => ({ default: m.LensMasterSection })));
const LensCatalogEnumsSection = lazy(() => import('../pages/settings/SettingsLensEnums').then(m => ({ default: m.LensCatalogEnumsSection })));
const CatalogDictionarySection = lazy(() => import('../pages/settings/SettingsCatalogDictionary').then(m => ({ default: m.CatalogDictionarySection })));
const LensRangePricingSection = lazy(() => import('../components/settings/LensRangePricing').then(m => ({ default: m.LensRangePricingSection })));
const LoyaltySettingsSection = lazy(() => import('../components/settings/LoyaltySettings').then(m => ({ default: m.LoyaltySettingsSection })));
const TaxInvoiceSettingsPage = lazy(() => import('../pages/settings/SettingsTaxInvoice').then(m => ({ default: m.TaxInvoiceSettingsPage })));
const HsnRatesSection = lazy(() => import('../components/settings/HsnRatesSection').then(m => ({ default: m.HsnRatesSection })));
const TdsRatesSection = lazy(() => import('../components/settings/TdsRatesSection').then(m => ({ default: m.TdsRatesSection })));
const PolicySchemaForm = lazy(() => import('../components/settings/PolicySchemaForm').then(m => ({ default: m.PolicySchemaForm })));
const RefundPolicySection = lazy(() => import('../pages/settings/RefundPolicyPage').then(m => ({ default: m.RefundPolicySection })));
const NotificationSettings = lazy(() => import('../components/settings/NotificationSettings').then(m => ({ default: m.NotificationSettings })));
const RemindersSettings = lazy(() => import('../pages/settings/RemindersSettings').then(m => ({ default: m.RemindersSettings })));
const IntegrationsHub = lazy(() => import('../components/settings/IntegrationsHub').then(m => ({ default: m.IntegrationsHub })));
const PrinterSettingsPage = lazy(() => import('../pages/settings/SettingsPrinters').then(m => ({ default: m.PrinterSettingsPage })));
const ApprovalWorkflows = lazy(() => import('../components/settings/ApprovalWorkflows').then(m => ({ default: m.ApprovalWorkflows })));
const AgentControlPanel = lazy(() => import('../components/settings/AgentControlPanel').then(m => ({ default: m.AgentControlPanel })));
const FeatureToggles = lazy(() => import('../components/settings/FeatureToggles').then(m => ({ default: m.FeatureToggles })));
const ShopifyLiveSyncSection = lazy(() => import('../pages/settings/SettingsShopifyLiveSync').then(m => ({ default: m.ShopifyLiveSyncSection })));
const AuditLogSettingsPage = lazy(() => import('../pages/settings/SettingsAuditLogs').then(m => ({ default: m.AuditLogSettingsPage })));
const SystemSettingsPage = lazy(() => import('../pages/settings/SettingsSystem').then(m => ({ default: m.SystemSettingsPage })));

// Other admin screens (unchanged)
// EntitiesPage retired from routing — /settings/entities now redirects to the
// canonical /organization screen (COUNCIL RULING §3). The page file is kept for
// a later-release deletion.
const OrganizationPage = lazy(() => import('../pages/settings/OrganizationPage'));
const SetupPage = lazy(() => import('../pages/settings/SetupPage'));
const GoLiveChecklistPage = lazy(() => import('../pages/settings/GoLiveChecklistPage').then(m => ({ default: m.GoLiveChecklistPage })));
const JarvisPage = lazy(() => import('../pages/jarvis/JarvisPage').then(m => ({ default: m.JarvisPage })));
const ActivityLogPage = lazy(() => import('../pages/admin/ActivityLogPage'));

// PolicySchemaForm + FeatureToggles take the active store id as a prop.
function PoliciesSectionPage() {
  const { user } = useAuth();
  return <PolicySchemaForm storeId={user?.activeStoreId || ''} />;
}
function FeatureTogglesSectionPage() {
  const { user } = useAuth();
  return <FeatureToggles storeId={user?.activeStoreId || ''} />;
}

// One element per section id — paired with SETTINGS_SECTIONS below, so a new
// section without an element (or vice versa) fails loudly in dev.
const SECTION_ELEMENTS: Record<SettingsTab, React.ReactElement> = {
  profile: <ProfileSection />,
  business: <BusinessSection />,
  users: <UserManagementSection />,
  categories: <CategorySection />,
  brands: <BrandSection />,
  'lens-master': <LensMasterSection />,
  'lens-enums': <LensCatalogEnumsSection />,
  'catalog-dictionary': <CatalogDictionarySection />,
  'lens-pricing': <LensRangePricingSection />,
  discounts: <DiscountSection />,
  loyalty: <LoyaltySettingsSection />,
  'tax-invoice': <TaxInvoiceSettingsPage />,
  'hsn-rates': <HsnRatesSection />,
  'tds-rates': <TdsRatesSection />,
  policies: <PoliciesSectionPage />,
  'refund-policy': <RefundPolicySection />,
  notifications: <NotificationSettings />,
  reminders: <RemindersSettings />,
  integrations: <IntegrationsHub />,
  printers: <PrinterSettingsPage />,
  approvals: <ApprovalWorkflows />,
  agents: <AgentControlPanel />,
  'feature-toggles': <FeatureTogglesSectionPage />,
  'shopify-live-sync': <ShopifyLiveSyncSection />,
  'audit-logs': <AuditLogSettingsPage />,
  system: <SystemSettingsPage />,
};

// Legacy ?tab= mapper: /settings and /settings?tab=<x> land on the section
// page, carrying any other query params along.
function SettingsTabRedirect() {
  const [searchParams] = useSearchParams();
  const tab = searchParams.get('tab') || '';
  const known = SETTINGS_SECTIONS.some(s => s.id === tab);
  const section = known ? tab : 'profile';
  const rest = new URLSearchParams(searchParams);
  rest.delete('tab');
  const suffix = rest.toString() ? `?${rest.toString()}` : '';
  return <Navigate to={`/settings/${section}${suffix}`} replace />;
}

export const settingsRoutes = (
  <>
    {/* Settings module — layout + one page per section. The layout gate is the
        old /settings module gate; each section re-gates per its declared
        roles (the same table that drives nav visibility). */}
    <Route
      path="settings"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'ACCOUNTANT']}>
          <SettingsLayout />
        </ProtectedRoute>
      }
    >
      <Route index element={<SettingsTabRedirect />} />
      {/* COUNCIL RULING §3: Entities are managed on the canonical
          /organization screen. Redirect the orphaned SPA route. */}
      <Route path="entities" element={<Navigate to="/organization" replace />} />
      {SETTINGS_SECTIONS.map((section) => (
        <Route
          key={section.id}
          path={section.id}
          element={
            section.role.includes('ALL') ? (
              <ProtectedRoute>{SECTION_ELEMENTS[section.id]}</ProtectedRoute>
            ) : (
              <ProtectedRoute allowedRoles={section.role as UserRole[]}>
                {SECTION_ELEMENTS[section.id]}
              </ProtectedRoute>
            )
          }
        />
      ))}
    </Route>

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
  </>
);
