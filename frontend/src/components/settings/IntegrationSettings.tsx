// ============================================================================
// IMS 2.0 - Integration Settings UI
// ============================================================================
// Per-integration configure form: opens a modal, lets ADMIN/SUPERADMIN type in
// API keys / URLs / etc., and saves to the canonical PUT
// /api/v1/settings/integrations/{type} endpoint (the same Mongo doc that
// nexus_providers.py + services/shiprocket.py read at runtime).
//
// Sensitive fields are masked on the initial display (the backend returns
// masked values like "abcd**...**12" for SENSITIVE_FIELDS via _mask_config).
// We never log raw values, never put them in toasts, never put them on a URL.

import { useState, useEffect, useCallback } from 'react';
import {
  Eye, EyeOff, Zap, Calendar, Copy, Check, Loader2, Download, RefreshCw,
  AlertTriangle, FileDown, X, Save, Settings as SettingsIcon, Info,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { settingsApi, adminIntegrationApi } from '../../services/api/settings';
import { IntegrationStatusCard } from '../integrations/IntegrationStatusCard';
// New service modules must be imported DIRECTLY (not via the api barrel) -
// TS2614 issue with re-exported types from newly-added services.
import { getIntegrationStatus, type IntegrationStatusReport } from '../../services/api/integrations';

interface Integration {
  id: string;
  name: string;
  description: string;
  icon: string;
  connected: boolean;
  /** Existing config returned by backend GET (sensitive fields masked) */
  config?: Record<string, unknown>;
  lastSync?: string;
  testable: boolean;
}

// Default integration definitions (metadata only).
// id values match the {type:<lower>} key the backend stores under in Mongo.
const DEFAULT_INTEGRATIONS: Integration[] = [
  { id: 'razorpay', name: 'Razorpay', description: 'Payment gateway for credit/debit cards', icon: 'PAY', connected: false, testable: true },
  { id: 'whatsapp', name: 'WhatsApp Business', description: 'Send customer notifications and updates (MSG91)', icon: 'MSG', connected: false, testable: true },
  { id: 'tally', name: 'Tally ERP9', description: 'Synchronize inventory and accounts', icon: 'ERP', connected: false, testable: true },
  { id: 'shopify', name: 'Shopify', description: 'Sync products and orders', icon: 'SHOP', connected: false, testable: true },
  { id: 'shiprocket', name: 'Shiprocket', description: 'Manage shipping and logistics', icon: 'SHIP', connected: false, testable: true },
  { id: 'gst-portal', name: 'GST Portal', description: 'File GST returns and compliance', icon: 'GST', connected: false, testable: false },
];

// ----------------------------------------------------------------------------
// Per-integration field templates
// ----------------------------------------------------------------------------
// Field names match what nexus_providers.py / services/shiprocket.py /
// providers.py actually read from the integrations collection.
// `password` fields use type="password" with a show/hide toggle.

type FieldKind = 'text' | 'password' | 'number';

interface FieldDef {
  key: string;
  label: string;
  kind: FieldKind;
  placeholder?: string;
  defaultValue?: string | number;
  optional?: boolean;
  help?: string;
}

interface IntegrationSchema {
  fields: FieldDef[];
  /** id in /jarvis/integrations/status - used to surface env-present callout */
  statusId?: string;
  /** Optional banner text shown at the top of the modal body */
  banner?: { kind: 'info' | 'warn'; text: string };
  /** Hide save buttons entirely (e.g. gst-portal - informational only) */
  readOnly?: boolean;
}

const INTEGRATION_SCHEMAS: Record<string, IntegrationSchema> = {
  razorpay: {
    fields: [
      { key: 'key_id', label: 'Key ID', kind: 'text', placeholder: 'rzp_live_xxxxxxxxxxxx' },
      { key: 'key_secret', label: 'Key Secret', kind: 'password', placeholder: 'Razorpay key secret' },
      { key: 'webhook_secret', label: 'Webhook Secret (optional)', kind: 'password', optional: true, placeholder: 'For verifying webhook signatures' },
    ],
  },
  whatsapp: {
    statusId: 'msg91_whatsapp',
    fields: [
      { key: 'api_key', label: 'MSG91 API Key', kind: 'password', placeholder: 'Your MSG91 auth key' },
      { key: 'integrated_number', label: 'Integrated Number', kind: 'text', placeholder: 'WhatsApp business number from MSG91' },
      { key: 'namespace', label: 'WhatsApp Namespace', kind: 'text', placeholder: 'Per-template namespace from MSG91' },
      { key: 'sms_template_id', label: 'SMS Template ID', kind: 'text', placeholder: 'DLT-approved template id' },
      { key: 'sender_id', label: 'SMS Sender ID', kind: 'text', defaultValue: 'BVOPTL', placeholder: 'DLT-registered 6-char sender' },
    ],
    banner: {
      kind: 'info',
      text: 'Maps to MSG91 (WhatsApp + SMS). Railway env vars (MSG91_*) also work - if set, the env values take precedence over what you save here.',
    },
  },
  tally: {
    fields: [
      { key: 'server_url', label: 'Tally Server URL', kind: 'text', placeholder: 'http://localhost:9000' },
      { key: 'company_name', label: 'Company Name', kind: 'text', placeholder: 'As it appears in Tally' },
      { key: 'sync_interval', label: 'Sync Interval (seconds)', kind: 'number', defaultValue: 3600 },
    ],
    banner: {
      kind: 'info',
      text: 'Tally integration is export-only today (per-store voucher XML downloads below). Live push is not yet wired - these fields are saved for when it lands.',
    },
  },
  shopify: {
    fields: [
      { key: 'shop_url', label: 'Shop URL', kind: 'text', placeholder: 'mystore.myshopify.com' },
      { key: 'api_key', label: 'API Key', kind: 'text', placeholder: 'Shopify app API key' },
      { key: 'api_secret', label: 'API Secret', kind: 'password', placeholder: 'Shopify app API secret' },
      { key: 'access_token', label: 'Access Token', kind: 'password', placeholder: 'Shopify admin API access token' },
    ],
    banner: {
      kind: 'warn',
      text: 'Shopify via NEXUS is currently dormant - the bettervision-inventory (BVI) app now owns Shopify writes. Saving values here is for future re-activation only.',
    },
  },
  shiprocket: {
    statusId: 'shiprocket',
    fields: [
      { key: 'email', label: 'Shiprocket Login Email', kind: 'text', placeholder: 'ops@bettervision.in' },
      { key: 'password', label: 'Shiprocket Password', kind: 'password', placeholder: 'Shiprocket account password' },
      { key: 'pickup_postcode', label: 'Default Pickup Postcode (optional)', kind: 'text', optional: true, placeholder: 'e.g. 827006 (Bokaro)' },
    ],
    banner: {
      kind: 'info',
      text: 'Railway env vars (SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD) also work - if set, the env values take precedence over what you save here. Bookings only go live when DISPATCH_MODE=live.',
    },
  },
  'gst-portal': {
    fields: [],
    readOnly: true,
    banner: {
      kind: 'info',
      text: 'GST filing already works end-to-end via the offline-tool workflow: download GSTR-1 / GSTR-3B JSON from Reports -> GST, import on gst.gov.in -> Returns -> Offline Tool. A GSP integration (one-click portal push from inside IMS) only becomes necessary once any single legal entity crosses the Rs 5 Cr aggregate-turnover e-invoicing mandate. Until then, the manual JSON workflow is the standard practice for small + mid Indian businesses and is the supported path here.',
    },
  },
};

const SENSITIVE_FIELD_NAMES = new Set([
  'api_key', 'api_secret', 'secret_key', 'key_secret', 'secret', 'password',
  'token', 'access_token', 'refresh_token', 'private_key', 'webhook_secret',
]);

// A value the backend returns is "masked" if it looks like the _mask_value
// output: "abcd****..**xy" - i.e. contains stars and is non-empty.
function looksMasked(v: unknown): boolean {
  if (typeof v !== 'string') return false;
  return v.includes('*') && v.length >= 4;
}

export function IntegrationSettings() {
  const [integrations, setIntegrations] = useState<Integration[]>(DEFAULT_INTEGRATIONS);
  const [isLoading, setIsLoading] = useState(true);
  const [showApiKey, setShowApiKey] = useState<Record<string, boolean>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [configuringId, setConfiguringId] = useState<string | null>(null);
  const [statusReport, setStatusReport] = useState<IntegrationStatusReport | null>(null);
  // Bumping this remounts <IntegrationStatusCard /> so it re-fetches after a save.
  const [statusKey, setStatusKey] = useState(0);
  const toast = useToast();

  // Load integration configs from API
  const loadIntegrations = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await settingsApi.getIntegrations();
      const apiIntegrations: Array<Record<string, unknown>> = response?.integrations || [];
      const merged = DEFAULT_INTEGRATIONS.map(def => {
        const apiData = apiIntegrations.find(
          (a) => String((a as { type?: string; id?: string }).type ?? (a as { id?: string }).id ?? '').toLowerCase() === def.id
        );
        if (apiData) {
          const cfg = (apiData.config && typeof apiData.config === 'object')
            ? apiData.config as Record<string, unknown>
            : undefined;
          return {
            ...def,
            connected: Boolean(apiData.enabled ?? apiData.connected ?? def.connected),
            config: cfg,
            lastSync: (apiData.last_sync as string) || (apiData.lastSync as string) || def.lastSync,
          };
        }
        return def;
      });
      setIntegrations(merged);
    } catch {
      // Fall back to defaults - never log secrets in the error path
      setIntegrations(DEFAULT_INTEGRATIONS);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadStatusReport = useCallback(async () => {
    try {
      const report = await getIntegrationStatus();
      setStatusReport(report);
    } catch {
      setStatusReport(null);
    }
  }, []);

  useEffect(() => {
    loadIntegrations();
    loadStatusReport();
  }, [loadIntegrations, loadStatusReport]);

  const handleCopyApiKey = (id: string, apiKey: string | undefined) => {
    if (!apiKey) return;
    navigator.clipboard.writeText(apiKey);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleTestConnection = async (id: string) => {
    setTestingId(id);
    try {
      const result = await settingsApi.testIntegration(id);
      if (result?.success === false) {
        toast.error(result?.message ?? `Connection test failed for ${id}`);
      } else {
        toast.success(result?.message ?? `Connection to ${id} is working`);
      }
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        `Failed to test ${id} connection`;
      toast.error(message);
    } finally {
      setTestingId(null);
    }
  };

  const handleSaved = useCallback(async () => {
    // Reload list + bump key so IntegrationStatusCard re-fetches
    await loadIntegrations();
    await loadStatusReport();
    setStatusKey(k => k + 1);
  }, [loadIntegrations, loadStatusReport]);

  const formatLastSync = (dateString: string | undefined) => {
    if (!dateString) return 'Never synced';
    const date = new Date(dateString);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return `${days}d ago`;
  };

  const activeIntegration = configuringId
    ? integrations.find(i => i.id === configuringId) ?? null
    : null;

  return (
    <div className="space-y-6">
      {/* Read-only, SUPERADMIN-only honest status (renders null otherwise) */}
      <IntegrationStatusCard key={statusKey} />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {integrations.map(integration => {
          // Derive a primary key display value for the card surface (masked).
          const primaryKey = pickPrimaryKey(integration);
          return (
            <div
              key={integration.id}
              className={clsx(
                'p-6 rounded-lg border-2 transition',
                integration.connected
                  ? 'bg-white border-green-600 border-opacity-30'
                  : 'bg-white border-gray-200'
              )}
            >
              {/* Header */}
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-start gap-3">
                  <span className="text-3xl">{integration.icon}</span>
                  <div>
                    <h3 className="font-semibold text-gray-900">{integration.name}</h3>
                    <p className="text-sm text-gray-500">{integration.description}</p>
                  </div>
                </div>
                <span className={clsx(
                  'px-3 py-1 rounded-full text-xs font-medium',
                  integration.connected
                    ? 'bg-green-50 text-green-700'
                    : 'bg-gray-100 text-gray-700'
                )}>
                  {integration.connected ? 'Connected' : 'Not Connected'}
                </span>
              </div>

              {/* Existing primary-key display (read-only, masked by backend) */}
              {primaryKey && (
                <div className="mb-4">
                  <label className="block text-xs font-medium text-gray-500 mb-2">
                    {primaryKey.label}
                  </label>
                  <div className="flex gap-2">
                    <input
                      type={showApiKey[integration.id] ? 'text' : 'password'}
                      value={primaryKey.value}
                      readOnly
                      className="flex-1 px-3 py-2 bg-gray-100 border border-gray-300 rounded text-sm text-gray-700 font-mono"
                    />
                    <button
                      onClick={() => setShowApiKey(prev => ({ ...prev, [integration.id]: !prev[integration.id] }))}
                      className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-500 transition"
                      title={showApiKey[integration.id] ? 'Hide' : 'Show'}
                    >
                      {showApiKey[integration.id] ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => handleCopyApiKey(integration.id, primaryKey.value)}
                      className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-500 transition"
                      title="Copy"
                    >
                      {copiedId === integration.id ? (
                        <Check className="w-4 h-4 text-green-500" />
                      ) : (
                        <Copy className="w-4 h-4" />
                      )}
                    </button>
                  </div>
                  <p className="text-[11px] text-gray-400 mt-1">
                    Stored value (masked). Click Configure to replace.
                  </p>
                </div>
              )}

              {/* Last Sync Info */}
              {integration.lastSync && (
                <div className="mb-4 flex items-center gap-2 text-xs text-gray-500">
                  <Calendar className="w-3 h-3" />
                  <span>Last sync: {formatLastSync(integration.lastSync)}</span>
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2">
                {integration.testable && (
                  <button
                    onClick={() => handleTestConnection(integration.id)}
                    disabled={testingId === integration.id}
                    className={clsx(
                      'flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium transition',
                      testingId === integration.id
                        ? 'bg-gray-100 text-gray-500 cursor-not-allowed'
                        : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
                    )}
                  >
                    {testingId === integration.id ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Zap className="w-4 h-4" />
                    )}
                    {testingId === integration.id ? 'Testing...' : 'Test Connection'}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setConfiguringId(integration.id)}
                  className="flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium bg-bv text-white hover:bg-bv-600 transition"
                >
                  <SettingsIcon className="w-4 h-4" />
                  Configure
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Tally per-store voucher export panel (Phase I-6). */}
      <TallyExportsPanel />

      {/* Info box */}
      <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700">
        <p>
          <strong>API Keys:</strong> Keep your API keys secure. Never share them publicly. The
          backend masks stored secrets when displayed - re-enter a value to replace it. Contact
          support if you need to rotate your keys.
        </p>
      </div>

      {/* Loading hint while initial fetch in flight */}
      {isLoading && (
        <div className="text-xs text-gray-400 text-center">Loading integrations...</div>
      )}

      {/* Configure modal */}
      {activeIntegration && (
        <ConfigureIntegrationModal
          integration={activeIntegration}
          statusReport={statusReport}
          onClose={() => setConfiguringId(null)}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Configure modal
// ----------------------------------------------------------------------------

interface ConfigureModalProps {
  integration: Integration;
  statusReport: IntegrationStatusReport | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}

function ConfigureIntegrationModal({
  integration, statusReport, onClose, onSaved,
}: ConfigureModalProps) {
  const toast = useToast();
  const schema = INTEGRATION_SCHEMAS[integration.id];
  const [enabled, setEnabled] = useState<boolean>(integration.connected);
  const [form, setForm] = useState<Record<string, string>>({});
  const [showField, setShowField] = useState<Record<string, boolean>>({});
  const [isSaving, setIsSaving] = useState(false);

  // Initialize form. We do NOT pre-fill sensitive fields with their masked
  // backend value - that would round-trip the masked string back as a "new"
  // value on save and corrupt the stored secret. Non-sensitive fields are
  // pre-filled for editing.
  useEffect(() => {
    if (!schema) return;
    const initial: Record<string, string> = {};
    const existing = integration.config ?? {};
    for (const field of schema.fields) {
      const stored = existing[field.key];
      const isSensitive = field.kind === 'password' || SENSITIVE_FIELD_NAMES.has(field.key);
      if (isSensitive) {
        // Always start blank for sensitive fields; placeholder messaging
        // tells the user a value is stored when looksMasked(stored) is true.
        initial[field.key] = '';
      } else if (stored != null && stored !== '') {
        initial[field.key] = String(stored);
      } else if (field.defaultValue != null) {
        initial[field.key] = String(field.defaultValue);
      } else {
        initial[field.key] = '';
      }
    }
    setForm(initial);
  }, [integration, schema]);

  if (!schema) {
    // Defensive - unknown integration id, render a minimal placeholder
    return (
      <ModalShell title={`Configure ${integration.name}`} onClose={onClose}>
        <div className="text-sm text-gray-500">
          No configuration schema defined for this integration.
        </div>
      </ModalShell>
    );
  }

  // Look up env-present hints for this integration from the status report.
  const statusItem = schema.statusId
    ? statusReport?.integrations.find(i => i.id === schema.statusId)
    : undefined;
  const envPresentKeys = (statusItem?.env_keys ?? []).filter(k => k.present);

  const update = (key: string, value: string) => {
    setForm(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    if (schema.readOnly) {
      onClose();
      return;
    }

    setIsSaving(true);
    try {
      // Only include fields the user actually filled (preserve existing values
      // for blanks). Number fields coerced to Number.
      const config: Record<string, unknown> = {};
      for (const field of schema.fields) {
        const raw = form[field.key];
        if (raw == null || raw === '') continue;
        if (field.kind === 'number') {
          const n = Number(raw);
          if (Number.isFinite(n)) config[field.key] = n;
        } else {
          config[field.key] = raw.trim();
        }
      }

      const payload = {
        integration_type: integration.id,
        enabled,
        config,
      };

      await settingsApi.updateIntegration(integration.id, payload);
      // Never include any config values in the toast - name only.
      toast.success(`${integration.name} configuration saved`);
      await onSaved();
      onClose();
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        `Failed to save ${integration.name} configuration`;
      // eslint-disable-next-line no-console
      console.error('[integration-save] failed for', integration.id);
      // ^ Never include the form contents (which may contain a new secret)
      toast.error(typeof message === 'string' ? message : 'Save failed');
    } finally {
      setIsSaving(false);
    }
  };

  const bannerKindCls = (kind: 'info' | 'warn') =>
    kind === 'warn'
      ? 'bg-amber-50 border-amber-200 text-amber-800'
      : 'bg-blue-50 border-blue-200 text-blue-800';

  return (
    <ModalShell title={`Configure ${integration.name}`} onClose={onClose}>
      {/* Banner */}
      {schema.banner && (
        <div className={clsx('flex items-start gap-2 px-3 py-2 rounded border text-xs', bannerKindCls(schema.banner.kind))}>
          <Info className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{schema.banner.text}</span>
        </div>
      )}

      {/* Env-present callout (Railway env vars already set) */}
      {envPresentKeys.length > 0 && (
        <div className="flex items-start gap-2 px-3 py-2 rounded border bg-green-50 border-green-200 text-green-800 text-xs">
          <Check className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            Already configured via Railway env vars:{' '}
            <span className="font-mono">{envPresentKeys.map(k => k.key).join(', ')}</span>
            . Values entered here are saved to the DB and used as a fallback when env vars are missing.
          </span>
        </div>
      )}

      {/* Enabled toggle (only when there's something to save) */}
      {!schema.readOnly && (
        <label className="flex items-center justify-between px-3 py-2 rounded border border-gray-200 bg-gray-50">
          <div>
            <div className="text-sm font-medium text-gray-900">Enabled</div>
            <div className="text-xs text-gray-500">
              Stage credentials and flip this on when you're ready to go live.
            </div>
          </div>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="w-5 h-5 accent-bv"
          />
        </label>
      )}

      {/* Field list */}
      {schema.fields.length > 0 && (
        <div className="space-y-3">
          {schema.fields.map(field => {
            const isPassword = field.kind === 'password';
            const isSensitive = isPassword || SENSITIVE_FIELD_NAMES.has(field.key);
            const stored = integration.config?.[field.key];
            const hasStored = isSensitive
              ? looksMasked(stored)
              : (stored != null && stored !== '');
            const showVal = !!showField[field.key];
            return (
              <div key={field.key}>
                <label className="text-xs font-medium text-gray-700 block mb-1">
                  {field.label}
                  {field.optional && (
                    <span className="text-gray-400 font-normal"> (optional)</span>
                  )}
                </label>
                <div className="flex gap-2">
                  <input
                    type={isPassword && !showVal ? 'password' : field.kind === 'number' ? 'number' : 'text'}
                    value={form[field.key] ?? ''}
                    onChange={(e) => update(field.key, e.target.value)}
                    placeholder={
                      hasStored && isSensitive
                        ? 'Configured - type a new value to replace'
                        : field.placeholder
                    }
                    min={field.kind === 'number' ? 1 : undefined}
                    autoComplete={isPassword ? 'new-password' : 'off'}
                    spellCheck={false}
                    className="flex-1 px-3 py-2 border border-gray-300 rounded text-sm text-gray-900 font-mono"
                  />
                  {isPassword && (
                    <button
                      type="button"
                      onClick={() => setShowField(prev => ({ ...prev, [field.key]: !prev[field.key] }))}
                      className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-500 transition"
                      title={showVal ? 'Hide' : 'Show'}
                    >
                      {showVal ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  )}
                </div>
                {field.help && (
                  <p className="text-[11px] text-gray-400 mt-1">{field.help}</p>
                )}
                {hasStored && isSensitive && !form[field.key] && (
                  <p className="text-[11px] text-gray-400 mt-1">
                    A value is currently stored (shown masked). Leave blank to keep it.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-end gap-2 pt-4 border-t border-gray-200">
        <button
          type="button"
          onClick={onClose}
          disabled={isSaving}
          className="px-4 py-2 rounded text-sm font-medium bg-gray-100 hover:bg-gray-200 text-gray-700 transition disabled:opacity-50"
        >
          {schema.readOnly ? 'Close' : 'Cancel'}
        </button>
        {!schema.readOnly && (
          <button
            type="button"
            onClick={handleSave}
            disabled={isSaving}
            className={clsx(
              'flex items-center gap-2 px-4 py-2 rounded text-sm font-medium text-white transition',
              isSaving ? 'bg-bv/60 cursor-not-allowed' : 'bg-bv hover:bg-bv-600'
            )}
          >
            {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {isSaving ? 'Saving...' : 'Save'}
          </button>
        )}
      </div>
    </ModalShell>
  );
}

function ModalShell({
  title, onClose, children,
}: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-xl max-h-[90vh] overflow-hidden flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 hover:bg-gray-100 rounded text-gray-500"
            title="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {children}
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

/** Choose the most representative key for the card surface display. */
function pickPrimaryKey(integration: Integration): { label: string; value: string } | null {
  const schema = INTEGRATION_SCHEMAS[integration.id];
  if (!schema) return null;
  const cfg = integration.config;
  if (!cfg || typeof cfg !== 'object') return null;
  // First field with a stored value wins (excluding numbers - usually intervals)
  for (const field of schema.fields) {
    if (field.kind === 'number') continue;
    const v = cfg[field.key];
    if (typeof v === 'string' && v) {
      return { label: field.label, value: v };
    }
  }
  return null;
}

// ============================================================================
// Tally per-store export panel
// ============================================================================

interface TallyExportRow {
  store_id: string;
  store_code: string;
  store_name: string;
  voucher_count: number;
  balanced: boolean;
  balance_check?: { ok: boolean; mismatch_count: number; batch_delta: number };
  generated_at: string;
}

function todayIso(): string {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function TallyExportsPanel() {
  const toast = useToast();
  const [date, setDate] = useState<string>(todayIso());
  const [rows, setRows] = useState<TallyExportRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [regenAll, setRegenAll] = useState(false);
  const [regenStoreId, setRegenStoreId] = useState<string | null>(null);

  const load = useCallback(async (selectedDate: string) => {
    setLoading(true);
    try {
      const resp = await adminIntegrationApi.listTallyExports(selectedDate);
      setRows(resp.exports || []);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(date);
  }, [date, load]);

  const handleDownload = async (storeId: string, storeCode: string) => {
    setDownloadingId(storeId);
    try {
      await adminIntegrationApi.downloadTallyVoucherXml(date, storeId);
      toast.success(`Downloaded voucher XML for ${storeCode}`);
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        `Failed to download voucher for ${storeCode}`;
      toast.error(message);
    } finally {
      setDownloadingId(null);
    }
  };

  const handleRegenerate = async (storeId?: string) => {
    if (storeId) setRegenStoreId(storeId);
    else setRegenAll(true);
    try {
      const result = await adminIntegrationApi.regenerateTallyExport(date, storeId);
      if (result.ok) {
        toast.success(result.notes || 'Regenerated');
        await load(date);
      } else {
        toast.error(result.error || result.notes || 'Regenerate failed');
      }
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to regenerate';
      toast.error(message);
    } finally {
      setRegenAll(false);
      setRegenStoreId(null);
    }
  };

  return (
    <div className="bg-white rounded-lg border-2 border-gray-200 p-6">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div>
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <FileDown className="w-4 h-4 text-bv-red-600" />
            Tally voucher exports — per store
          </h3>
          <p className="text-sm text-gray-500 mt-0.5">
            One XML per active store. Import each in the matching Tally Company on your RDP machine.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="tally-export-date" className="text-xs font-medium text-gray-500">
            Date
          </label>
          <input
            id="tally-export-date"
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded text-sm"
          />
          <button
            type="button"
            onClick={() => handleRegenerate()}
            disabled={regenAll || loading}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition',
              regenAll || loading
                ? 'bg-gray-100 text-gray-500 cursor-not-allowed'
                : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
            )}
            title="Re-run the export for every active store on this date"
          >
            {regenAll ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5" />
            )}
            Regenerate all
          </button>
        </div>
      </div>

      {loading && rows.length === 0 ? (
        <div className="flex items-center justify-center py-8 text-gray-500">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      ) : rows.length === 0 ? (
        <div className="text-center py-8 text-gray-500 text-sm">
          No exports for {date}. Either the day's nightly tick hasn't run yet, or no
          stores had qualifying orders. Use "Regenerate all" above to run on demand.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                <th className="py-2 pr-4">Store</th>
                <th className="py-2 pr-4">Vouchers</th>
                <th className="py-2 pr-4">Balanced</th>
                <th className="py-2 pr-4">Generated</th>
                <th className="py-2 pr-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.store_id} className="border-t border-gray-100">
                  <td className="py-2 pr-4">
                    <div className="font-medium text-gray-900">{row.store_name}</div>
                    <div className="text-xs font-mono text-gray-500">{row.store_code}</div>
                  </td>
                  <td className="py-2 pr-4 font-mono">{row.voucher_count}</td>
                  <td className="py-2 pr-4">
                    {row.balanced ? (
                      <span className="inline-flex items-center gap-1 text-green-700 text-xs font-medium">
                        <Check className="w-3.5 h-3.5" /> ok
                      </span>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1 text-amber-700 text-xs font-medium"
                        title={
                          row.balance_check
                            ? `${row.balance_check.mismatch_count} mismatched, batch delta ${row.balance_check.batch_delta}`
                            : 'Failed validation'
                        }
                      >
                        <AlertTriangle className="w-3.5 h-3.5" />
                        unbalanced
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-xs text-gray-500">
                    {new Date(row.generated_at).toLocaleString('en-IN', {
                      day: '2-digit',
                      month: 'short',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                  <td className="py-2 pr-2 text-right">
                    <div className="inline-flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => handleRegenerate(row.store_id)}
                        disabled={regenStoreId === row.store_id}
                        className="p-1.5 rounded hover:bg-gray-100 text-gray-600 transition disabled:opacity-50"
                        title="Regenerate just this store"
                      >
                        {regenStoreId === row.store_id ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="w-3.5 h-3.5" />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDownload(row.store_id, row.store_code)}
                        disabled={downloadingId === row.store_id}
                        className={clsx(
                          'flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition',
                          downloadingId === row.store_id
                            ? 'bg-gray-100 text-gray-500 cursor-not-allowed'
                            : row.balanced
                              ? 'bg-bv-red-600 text-white hover:bg-bv-red-700'
                              : 'bg-amber-600 text-white hover:bg-amber-700'
                        )}
                      >
                        {downloadingId === row.store_id ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Download className="w-3.5 h-3.5" />
                        )}
                        Download
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
