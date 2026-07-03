// ============================================================================
// IMS 2.0 - Settings Page (Tab Container)
// ============================================================================
// Imports sub-components for each settings section:
// - SettingsProfile (profile, business)
// - SettingsAuth (user management)
// - SettingsStore (stores, categories, brands, discounts)
// - SettingsLens (lens brands, indices, coatings, add-ons)
// Remaining tabs (tax-invoice, notifications, integrations, printers,
// audit-logs, approvals, feature-toggles, system) are rendered inline.

import { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
  Users, Tag, Percent, Database,
  BookOpenCheck, ChevronRight, Plus, AlertCircle,
  RefreshCw, ToggleLeft, ToggleRight,
  Link, Boxes, CircleDot, Layers,
  User, Building2, Receipt, Bell, History, Printer, Save,
  Search, Calendar, Filter, X, Shield, LogOut, Bot, Award, Sliders, Target,
  RotateCcw, ExternalLink,
} from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import type { UserRole } from '../../types';
import {
  adminSystemApi,
  settingsApi,
  policiesApi,
} from '../../services/api';
import { financeApi } from '../../services/api/finance';

import { ApprovalWorkflows } from '../../components/settings/ApprovalWorkflows';
import { FeatureToggles } from '../../components/settings/FeatureToggles';
// IntegrationSettings (legacy 5-card panel) is NOT imported here anymore — the
// IntegrationsHub embeds it as a "Supplementary tools" section, so it remains the
// single integrations UI (COUNCIL RULING §3).
import { IntegrationsHub } from '../../components/settings/IntegrationsHub';
import { NotificationSettings } from '../../components/settings/NotificationSettings';
import { AdminControlPanel } from '../../components/settings/AdminControlPanel';
import { AgentControlPanel } from '../../components/settings/AgentControlPanel';
import { LoyaltySettingsSection } from '../../components/settings/LoyaltySettings';
import { LensRangePricingSection } from '../../components/settings/LensRangePricing';
import { HsnRatesSection } from '../../components/settings/HsnRatesSection';
import { TdsRatesSection } from '../../components/settings/TdsRatesSection';
import { PolicySchemaForm } from '../../components/settings/PolicySchemaForm';
import { AutoLogoutSettings } from '../../components/settings/AutoLogoutSettings';

// Sub-components
import { ProfileSection, BusinessSection } from './SettingsProfile';
import { UserManagementSection } from './SettingsAuth';
// StoreManagementSection intentionally NOT imported: Stores are managed ONLY on
// the canonical /organization screen now. The Settings "Stores" tab shows a
// redirect card (below). The component still exists in ./SettingsStore for a
// later-release deletion (COUNCIL RULING §3: redirect first, delete later).
import { CategorySection, BrandSection, DiscountSection } from './SettingsStore';
import { LensMasterSection } from './SettingsLens';
import { LensCatalogEnumsSection } from './SettingsLensEnums';
import { CatalogDictionarySection } from './SettingsCatalogDictionary';
import { RemindersSettings } from './RemindersSettings';
import { RefundPolicySection } from './RefundPolicyPage';
import type { SettingsTab } from './settingsTypes';

// ============================================================================
// Types for inline tabs
// ============================================================================

type AuditAction = 'LOGIN' | 'LOGOUT' | 'CREATE' | 'UPDATE' | 'DELETE' | 'EXPORT';

interface AuditLogEntry {
  id: string;
  timestamp: string;
  user_id: string;
  user_name: string;
  action: AuditAction;
  details: string;
  ip_address: string;
  entity_type?: string;
  entity_id?: string;
  changes?: Record<string, any>;
}

const AUDIT_ACTION_STYLES: Record<AuditAction, { bg: string; text: string; label: string }> = {
  LOGIN:  { bg: 'bg-purple-100', text: 'text-purple-700', label: 'Login' },
  LOGOUT: { bg: 'bg-gray-100',   text: 'text-gray-600',   label: 'Logout' },
  CREATE: { bg: 'bg-green-100',  text: 'text-green-700',  label: 'Create' },
  UPDATE: { bg: 'bg-blue-100',   text: 'text-blue-700',   label: 'Update' },
  DELETE: { bg: 'bg-red-100',    text: 'text-red-700',    label: 'Delete' },
  EXPORT: { bg: 'bg-amber-100',  text: 'text-amber-700',  label: 'Export' },
};

const AUDIT_ACTION_ROW_STYLES: Record<AuditAction, string> = {
  LOGIN:  '',
  LOGOUT: '',
  CREATE: 'bg-green-50/40',
  UPDATE: '',
  DELETE: 'bg-red-50/40',
  EXPORT: '',
};

// ============================================================================
// Settings IA — audience groups (COUNCIL RULING §3)
// ============================================================================
// Five audience buckets. The per-tab assignment below is a typed TOTAL map, so
// a new SettingsTab with no group is a compile error (no silent "More" orphan).
type GroupId = 'account' | 'org' | 'catalog' | 'compliance' | 'system';

// ============================================================================
// Sidebar section config
// ============================================================================

const SETTINGS_SECTIONS = [
  { id: 'profile' as SettingsTab, label: 'My Profile', icon: User, description: 'Account settings and preferences', role: ['ALL'] },
  { id: 'business' as SettingsTab, label: 'Business Profile', icon: Building2, description: 'Company info and branding', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'users' as SettingsTab, label: 'User Management', icon: Users, description: 'Manage users and roles', role: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
  { id: 'categories' as SettingsTab, label: 'Category Master', icon: Tag, description: 'Product categories and attributes', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  // Backend /admin/brands is gated SUPERADMIN/ADMIN — CATALOG_MANAGER was
  // shown this tab but 403'd on every call, so the tab matches the gate now.
  { id: 'brands' as SettingsTab, label: 'Brand Master', icon: Boxes, description: 'Brands, sub-brands and tier — drives the Catalog brand list', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'lens-master' as SettingsTab, label: 'Lens Master', icon: CircleDot, description: 'Lens brands, indices, coatings', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'lens-enums' as SettingsTab, label: 'Lens Catalog Enums', icon: Layers, description: 'Editable brand/coating/index/material/type lists for the typed catalog', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'catalog-dictionary' as SettingsTab, label: 'Catalog Dictionary', icon: BookOpenCheck, description: 'Allowed values per Add-Product field — only saved values can be chosen in Catalog', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'lens-pricing' as SettingsTab, label: 'Lens Pricing', icon: Receipt, description: 'Range-based tier pricing brackets', role: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { id: 'discounts' as SettingsTab, label: 'Discount Rules', icon: Percent, description: 'Role-based discount limits', role: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'] },
  { id: 'loyalty' as SettingsTab, label: 'Loyalty Programme', icon: Award, description: 'Earn rate, tiers, expiry, redemption rules', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'tax-invoice' as SettingsTab, label: 'Tax & Invoice', icon: Receipt, description: 'GST, invoice numbering', role: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
  { id: 'hsn-rates' as SettingsTab, label: 'HSN & GST Rates', icon: Percent, description: 'Edit GST rate per HSN code (govt revisions)', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'tds-rates' as SettingsTab, label: 'TDS Rates', icon: Percent, description: 'TDS rates on vendor / rent / contractor payments', role: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
  { id: 'policies' as SettingsTab, label: 'Policy Matrix', icon: Sliders, description: 'Scoped operational policies — discount caps, cash variance, refund tiers, promo, reminders (global → entity → store)', role: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT', 'STORE_MANAGER'] },
  { id: 'refund-policy' as SettingsTab, label: 'Refund Policy', icon: RotateCcw, description: 'Refund approval thresholds (auto / admin / superadmin), matrix on/off, original-tender hard-lock, and your approval PIN', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'notifications' as SettingsTab, label: 'Notifications', icon: Bell, description: 'SMS, WhatsApp templates', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'reminders' as SettingsTab, label: 'Reminders', icon: Bell, description: 'Configurable reminder rules — segment, channel, schedule, on/off (config only; no live send)', role: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
  { id: 'integrations' as SettingsTab, label: 'Integrations', icon: Link, description: 'Payment, Tally, Shopify', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'printers' as SettingsTab, label: 'Printers', icon: Printer, description: 'Receipt and label printers', role: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
  { id: 'approvals' as SettingsTab, label: 'Approval Workflows', icon: Shield, description: 'Configure approval rules; set your approval PIN', role: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
  { id: 'agents' as SettingsTab, label: 'AI Agents', icon: Bot, description: 'JARVIS agent control panel', role: ['SUPERADMIN'] },
  { id: 'feature-toggles' as SettingsTab, label: 'Feature Toggles', icon: ToggleLeft, description: 'Enable/disable system features per store', role: ['SUPERADMIN'] },
  { id: 'audit-logs' as SettingsTab, label: 'Audit Logs', icon: History, description: 'Activity history and logs', role: ['SUPERADMIN', 'ADMIN'] },
  { id: 'system' as SettingsTab, label: 'System', icon: Database, description: 'Backup, sync, maintenance', role: ['SUPERADMIN', 'ADMIN'] },
];

// ============================================================================
// Main Settings Page Component
// ============================================================================

export function SettingsPage() {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState<SettingsTab>('profile');

  // Sync active tab from URL query params
  useEffect(() => {
    const tabParam = searchParams.get('tab');
    if (tabParam && tabParam !== activeTab) {
      const validTabs: SettingsTab[] = ['profile', 'business', 'users', 'categories', 'brands', 'lens-master', 'lens-enums', 'catalog-dictionary', 'lens-pricing', 'discounts', 'loyalty', 'tax-invoice', 'hsn-rates', 'tds-rates', 'policies', 'refund-policy', 'notifications', 'reminders', 'integrations', 'printers', 'approvals', 'agents', 'feature-toggles', 'audit-logs', 'system'];
      if (validTabs.includes(tabParam as SettingsTab)) {
        setActiveTab(tabParam as SettingsTab);
      }
    }
  }, [searchParams]);

  // ---- State for inline tabs (tax-invoice, printers, audit-logs, system) ----
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Tax & Invoice
  const [taxSettings, setTaxSettings] = useState<{
    gst_enabled: boolean;
    company_gstin: string;
    default_gst_rate: number;
    hsn_validation: boolean;
    e_invoice_enabled: boolean;
    e_way_bill_enabled: boolean;
    e_way_bill_threshold: number;
  } | null>(null);
  const [invoiceSettings, setInvoiceSettings] = useState<{
    invoice_prefix: string;
    current_invoice_number: number;
    financial_year: string;
    show_logo_on_invoice: boolean;
    show_terms_on_invoice: boolean;
    default_terms: string;
    default_warranty_days: number;
    show_qr_code: boolean;
  } | null>(null);

  // Printer
  const [printerSettings, setPrinterSettings] = useState<{
    receipt_printer_name: string;
    receipt_printer_width: number;
    label_printer_name: string;
    label_size: string;
    auto_print_receipt: boolean;
    auto_print_job_card: boolean;
    copies_per_print: number;
    qz_enabled?: boolean;
    auto_print_stage_sticker?: boolean;
  } | null>(null);
  const [availablePrinters, setAvailablePrinters] = useState<Array<{ name: string; type: string; status: string }>>([]);

  // System
  const [systemStatus, setSystemStatus] = useState<{ database: string; api: string; version: string } | null>(null);

  // Audit logs
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [auditSummary, setAuditSummary] = useState<{
    today: { total_actions: number; logins: number; orders_created: number };
  } | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [auditActionFilter, setAuditActionFilter] = useState<AuditAction | ''>('');
  const [auditSearchQuery, setAuditSearchQuery] = useState('');
  const [auditDateFrom, setAuditDateFrom] = useState('');
  const [auditDateTo, setAuditDateTo] = useState('');

  // Filter sidebar by user role
  const visibleSections = SETTINGS_SECTIONS.filter(section => {
    if (!user) return false;
    if (section.role.includes('ALL')) return true;
    const userRoles = user.roles || [user.activeRole];
    return section.role.some(role => userRoles.includes(role as UserRole)) || user.activeRole === 'SUPERADMIN';
  });

  /**
   * COUNCIL RULING §3 — taxonomy by AUDIENCE, ~5 buckets, NO "More" orphan.
   * Group membership is a TYPED TOTAL MAP (`Record<SettingsTab, GroupId>`):
   * every tab MUST name its group, so adding a SettingsTab without assigning
   * it a group is a COMPILE ERROR rather than silently falling into a "More"
   * bucket (which is how Lens Pricing + Loyalty were previously dropped). The
   * orphan-catch is removed — the type system is the guarantee now.
   */
  const SETTINGS_GROUP_OF: Record<SettingsTab, GroupId> = {
    // My Account
    profile: 'account',
    // Business & Org (the Organization link lives in this group's header)
    business: 'org',
    users: 'org',
    // Catalog & Pricing
    categories: 'catalog',
    brands: 'catalog',
    'lens-master': 'catalog',
    'lens-enums': 'catalog',
    'catalog-dictionary': 'catalog',
    'lens-pricing': 'catalog',
    discounts: 'catalog',
    loyalty: 'catalog',
    // Compliance & Finance
    'tax-invoice': 'compliance',
    'hsn-rates': 'compliance',
    'tds-rates': 'compliance',
    policies: 'compliance',
    'refund-policy': 'compliance',
    // System & Admin
    notifications: 'system',
    reminders: 'system',
    integrations: 'system',
    printers: 'system',
    approvals: 'system',
    agents: 'system',
    'feature-toggles': 'system',
    'audit-logs': 'system',
    system: 'system',
  };
  const SETTINGS_GROUPS: Array<{ id: GroupId; label: string }> = [
    { id: 'account',    label: 'My Account' },
    { id: 'org',        label: 'Business & Org' },
    { id: 'catalog',    label: 'Catalog & Pricing' },
    { id: 'compliance', label: 'Compliance & Finance' },
    { id: 'system',     label: 'System & Admin' },
  ];

  // Load data for inline tabs
  useEffect(() => {
    loadInlineTabData();
  }, [activeTab]);

  const loadInlineTabData = async () => {
    // Only load for tabs that are still managed inline
    const inlineTabs: SettingsTab[] = ['tax-invoice', 'printers', 'audit-logs', 'system'];
    if (!inlineTabs.includes(activeTab)) return;

    setIsLoading(true);
    setError(null);
    setAuditError(null);

    try {
      switch (activeTab) {
        case 'tax-invoice':
          try {
            const [taxRes, invoiceRes] = await Promise.all([
              settingsApi.getTaxSettings().catch(() => null),
              settingsApi.getInvoiceSettings().catch(() => null),
            ]);
            if (taxRes) setTaxSettings(taxRes);
            if (invoiceRes) setInvoiceSettings(invoiceRes);
          } catch {
            // Use defaults
          }
          break;

        case 'printers':
          try {
            const [printerRes, availableRes] = await Promise.all([
              settingsApi.getPrinterSettings().catch(() => null),
              settingsApi.getAvailablePrinters().catch(() => ({ printers: [] })),
            ]);
            if (printerRes) setPrinterSettings(printerRes);
            setAvailablePrinters(availableRes.printers || []);
          } catch {
            // Use defaults
          }
          break;

        case 'audit-logs':
          setAuditError(null);
          try {
            const [logsRes, summaryRes] = await Promise.all([
              settingsApi.getAuditLogs({ limit: 50 }),
              settingsApi.getAuditSummary().catch(() => null),
            ]);
            setAuditLogs(logsRes.logs || []);
            setAuditSummary(summaryRes || null);
          } catch (err) {
            setAuditLogs([]);
            setAuditSummary(null);
            setAuditError(err instanceof Error ? err.message : 'Failed to load audit logs');
          }
          break;

        case 'system':
          try {
            const statusRes = await adminSystemApi.getSystemStatus().catch(() => null);
            if (statusRes) {
              setSystemStatus(statusRes);
            }
          } catch {
            // Ignore
          }
          break;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setIsLoading(false);
    }
  };

  // ============================================================================
  // Render
  // ============================================================================

  const activeSection = visibleSections.find(s => s.id === activeTab) ?? visibleSections[0];

  return (
    <div className="setup-grid">
      {/* Left nav — 240px, sections bucketed into 5 functional groups */}
      <nav className="s-nav">
        <span className="eyebrow">Configuration · {visibleSections.length} sections</span>
        {SETTINGS_GROUPS.map((group) => {
          const groupSections = visibleSections.filter((s) => SETTINGS_GROUP_OF[s.id] === group.id);
          if (groupSections.length === 0) return null;
          return (
            <div key={group.id}>
              <span className="s-nav-group">{group.label}</span>
              {groupSections.map((section) => {
                const IconCmp = section.icon;
                return (
                  <button
                    key={section.id}
                    type="button"
                    onClick={() => setActiveTab(section.id)}
                    className={'s-nav-item' + (activeTab === section.id ? ' on' : '')}
                    title={section.description}
                  >
                    <IconCmp className="s-nav-icon" />
                    <span className="s-nav-label">{section.label}</span>
                  </button>
                );
              })}
              {/* Business & Org links out to the canonical Organization screen
                  (entities + stores live there, not here). */}
              {group.id === 'org' && (
                <button
                  type="button"
                  onClick={() => navigate('/organization')}
                  className="s-nav-item"
                  title="Manage legal entities and stores (canonical screen)"
                >
                  <Building2 className="s-nav-icon" />
                  <span className="s-nav-label">Organization</span>
                  <ExternalLink className="s-nav-icon" style={{ marginLeft: 'auto', opacity: 0.5 }} />
                </button>
              )}
            </div>
          );
        })}
        {user?.activeRole === 'SUPERADMIN' && (
          <>
            <div className="divider" />
            <span className="eyebrow">Superadmin</span>
            <div style={{ padding: '0 10px', fontSize: 11, color: 'var(--ink-4)' }}>
              Elevated mode — all changes audit-logged.
            </div>
          </>
        )}
      </nav>

      {/* Content */}
      <div className="s-content">
        <div className="s-head">
          <span className="eyebrow" style={{ display: 'block', marginBottom: 6 }}>
            {activeSection?.description ?? ''}
          </span>
          <h1>{activeSection?.label ?? 'Store Setup'}</h1>
          <p className="sub">
            System configuration and master data management. Some settings are <strong>locked at HQ level</strong> — changes require superadmin approval and are recorded in audit.
          </p>
        </div>

        {/* Error Banner */}
        {error && (
          <div className="s-section" style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertCircle className="w-5 h-5" style={{ color: 'var(--err)' }} />
            <span className="t" style={{ color: 'var(--err)' }}>{error}</span>
            <button onClick={loadInlineTabData} className="btn sm" style={{ marginLeft: 'auto' }}>
              Retry
            </button>
          </div>
        )}

        <div className="s-section-body">
          {/* Inline loading spinner for inline tabs only */}
          {isLoading && ['tax-invoice', 'printers', 'audit-logs', 'system'].includes(activeTab) && (
            <div className="flex items-center justify-center h-48">
              <RefreshCw className="w-8 h-8 animate-spin" style={{ color: 'var(--bv)' }} />
            </div>
          )}

          {/* ---- Delegated sub-components ---- */}
          {activeTab === 'profile' && <ProfileSection />}
          {activeTab === 'business' && <BusinessSection />}
          {activeTab === 'users' && <UserManagementSection />}
          {activeTab === 'categories' && <CategorySection />}
          {activeTab === 'brands' && <BrandSection />}
          {activeTab === 'lens-master' && <LensMasterSection />}
          {activeTab === 'lens-enums' && <LensCatalogEnumsSection />}
          {activeTab === 'catalog-dictionary' && <CatalogDictionarySection />}
          {activeTab === 'discounts' && <DiscountSection />}

          {/* ---- Existing component delegates ---- */}
          {activeTab === 'integrations' && (
            <div>
              {/* IntegrationsHub is the ONLY integrations UI: one catalog-driven
                  card grid (per-card Test Connection + Configure), plus a
                  "Supplementary tools" section (Tally per-store export table +
                  the SUPERADMIN-only read-only status card). The duplicate
                  hardcoded 6-card grid that IntegrationSettings used to render
                  was removed. The catalog endpoint is ADMIN+SUPERADMIN, matching
                  the GET/PUT integration-config gating. */}
              <IntegrationsHub />
            </div>
          )}
          {activeTab === 'notifications' && <div><NotificationSettings /></div>}
          {activeTab === 'reminders' && <div><RemindersSettings /></div>}
          {activeTab === 'approvals' && <div><ApprovalWorkflows /></div>}
          {activeTab === 'agents' && <div><AgentControlPanel /></div>}
          {activeTab === 'loyalty' && <div><LoyaltySettingsSection /></div>}
          {activeTab === 'lens-pricing' && <div><LensRangePricingSection /></div>}
          {activeTab === 'hsn-rates' && <div><HsnRatesSection /></div>}
          {activeTab === 'tds-rates' && <div><TdsRatesSection /></div>}
          {activeTab === 'policies' && <div><PolicySchemaForm storeId={user?.activeStoreId || ''} /></div>}
          {activeTab === 'refund-policy' && <div><RefundPolicySection /></div>}
          {activeTab === 'feature-toggles' && <div><FeatureToggles storeId={user?.activeStoreId || ''} /></div>}

          {/* ---- Inline tabs ---- */}
          {!isLoading && activeTab === 'tax-invoice' && (
            <TaxInvoiceSection
              taxSettings={taxSettings}
              setTaxSettings={setTaxSettings}
              invoiceSettings={invoiceSettings}
              setInvoiceSettings={setInvoiceSettings}
            />
          )}

          {!isLoading && activeTab === 'printers' && (
            <PrinterSection
              printerSettings={printerSettings}
              setPrinterSettings={setPrinterSettings}
              availablePrinters={availablePrinters}
            />
          )}

          {!isLoading && activeTab === 'audit-logs' && (
            <AuditLogSection
              auditLogs={auditLogs}
              auditSummary={auditSummary}
              auditError={auditError}
              auditActionFilter={auditActionFilter}
              setAuditActionFilter={setAuditActionFilter}
              auditSearchQuery={auditSearchQuery}
              setAuditSearchQuery={setAuditSearchQuery}
              auditDateFrom={auditDateFrom}
              setAuditDateFrom={setAuditDateFrom}
              auditDateTo={auditDateTo}
              setAuditDateTo={setAuditDateTo}
              onRefresh={loadInlineTabData}
            />
          )}

          {!isLoading && activeTab === 'system' && (
            <SystemSection systemStatus={systemStatus} />
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Tax & Invoice Section (inline)
// ============================================================================

function TaxInvoiceSection({
  taxSettings,
  setTaxSettings,
  invoiceSettings,
  setInvoiceSettings,
}: {
  taxSettings: any;
  setTaxSettings: (fn: any) => void;
  invoiceSettings: any;
  setInvoiceSettings: (fn: any) => void;
}) {
  const toast = useToast();

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Tax Settings</h2>
        <div className="space-y-4">
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
            <div>
              <p className="font-medium text-gray-900">GST Enabled</p>
              <p className="text-sm text-gray-500">Apply GST to all transactions</p>
            </div>
            {taxSettings?.gst_enabled ? (
              <ToggleRight className="w-8 h-8 text-green-600 cursor-pointer" onClick={() => setTaxSettings((prev: any) => prev ? { ...prev, gst_enabled: false } : null)} />
            ) : (
              <ToggleLeft className="w-8 h-8 text-gray-500 cursor-pointer" onClick={() => setTaxSettings((prev: any) => prev ? { ...prev, gst_enabled: true } : null)} />
            )}
          </div>
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Company GSTIN</label>
              <input
                type="text"
                value={taxSettings?.company_gstin || ''}
                onChange={e => setTaxSettings((prev: any) => prev ? { ...prev, company_gstin: e.target.value.toUpperCase() } : null)}
                placeholder="19ABCDE1234F1Z5"
                className="input-field font-mono"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Default GST Rate (%)</label>
              <input
                type="number"
                value={taxSettings?.default_gst_rate || 18}
                onChange={e => setTaxSettings((prev: any) => prev ? { ...prev, default_gst_rate: parseFloat(e.target.value) } : null)}
                className="input-field"
              />
            </div>
          </div>
          {/* COUNCIL RULING §3: HIDE until wired. E-Invoice (IRN / digital
              signature), E-Way Bill auto-generate, and HSN-validation are inert
              toggles — a control that does nothing is a false-security lie. They
              are intentionally not rendered until the integration is actually
              wired. (The underlying fields stay in the settings model so no data
              is lost; nothing reads them yet.) */}
        </div>
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Invoice Settings</h2>
        <div className="space-y-4">
          {/* COUNCIL RULING §3: HIDE until wired. Invoice-numbering config
              (prefix / current number / financial year) is NOT GST-compliant
              yet (the serial generator is a separate, sign-off-gated change) and
              editing it here does not change the numbers the system actually
              issues. Hidden so it can't read as a working control. */}
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">Default Terms & Conditions</label>
            <textarea
              value={invoiceSettings?.default_terms || ''}
              onChange={e => setInvoiceSettings((prev: any) => prev ? { ...prev, default_terms: e.target.value } : null)}
              rows={3}
              className="input-field"
            />
          </div>
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Default Warranty (days)</label>
              <input
                type="number"
                value={invoiceSettings?.default_warranty_days || 365}
                onChange={e => setInvoiceSettings((prev: any) => prev ? { ...prev, default_warranty_days: parseInt(e.target.value) } : null)}
                className="input-field"
              />
            </div>
          </div>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={invoiceSettings?.show_logo_on_invoice ?? false}
                onChange={e => setInvoiceSettings((prev: any) => prev ? { ...prev, show_logo_on_invoice: e.target.checked } : null)}
                className="rounded border-gray-300"
              />
              <span className="text-sm">Show logo on invoice</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={invoiceSettings?.show_qr_code ?? false}
                onChange={e => setInvoiceSettings((prev: any) => prev ? { ...prev, show_qr_code: e.target.checked } : null)}
                className="rounded border-gray-300"
              />
              <span className="text-sm">Show QR code</span>
            </label>
          </div>
          <button
            onClick={async () => {
              try {
                await Promise.all([
                  settingsApi.updateTaxSettings(taxSettings || {}),
                  settingsApi.updateInvoiceSettings(invoiceSettings || {}),
                ]);
                toast.success('Settings saved');
              } catch {
                toast.error('Failed to save settings');
              }
            }}
            className="btn-primary"
          >
            <Save className="w-4 h-4 mr-2" />
            Save Tax & Invoice Settings
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Printer Section (inline)
// ============================================================================

function PrinterSection({
  printerSettings,
  setPrinterSettings,
  availablePrinters,
}: {
  printerSettings: any;
  setPrinterSettings: (fn: any) => void;
  availablePrinters: Array<{ name: string; type: string; status: string }>;
}) {
  const toast = useToast();

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Printer Configuration</h2>
        {/* COUNCIL RULING §3: KEEP printer settings, with an honesty note. */}
        <div className="mb-4 flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-3">
          <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>
            These preferences are saved, but the silent-print path (QZ Tray) only
            takes effect on terminals where QZ + a signing certificate are
            installed. Where it is not yet wired, labels open in a print window.
          </span>
        </div>
        <div className="space-y-4">
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Receipt Printer</label>
              <select
                value={printerSettings?.receipt_printer_name || ''}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, receipt_printer_name: e.target.value } : null)}
                className="input-field"
              >
                <option value="">Select printer...</option>
                {availablePrinters.filter(p => p.type === 'RECEIPT').map(p => (
                  <option key={p.name} value={p.name}>{p.name} ({p.status})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Receipt Width (mm)</label>
              <select
                value={printerSettings?.receipt_printer_width || 80}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, receipt_printer_width: parseInt(e.target.value) } : null)}
                className="input-field"
              >
                <option value={58}>58mm (2 inch)</option>
                <option value={80}>80mm (3 inch)</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Label Printer</label>
              <select
                value={printerSettings?.label_printer_name || ''}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, label_printer_name: e.target.value } : null)}
                className="input-field"
              >
                <option value="">Select printer...</option>
                {availablePrinters.filter(p => p.type === 'LABEL').map(p => (
                  <option key={p.name} value={p.name}>{p.name} ({p.status})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Label Size</label>
              <select
                value={printerSettings?.label_size || '50x25'}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, label_size: e.target.value } : null)}
                className="input-field"
              >
                <option value="50x25">50 x 25 mm</option>
                <option value="50x30">50 x 30 mm</option>
                <option value="100x50">100 x 50 mm</option>
              </select>
            </div>
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-2 cursor-pointer p-2 hover:bg-gray-100 rounded">
              <input
                type="checkbox"
                checked={printerSettings?.auto_print_receipt}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, auto_print_receipt: e.target.checked } : null)}
                className="rounded border-gray-300 text-bv-red-600"
              />
              <span className="text-sm">Auto-print receipt after payment</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer p-2 hover:bg-gray-100 rounded">
              <input
                type="checkbox"
                checked={printerSettings?.auto_print_job_card}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, auto_print_job_card: e.target.checked } : null)}
                className="rounded border-gray-300 text-bv-red-600"
              />
              <span className="text-sm">Auto-print job card for workshop orders</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer p-2 hover:bg-gray-100 rounded">
              <input
                type="checkbox"
                checked={printerSettings?.auto_print_stage_sticker ?? true}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, auto_print_stage_sticker: e.target.checked } : null)}
                className="rounded border-gray-300 text-bv-red-600"
              />
              <span className="text-sm">Auto-print stage sticker when a job advances</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer p-2 hover:bg-gray-100 rounded">
              <input
                type="checkbox"
                checked={printerSettings?.qz_enabled ?? true}
                onChange={e => setPrinterSettings((prev: any) => prev ? { ...prev, qz_enabled: e.target.checked } : null)}
                className="rounded border-gray-300 text-bv-red-600"
              />
              <span className="text-sm">
                Use QZ Tray for silent label printing
                <span className="block text-xs text-gray-500">When off (or QZ/cert not configured), labels open in a print window.</span>
              </span>
            </label>
          </div>

          <button
            onClick={async () => {
              try {
                await settingsApi.updatePrinterSettings(printerSettings || {});
                toast.success('Printer settings saved');
              } catch {
                toast.error('Failed to save settings');
              }
            }}
            className="btn-primary"
          >
            <Save className="w-4 h-4 mr-2" />
            Save Printer Settings
          </button>
        </div>
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Available Printers</h2>
        <div className="space-y-2">
          {availablePrinters.length === 0 ? (
            <p className="text-gray-500 text-center py-4">No printers detected on network</p>
          ) : (
            availablePrinters.map(printer => (
              <div key={printer.name} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                <div className="flex items-center gap-3">
                  <Printer className="w-5 h-5 text-gray-500" />
                  <div>
                    <p className="font-medium text-gray-900">{printer.name}</p>
                    <p className="text-xs text-gray-500">{printer.type}</p>
                  </div>
                </div>
                <span className={clsx(
                  'text-xs px-2 py-1 rounded',
                  printer.status === 'online' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                )}>
                  {printer.status}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Audit Log Section (inline)
// ============================================================================

function AuditLogSection({
  auditLogs,
  auditSummary,
  auditError,
  auditActionFilter,
  setAuditActionFilter,
  auditSearchQuery,
  setAuditSearchQuery,
  auditDateFrom,
  setAuditDateFrom,
  auditDateTo,
  setAuditDateTo,
  onRefresh,
}: {
  auditLogs: AuditLogEntry[];
  auditSummary: { today: { total_actions: number; logins: number; orders_created: number } } | null;
  auditError: string | null;
  auditActionFilter: AuditAction | '';
  setAuditActionFilter: (v: AuditAction | '') => void;
  auditSearchQuery: string;
  setAuditSearchQuery: (v: string) => void;
  auditDateFrom: string;
  setAuditDateFrom: (v: string) => void;
  auditDateTo: string;
  setAuditDateTo: (v: string) => void;
  onRefresh: () => void;
}) {
  const filteredLogs = auditLogs.filter(log => {
    if (auditActionFilter && log.action !== auditActionFilter) return false;
    if (auditSearchQuery && !log.user_name.toLowerCase().includes(auditSearchQuery.toLowerCase())) return false;
    if (auditDateFrom) {
      const logDate = new Date(log.timestamp);
      const fromDate = new Date(auditDateFrom);
      fromDate.setHours(0, 0, 0, 0);
      if (logDate < fromDate) return false;
    }
    if (auditDateTo) {
      const logDate = new Date(log.timestamp);
      const toDate = new Date(auditDateTo);
      toDate.setHours(23, 59, 59, 999);
      if (logDate > toDate) return false;
    }
    return true;
  });

  const hasActiveFilters = !!(auditActionFilter || auditSearchQuery || auditDateFrom || auditDateTo);

  return (
    <div className="space-y-4">
      {/* Error Banner */}
      {auditError && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <span className="text-sm text-red-700">{auditError}</span>
          <button onClick={onRefresh} className="ml-auto text-sm text-red-600 hover:underline flex-shrink-0">
            Retry
          </button>
        </div>
      )}

      {/* Summary Cards */}
      {auditSummary && (
        <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-1">
              <Shield className="w-4 h-4 text-gray-500" />
              <p className="text-sm text-gray-500">Total Actions</p>
            </div>
            <p className="text-2xl font-bold text-gray-900">{auditSummary.today.total_actions}</p>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-1">
              <LogOut className="w-4 h-4 text-green-600" />
              <p className="text-sm text-gray-500">Logins</p>
            </div>
            <p className="text-2xl font-bold text-green-600">{auditSummary.today.logins}</p>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-1">
              <Plus className="w-4 h-4 text-blue-600" />
              <p className="text-sm text-gray-500">Orders Created</p>
            </div>
            <p className="text-2xl font-bold text-blue-600">{auditSummary.today.orders_created}</p>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-1">
              <AlertCircle className="w-4 h-4 text-green-600" />
              <p className="text-sm text-gray-500">System Health</p>
            </div>
            <p className="text-2xl font-bold text-green-600">Good</p>
          </div>
        </div>
      )}

      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <History className="w-5 h-5 text-gray-500" />
            <h2 className="text-lg font-semibold text-gray-900">Activity Log</h2>
            <span className="text-sm text-gray-500 ml-1">
              ({filteredLogs.length}{hasActiveFilters ? ` of ${auditLogs.length}` : ''} entries)
            </span>
          </div>
          <button onClick={onRefresh} className="btn-outline flex items-center gap-1" title="Refresh logs">
            <RefreshCw className="w-4 h-4" />
            <span className="hidden sm:inline text-sm">Refresh</span>
          </button>
        </div>

        {/* Filters Row */}
        <div className="flex flex-wrap items-end gap-3 mb-4 p-3 bg-gray-50 rounded-lg border border-gray-200">
          <div className="flex items-center gap-1 text-sm font-medium text-gray-500">
            <Filter className="w-4 h-4" />
            Filters
          </div>

          <div className="flex-1 min-w-[180px]">
            <label className="block text-xs text-gray-500 mb-1">Search User</label>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
              <input
                type="text"
                placeholder="Search by user name..."
                value={auditSearchQuery}
                onChange={e => setAuditSearchQuery(e.target.value)}
                className="input-field pl-8 w-full"
              />
            </div>
          </div>

          <div className="min-w-[150px]">
            <label className="block text-xs text-gray-500 mb-1">Action Type</label>
            <select
              value={auditActionFilter}
              onChange={e => setAuditActionFilter(e.target.value as AuditAction | '')}
              className="input-field w-full"
            >
              <option value="">All Actions</option>
              <option value="LOGIN">Login</option>
              <option value="LOGOUT">Logout</option>
              <option value="CREATE">Create</option>
              <option value="UPDATE">Update</option>
              <option value="DELETE">Delete</option>
              <option value="EXPORT">Export</option>
            </select>
          </div>

          <div className="min-w-[150px]">
            <label className="block text-xs text-gray-500 mb-1">
              <span className="flex items-center gap-1"><Calendar className="w-3 h-3" /> From</span>
            </label>
            <input
              type="date"
              value={auditDateFrom}
              onChange={e => setAuditDateFrom(e.target.value)}
              className="input-field w-full"
            />
          </div>

          <div className="min-w-[150px]">
            <label className="block text-xs text-gray-500 mb-1">
              <span className="flex items-center gap-1"><Calendar className="w-3 h-3" /> To</span>
            </label>
            <input
              type="date"
              value={auditDateTo}
              onChange={e => setAuditDateTo(e.target.value)}
              className="input-field w-full"
            />
          </div>

          {hasActiveFilters && (
            <button
              onClick={() => {
                setAuditActionFilter('');
                setAuditSearchQuery('');
                setAuditDateFrom('');
                setAuditDateTo('');
              }}
              className="btn-outline text-sm flex items-center gap-1 self-end"
            >
              <X className="w-3.5 h-3.5" />
              Clear
            </button>
          )}
        </div>

        {/* Table */}
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Timestamp</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">User</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Details</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">IP Address</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {filteredLogs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-gray-500">
                    <History className="w-12 h-12 mx-auto mb-3 opacity-50" />
                    {auditLogs.length === 0 && !hasActiveFilters ? (
                      <p className="font-medium">No audit logs yet</p>
                    ) : (
                      <>
                        <p className="font-medium">No audit logs found</p>
                        {hasActiveFilters && (
                          <p className="text-sm mt-1">Try adjusting your filters to see more results.</p>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ) : (
                filteredLogs.map(log => {
                  const actionKey = log.action as AuditAction;
                  const style = AUDIT_ACTION_STYLES[actionKey] || AUDIT_ACTION_STYLES.UPDATE;
                  const rowBg = AUDIT_ACTION_ROW_STYLES[actionKey] || '';

                  return (
                    <tr key={log.id} className={clsx('hover:bg-gray-100 transition-colors', rowBg)}>
                      <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">
                        <div>{new Date(log.timestamp).toLocaleDateString()}</div>
                        <div className="text-xs text-gray-500">{new Date(log.timestamp).toLocaleTimeString()}</div>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <p className="text-sm font-medium text-gray-900">{log.user_name}</p>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <span className={clsx(
                          'inline-flex items-center text-xs font-semibold px-2.5 py-1 rounded-full',
                          style.bg, style.text
                        )}>
                          {style.label}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <p className={clsx(
                          'text-sm',
                          actionKey === 'DELETE' ? 'text-red-700' :
                          actionKey === 'CREATE' ? 'text-green-700' :
                          'text-gray-600'
                        )}>
                          {log.details}
                        </p>
                        {log.entity_type && (
                          <p className="text-xs text-gray-500 mt-0.5">
                            {log.entity_type}{log.entity_id ? ` / ${log.entity_id}` : ''}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500 font-mono whitespace-nowrap">
                        {log.ip_address || '-'}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {filteredLogs.length > 0 && (
          <p className="text-xs text-gray-500 mt-3 text-right">
            Showing {filteredLogs.length} log {filteredLogs.length === 1 ? 'entry' : 'entries'}
            {hasActiveFilters ? ' (filtered)' : ''}
          </p>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// System Section (inline)
// ============================================================================

function SystemSection({ systemStatus }: { systemStatus: { database: string; api: string; version: string } | null }) {
  const toast = useToast();

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">System Status</h2>
        <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-3 gap-4">
          <div className={clsx('p-4 rounded-lg', systemStatus?.database === 'connected' ? 'bg-green-50' : 'bg-yellow-50')}>
            <p className="text-sm text-gray-500">Database</p>
            <p className={clsx('font-medium', systemStatus?.database === 'connected' ? 'text-green-600' : 'text-yellow-600')}>
              {systemStatus?.database || 'Checking...'}
            </p>
          </div>
          <div className={clsx('p-4 rounded-lg', systemStatus?.api === 'healthy' ? 'bg-green-50' : 'bg-yellow-50')}>
            <p className="text-sm text-gray-500">API Status</p>
            <p className={clsx('font-medium', systemStatus?.api === 'healthy' ? 'text-green-600' : 'text-yellow-600')}>
              {systemStatus?.api || 'Checking...'}
            </p>
          </div>
          <div className="p-4 bg-blue-50 rounded-lg">
            <p className="text-sm text-gray-500">Version</p>
            <p className="font-medium text-blue-600">{systemStatus?.version || '2.0.0'}</p>
          </div>
        </div>
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Data Management</h2>
        <div className="space-y-3">
          {/* Import Data (POST /admin/system/import/{type}) and Export Data
              (GET /admin/system/export/{type}) buttons were removed: the
              import route doesn't exist (404) and export is a hardcoded 501
              stub. Bulk data import/export is a sensitive operation worth
              building properly (with format + permission handling) rather
              than shipping broken buttons. Backup Database below works. */}
          <button
            onClick={async () => {
              if (window.confirm('Create a full system backup?')) {
                try {
                  await adminSystemApi.createBackup();
                  toast.success('Backup created successfully');
                } catch {
                  toast.error('Failed to create backup');
                }
              }
            }}
            className="w-full p-4 bg-gray-50 rounded-lg text-left hover:bg-gray-200 transition-colors flex items-center justify-between"
          >
            <div className="flex items-center gap-3">
              <Database className="w-5 h-5 text-gray-500" />
              <div>
                <p className="font-medium text-gray-900">Backup Database</p>
                <p className="text-sm text-gray-500">Create full system backup</p>
              </div>
            </div>
            <ChevronRight className="w-5 h-5 text-gray-500" />
          </button>
        </div>
      </div>

      <div className="card mt-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Admin Controls -- Store & Role Configuration</h2>
        <AdminControlPanel />
      </div>

      {/* F34 target-ticker config (SUPERADMIN/ADMIN; the System tab is already
          role-gated to them). Persisted to the two E2 policy keys. */}
      <TargetTickerSettings />

      {/* Idle auto-logout policy (SUPERADMIN-editable; persisted to the
          system_settings singleton, served on /health to every user). */}
      <AutoLogoutSettings />
    </div>
  );
}

// ============================================================================
// Target Ticker Settings (F34) -- milestone thresholds + refresh interval
// ============================================================================

function TargetTickerSettings() {
  const toast = useToast();
  const [milestonesCsv, setMilestonesCsv] = useState('25,50,75,100');
  const [refreshSeconds, setRefreshSeconds] = useState(60);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    policiesApi
      .getAll('global')
      .then((res) => {
        if (!alive) return;
        const pol = res?.policies || {};
        const pcts = pol['ticker.milestone_pcts']?.value;
        const refresh = pol['ticker.refresh_seconds']?.value;
        if (Array.isArray(pcts) && pcts.length > 0) setMilestonesCsv(pcts.join(','));
        if (typeof refresh === 'number' && refresh > 0) setRefreshSeconds(refresh);
      })
      .catch(() => {
        /* fall back to defaults */
      });
    return () => {
      alive = false;
    };
  }, []);

  const save = async () => {
    // Parse + validate the comma-separated thresholds (1..100 integers).
    const pcts = milestonesCsv
      .split(',')
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => !Number.isNaN(n));
    if (pcts.length === 0 || pcts.some((n) => n < 1 || n > 100)) {
      toast.error('Milestone thresholds must be integers between 1 and 100');
      return;
    }
    if (refreshSeconds < 30 || refreshSeconds > 300) {
      toast.error('Refresh interval must be between 30 and 300 seconds');
      return;
    }
    setSaving(true);
    try {
      const res = await financeApi.updateTickerSettings({ milestone_pcts: pcts, refresh_seconds: refreshSeconds });
      setMilestonesCsv((res.milestone_pcts || pcts).join(','));
      setRefreshSeconds(res.refresh_seconds || refreshSeconds);
      toast.success('Target ticker settings saved');
    } catch {
      toast.error('Failed to save target ticker settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card mt-6">
      <div className="flex items-center gap-2 mb-1">
        <Target className="w-5 h-5 text-gray-500" />
        <h2 className="text-lg font-semibold text-gray-900">Target Ticker</h2>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        The monthly-target progress card on the Hub. Milestone crossings push a one-time celebratory
        bell to store-floor staff.
      </p>
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-600 mb-1">Milestone thresholds (%)</label>
          <input
            type="text"
            value={milestonesCsv}
            onChange={(e) => setMilestonesCsv(e.target.value)}
            placeholder="25,50,75,100"
            className="input-field"
          />
          <p className="text-xs text-gray-400 mt-1">Comma-separated integers, each 1-100.</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-600 mb-1">Refresh every (seconds)</label>
          <input
            type="number"
            min={30}
            max={300}
            value={refreshSeconds}
            placeholder="60"
            title="Refresh interval in seconds"
            onChange={(e) => setRefreshSeconds(parseInt(e.target.value, 10) || 0)}
            className="input-field"
          />
          <p className="text-xs text-gray-400 mt-1">How often the Hub card re-polls (30-300).</p>
        </div>
      </div>
      <button type="button" onClick={save} disabled={saving} className="btn-primary mt-4">
        <Save className="w-4 h-4 mr-2" />
        {saving ? 'Saving…' : 'Save Target Ticker Settings'}
      </button>
    </div>
  );
}

export default SettingsPage;
