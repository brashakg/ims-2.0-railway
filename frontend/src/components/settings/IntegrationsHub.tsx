// ============================================================================
// IMS 2.0 - Unified Integrations Hub
// ============================================================================
// Renders the full catalog of every integration (fetched from the backend)
// grouped by category. Each card shows configured/not-configured status,
// all fields with masked secrets, a Test Connection button, and a Save button.
//
// This is the SINGLE integration card grid. The legacy IntegrationSettings
// component below it is now a pure supplementary panel (Tally per-store export
// table + the SUPERADMIN-only read-only status card) -- its duplicate hardcoded
// 6-card grid was removed when this hub absorbed Test Connection + the
// per-integration banners + the env-present callout + read-only handling.
//
// Security contract (mirrors the backend):
//   - Sensitive fields start blank; placeholder text tells the user a value is
//     stored (shows "Configured - type to replace").
//   - Saved values are NEVER shown in toasts, console logs, or copied to URLs.
//   - Non-sensitive fields are pre-filled from the backend masked response for
//     editing convenience.
//   - Test Connection reports configured/dispatch_mode honestly; it treats a
//     result as a PASS only when status === 'configured' || live === true
//     (NOT result.success -- there is no such key; a naive truthy check was the
//     old placebo-success bug).

import React, { useState, useEffect, useCallback } from 'react';
import {
  Save, Eye, EyeOff, Loader2, CheckCircle, Circle, Info, RefreshCw, Zap, Check,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api/settings';
import type { IntegrationCatalogEntry, IntegrationFieldDef } from '../../services/api/settings';
import { IntegrationSettings } from './IntegrationSettings';
// New service modules must be imported DIRECTLY (not via the api barrel) -
// TS2614 issue with re-exported types from newly-added services.
import {
  getIntegrationStatus,
  getAnthropicModels,
  type IntegrationStatusReport,
  type AnthropicModel,
} from '../../services/api/integrations';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StoredIntegration {
  type: string;
  is_configured: boolean;
  is_enabled: boolean;
  config: Record<string, unknown>;
}

// A value the backend returns is masked if it contains stars and is non-empty.
function looksMasked(v: unknown): boolean {
  if (typeof v !== 'string') return false;
  return v.includes('*') && v.length >= 4;
}

// ---------------------------------------------------------------------------
// Per-integration UI metadata (FRONTEND lookup, keyed by catalog `type`).
// The backend catalog supplies the fields; this map adds the human context:
//   banner    -- contextual note shown at the top of the configure modal
//   statusId  -- id in GET /jarvis/integrations/status, used to surface the
//                "already set via Railway env vars" callout
//   readOnly  -- nothing to save (informational only) -> hide Save, show "Close"
// Ported verbatim from the legacy IntegrationSettings INTEGRATION_SCHEMAS so no
// help/banner text is lost in the merge.
// ---------------------------------------------------------------------------

interface IntegrationMeta {
  banner?: { kind: 'info' | 'warn'; text: string };
  statusId?: string;
  readOnly?: boolean;
}

const INTEGRATION_META: Record<string, IntegrationMeta> = {
  tally: {
    banner: {
      kind: 'info',
      text: 'Tally integration is export-only today (per-store voucher XML downloads in the supplementary panel below). Live push is not yet wired - these fields are saved for when it lands.',
    },
  },
  shopify: {
    banner: {
      kind: 'info',
      text: 'IMS owns Shopify writes directly (the separate BVI app was retired 2026-07-20). Publishing posture and go-live controls live in Online Store -> Shopify Sync.',
    },
  },
  shiprocket: {
    statusId: 'shiprocket',
    banner: {
      kind: 'info',
      text: 'Railway env vars (SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD) also work - if set, the env values take precedence over what you save here. Bookings only go live when DISPATCH_MODE=live.',
    },
  },
  whatsapp: {
    statusId: 'msg91_whatsapp',
    banner: {
      kind: 'info',
      text: 'Maps to MSG91 (WhatsApp + SMS). Railway env vars (MSG91_*) also work - if set, the env values take precedence over what you save here.',
    },
  },
  'gst-portal': {
    readOnly: true,
    banner: {
      kind: 'info',
      text: 'GST filing already works end-to-end via the offline-tool workflow: download GSTR-1 / GSTR-3B JSON from Reports -> GST, import on gst.gov.in -> Returns -> Offline Tool. A GSP integration (one-click portal push from inside IMS) only becomes necessary once any single legal entity crosses the Rs 5 Cr aggregate-turnover e-invoicing mandate. Until then, the manual JSON workflow is the standard practice for small + mid Indian businesses and is the supported path here.',
    },
  },
  myluxottica: {
    banner: {
      kind: 'info',
      text: "Dealer portals often use SSO/2FA a server login can't complete; after saving, Autopilot will try it and we'll confirm it actually pulls data. Railway env vars (MYLUXOTTICA_USER / MYLUXOTTICA_PASS) also work as a fallback.",
    },
  },
  web_search: {
    banner: {
      kind: 'info',
      text: 'Create a Programmable Search Engine at programmablesearchengine.google.com (that gives you the cx / Search Engine ID) and an API key in Google Cloud with the Custom Search JSON API enabled. Railway env vars (GOOGLE_CSE_KEY / GOOGLE_CSE_CX, or SERP_API_KEY) also work as a fallback.',
    },
  },
};

function metaFor(type: string): IntegrationMeta {
  return INTEGRATION_META[type] ?? {};
}

// ---------------------------------------------------------------------------
// Category colours (bv-red accent + semantic pastels)
// ---------------------------------------------------------------------------

const CATEGORY_CHIP: Record<string, string> = {
  Commerce: 'bg-blue-50 text-blue-700 border-blue-200',
  Messaging: 'bg-purple-50 text-purple-700 border-purple-200',
  AI: 'bg-amber-50 text-amber-700 border-amber-200',
  Payments: 'bg-green-50 text-green-700 border-green-200',
  Compliance: 'bg-red-50 text-red-700 border-red-200',
  Storage: 'bg-gray-50 text-gray-700 border-gray-200',
  Ads: 'bg-orange-50 text-orange-700 border-orange-200',
};

const CATEGORY_ORDER = ['Commerce', 'Messaging', 'AI', 'Payments', 'Compliance', 'Storage', 'Ads'];

// ---------------------------------------------------------------------------
// Main hub component
// ---------------------------------------------------------------------------

export function IntegrationsHub() {
  const toast = useToast();

  const [catalog, setCatalog] = useState<IntegrationCatalogEntry[]>([]);
  const [stored, setStored] = useState<StoredIntegration[]>([]);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [configuringType, setConfiguringType] = useState<string | null>(null);
  const [testingType, setTestingType] = useState<string | null>(null);
  // Env-present hints (KEY presence only). SUPERADMIN-only endpoint; fail-soft
  // to null for ADMIN so the hub still works without it.
  const [statusReport, setStatusReport] = useState<IntegrationStatusReport | null>(null);

  const loadCatalog = useCallback(async () => {
    setLoadingCatalog(true);
    try {
      const [catRes, storedRes] = await Promise.all([
        settingsApi.getIntegrationsCatalog().catch(() => ({ catalog: [] })),
        settingsApi.getIntegrations().catch(() => ({ integrations: [] })),
      ]);
      setCatalog(catRes.catalog ?? []);
      setStored((storedRes.integrations ?? []) as StoredIntegration[]);
    } catch {
      // fail-soft: show empty state
    } finally {
      setLoadingCatalog(false);
    }
  }, []);

  const loadStatusReport = useCallback(async () => {
    try {
      const report = await getIntegrationStatus();
      setStatusReport(report);
    } catch {
      // SUPERADMIN-only endpoint; ADMIN gets 403/404 -> no env callouts (fine).
      setStatusReport(null);
    }
  }, []);

  useEffect(() => {
    loadCatalog();
    loadStatusReport();
  }, [loadCatalog, loadStatusReport]);

  const handleSaved = useCallback(async () => {
    await loadCatalog();
    await loadStatusReport();
    setConfiguringType(null);
  }, [loadCatalog, loadStatusReport]);

  // Test Connection -- ported verbatim from IntegrationSettings (POST-bugfix
  // logic). Calls POST /settings/integrations/{type}/test which returns
  //   { status: "configured" | "not_configured", live: boolean, message, ... }
  // There is NO `success` key; treat only configured/live as a pass.
  const handleTest = useCallback(async (type: string, name: string) => {
    setTestingType(type);
    try {
      const result = await settingsApi.testIntegration(type);
      const isConfigured = result?.status === 'configured' || result?.live === true;
      const detail = [
        result?.dispatch_mode ? `DISPATCH_MODE=${result.dispatch_mode}` : null,
      ].filter(Boolean).join(' · ');
      if (isConfigured) {
        toast.success(result?.message ?? `${name} is configured${detail ? ` (${detail})` : ''}`);
      } else {
        toast.error(result?.message ?? `${name} is not configured`);
      }
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        `Failed to test ${name} connection`;
      toast.error(message);
    } finally {
      setTestingType(null);
    }
  }, [toast]);

  // Group catalog by category, preserving the canonical order
  const byCategory = CATEGORY_ORDER
    .map((cat) => ({
      category: cat,
      entries: catalog.filter((e: IntegrationCatalogEntry) => e.category === cat),
    }))
    .filter((g: { category: string; entries: IntegrationCatalogEntry[] }) => g.entries.length > 0);

  const getStored = (type: string): StoredIntegration =>
    stored.find((s: StoredIntegration) => s.type === type) ?? { type, is_configured: false, is_enabled: false, config: {} };

  if (loadingCatalog) {
    return (
      <div className="flex items-center justify-center py-24 text-gray-500 gap-3">
        <RefreshCw className="w-5 h-5 animate-spin" />
        <span>Loading integrations...</span>
      </div>
    );
  }

  const activeEntry = catalog.find((e: IntegrationCatalogEntry) => e.type === configuringType) ?? null;

  return (
    <div className="space-y-8">
      {/* "Locked at HQ level" governance note (kept from the legacy panel). */}
      <div className="flex items-start gap-2 px-4 py-3 rounded-lg border border-blue-200 bg-blue-50 text-sm text-blue-800">
        <Info className="w-4 h-4 mt-0.5 shrink-0" />
        <span>
          Integration credentials are locked at the HQ level and require superadmin
          approval. The backend masks stored secrets when displayed - re-enter a value
          to replace it. Every change is recorded in the audit log.
        </span>
      </div>

      {/* Catalog sections */}
      {byCategory.map(({ category, entries }) => (
        <section key={category}>
          <div className="flex items-center gap-3 mb-4">
            <span
              className={clsx(
                'text-xs font-semibold px-2.5 py-0.5 rounded-full border',
                CATEGORY_CHIP[category] ?? 'bg-gray-50 text-gray-600 border-gray-200',
              )}
            >
              {category}
            </span>
            <span className="text-xs text-gray-400">{entries.length} integration{entries.length !== 1 ? 's' : ''}</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {entries.map((entry: IntegrationCatalogEntry) => {
              const s = getStored(entry.type);
              const meta = metaFor(entry.type);
              const handleConfigure = (): void => { setConfiguringType(entry.type); };
              const handleTestClick = (): void => { void handleTest(entry.type, entry.name); };
              return (
                <IntegrationCard
                  key={entry.type}
                  entry={entry}
                  stored={s}
                  readOnly={!!meta.readOnly}
                  testing={testingType === entry.type}
                  onConfigure={handleConfigure}
                  onTest={handleTestClick}
                />
              );
            })}
          </div>
        </section>
      ))}

      {/* Supplementary panel: Tally per-store export table + (SUPERADMIN-only)
          read-only status card. No second integration grid lives here anymore. */}
      <section>
        <div className="flex items-center gap-3 mb-4">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
            Supplementary tools
          </span>
        </div>
        <IntegrationSettings />
      </section>

      {/* Configure modal */}
      {activeEntry && (
        <ConfigureModal
          entry={activeEntry}
          stored={getStored(activeEntry.type)}
          meta={metaFor(activeEntry.type)}
          statusReport={statusReport}
          testing={testingType === activeEntry.type}
          onTest={() => { void handleTest(activeEntry.type, activeEntry.name); }}
          onClose={() => setConfiguringType(null)}
          onSaved={handleSaved}
          toast={toast}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Integration card
// ---------------------------------------------------------------------------

interface CardProps {
  entry: IntegrationCatalogEntry;
  stored: StoredIntegration;
  readOnly: boolean;
  testing: boolean;
  onConfigure: () => void;
  onTest: () => void;
}

function IntegrationCard({ entry, stored, readOnly, testing, onConfigure, onTest }: CardProps) {
  const configured = stored.is_configured;
  const enabled = stored.is_enabled;

  // Pick the first non-secret, non-empty stored value to show on the card face.
  const previewField = entry.fields.find((f) => {
    if (f.secret) return false;
    const v = stored.config[f.key];
    return typeof v === 'string' && v.length > 0 && !looksMasked(v);
  });

  return (
    <div
      className={clsx(
        'bg-white rounded-lg border-2 p-4 flex flex-col gap-3 transition',
        configured && enabled
          ? 'border-green-300'
          : configured
            ? 'border-amber-200'
            : 'border-gray-200',
      )}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-gray-900 text-sm leading-tight truncate">{entry.name}</h3>
          <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{entry.description}</p>
        </div>
        {readOnly ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 bg-blue-50 border border-blue-200 px-2 py-0.5 rounded-full shrink-0">
            <Info className="w-3 h-3" /> Info
          </span>
        ) : configured ? (
          enabled ? (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full shrink-0">
              <CheckCircle className="w-3 h-3" /> Active
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full shrink-0">
              <Circle className="w-3 h-3" /> Saved
            </span>
          )
        ) : (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 bg-gray-50 border border-gray-200 px-2 py-0.5 rounded-full shrink-0">
            <Circle className="w-3 h-3" /> Not set
          </span>
        )}
      </div>

      {/* Preview value */}
      {previewField && (
        <div className="text-xs text-gray-500 font-mono truncate bg-gray-50 rounded px-2 py-1 border border-gray-100">
          {previewField.label}: {String(stored.config[previewField.key])}
        </div>
      )}
      {configured && !previewField && !readOnly && (
        <div className="text-xs text-gray-400 italic">Credentials stored (masked)</div>
      )}

      {/* Actions */}
      <div className="mt-auto flex gap-2">
        {/* Test Connection -- not shown for read-only (nothing to test) */}
        {!readOnly && (
          <button
            type="button"
            onClick={onTest}
            disabled={testing}
            className={clsx(
              'flex-1 flex items-center justify-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded transition',
              testing
                ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                : 'bg-gray-100 hover:bg-gray-200 text-gray-700',
            )}
            title="Report whether credentials are present + the current DISPATCH_MODE"
          >
            {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
            {testing ? 'Testing...' : 'Test'}
          </button>
        )}
        <button
          type="button"
          onClick={onConfigure}
          className="flex-1 text-sm font-medium px-3 py-1.5 rounded bg-bv text-white hover:bg-bv-600 transition"
        >
          {readOnly ? 'View' : 'Configure'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Configure modal
// ---------------------------------------------------------------------------

interface ModalProps {
  entry: IntegrationCatalogEntry;
  stored: StoredIntegration;
  meta: IntegrationMeta;
  statusReport: IntegrationStatusReport | null;
  testing: boolean;
  onTest: () => void;
  onClose: () => void;
  onSaved: () => Promise<void>;
  toast: ReturnType<typeof useToast>;
}

function ConfigureModal({
  entry, stored, meta, statusReport, testing, onTest, onClose, onSaved, toast,
}: ModalProps) {
  const readOnly = !!meta.readOnly;
  const [enabled, setEnabled] = useState(stored.is_enabled);
  const [form, setForm] = useState<Record<string, string>>({});
  const [showField, setShowField] = useState<Record<string, boolean>>({});
  const [isSaving, setIsSaving] = useState(false);

  // Anthropic/Claude model dropdown: fetch the live list of currently-available
  // models so the owner picks from a dropdown instead of typing a model id that
  // may silently retire. Fail-soft -- if the fetch fails or returns empty, the
  // model field falls back to a free-text input (modelOptions stays null), so
  // the field is never unusable.
  const isAnthropic = entry.type === 'anthropic';
  const [modelOptions, setModelOptions] = useState<AnthropicModel[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);

  useEffect(() => {
    if (!isAnthropic) {
      setModelOptions(null);
      return;
    }
    let cancelled = false;
    setModelsLoading(true);
    getAnthropicModels()
      .then((res) => {
        if (cancelled) return;
        const list = res?.models ?? [];
        setModelOptions(list.length > 0 ? list : null);
      })
      .catch(() => {
        if (!cancelled) setModelOptions(null); // fall back to free-text input
      })
      .finally(() => {
        if (!cancelled) setModelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isAnthropic, entry.type]);

  // Initialise form: non-secret fields pre-filled, secret fields always blank.
  useEffect(() => {
    const init: Record<string, string> = {};
    for (const field of entry.fields) {
      if (field.secret) {
        init[field.key] = '';
      } else {
        const v = stored.config[field.key];
        init[field.key] = v != null && !looksMasked(v) ? String(v) : '';
      }
    }
    setForm(init);
  }, [entry, stored]);

  // Look up env-present hints for this integration from the status report.
  const statusItem = meta.statusId
    ? statusReport?.integrations.find((i) => i.id === meta.statusId)
    : undefined;
  const envPresentKeys = (statusItem?.env_keys ?? []).filter((k) => k.present);

  const update = (key: string, value: string) =>
    setForm((prev: Record<string, string>) => ({ ...prev, [key]: value }));

  const handleSave = async () => {
    if (readOnly) {
      onClose();
      return;
    }
    setIsSaving(true);
    try {
      const config: Record<string, unknown> = {};
      for (const field of entry.fields) {
        const raw = form[field.key];
        if (raw == null || raw.trim() === '') continue;
        config[field.key] = raw.trim();
      }
      await settingsApi.updateIntegration(entry.type, {
        integration_type: entry.type,
        enabled,
        config,
      });
      toast.success(`${entry.name} saved`);
      await onSaved();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      // Never log form contents (may contain a new secret)
      toast.error(typeof detail === 'string' ? detail : `Failed to save ${entry.name}`);
    } finally {
      setIsSaving(false);
    }
  };

  const bannerKindCls = (kind: 'info' | 'warn') =>
    kind === 'warn'
      ? 'bg-amber-50 border-amber-200 text-amber-800'
      : 'bg-blue-50 border-blue-200 text-blue-800';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-lg max-h-[90vh] overflow-hidden flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">{entry.name}</h2>
            <p className="text-xs text-gray-500 mt-0.5">{entry.description}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 hover:bg-gray-100 rounded text-gray-500 text-lg leading-none"
            title="Close"
          >
            &times;
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Category badge */}
          <span
            className={clsx(
              'inline-block text-xs font-semibold px-2.5 py-0.5 rounded-full border',
              CATEGORY_CHIP[entry.category] ?? 'bg-gray-50 text-gray-600 border-gray-200',
            )}
          >
            {entry.category}
          </span>

          {/* Per-integration banner (Tally export-only / Shopify dormant /
              Shiprocket+WhatsApp env-precedence / GST-portal read-only info). */}
          {meta.banner && (
            <div className={clsx('flex items-start gap-2 px-3 py-2 rounded border text-xs', bannerKindCls(meta.banner.kind))}>
              <Info className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{meta.banner.text}</span>
            </div>
          )}

          {/* Env-present callout (Railway env vars already set) */}
          {envPresentKeys.length > 0 && (
            <div className="flex items-start gap-2 px-3 py-2 rounded border bg-green-50 border-green-200 text-green-800 text-xs">
              <Check className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                Already configured via Railway env vars:{' '}
                <span className="font-mono">{envPresentKeys.map((k) => k.key).join(', ')}</span>
                . Values entered here are saved to the DB and used as a fallback when env vars are missing.
              </span>
            </div>
          )}

          {/* Enabled toggle (skipped for read-only integrations) */}
          {!readOnly && (
            <label className="flex items-center justify-between px-3 py-2 rounded border border-gray-200 bg-gray-50 cursor-pointer">
              <div>
                <div className="text-sm font-medium text-gray-900">Enabled</div>
                <div className="text-xs text-gray-500">
                  Stage credentials, then flip this on to activate.
                </div>
              </div>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => setEnabled(e.target.checked)}
                className="w-5 h-5 accent-bv"
              />
            </label>
          )}

          {/* Fields */}
          {!readOnly && entry.fields.length === 0 && (
            <div className="flex items-start gap-2 text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded px-3 py-2">
              <Info className="w-4 h-4 mt-0.5 shrink-0" />
              <span>No credentials required for this integration.</span>
            </div>
          )}

          {!readOnly && entry.fields.map((field: IntegrationFieldDef) => {
            const handleToggle = (): void => {
              setShowField((prev: Record<string, boolean>) => ({ ...prev, [field.key]: !prev[field.key] }));
            };
            const handleChange = (v: string): void => { update(field.key, v); };
            // For the Anthropic "model" field, offer a live dropdown of the
            // currently-available Claude models. null options -> free-text.
            const selectOptions =
              isAnthropic && field.key === 'model' ? modelOptions : null;
            return (
              <FieldRow
                key={field.key}
                field={field}
                value={form[field.key] ?? ''}
                hasStored={
                  field.secret
                    ? looksMasked(stored.config[field.key])
                    : !!(stored.config[field.key] && stored.config[field.key] !== '')
                }
                showValue={!!showField[field.key]}
                onToggleShow={handleToggle}
                onChange={handleChange}
                selectOptions={selectOptions}
                selectLoading={isAnthropic && field.key === 'model' && modelsLoading}
              />
            );
          })}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-5 py-4 border-t border-gray-200">
          {/* Test Connection inside the modal (left), not for read-only. */}
          <div>
            {!readOnly && (
              <button
                type="button"
                onClick={onTest}
                disabled={testing || isSaving}
                className={clsx(
                  'flex items-center gap-2 px-4 py-2 rounded text-sm font-medium transition',
                  testing || isSaving
                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                    : 'bg-gray-100 hover:bg-gray-200 text-gray-700',
                )}
              >
                {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                {testing ? 'Testing...' : 'Test Connection'}
              </button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isSaving}
              className="px-4 py-2 rounded text-sm font-medium bg-gray-100 hover:bg-gray-200 text-gray-700 transition disabled:opacity-50"
            >
              {readOnly ? 'Close' : 'Cancel'}
            </button>
            {!readOnly && (
              <button
                type="button"
                onClick={handleSave}
                disabled={isSaving}
                className={clsx(
                  'flex items-center gap-2 px-4 py-2 rounded text-sm font-medium text-white transition',
                  isSaving ? 'bg-bv/60 cursor-not-allowed' : 'bg-bv hover:bg-bv-600',
                )}
              >
                {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {isSaving ? 'Saving...' : 'Save'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Field row
// ---------------------------------------------------------------------------

interface FieldRowProps {
  field: IntegrationFieldDef;
  value: string;
  hasStored: boolean;
  showValue: boolean;
  onToggleShow: () => void;
  onChange: (v: string) => void;
  // When provided (Anthropic model field, live list fetched), render a
  // dropdown instead of a free-text input. null -> free-text fallback.
  selectOptions?: AnthropicModel[] | null;
  selectLoading?: boolean;
}

// Exported for unit testing the model-dropdown / free-text fallback behavior.
export function FieldRow({
  field, value, hasStored, showValue, onToggleShow, onChange, selectOptions, selectLoading,
}: FieldRowProps) {
  const useSelect = !!selectOptions && selectOptions.length > 0;

  // If the currently-saved model isn't in the live list (e.g. a retired snapshot
  // still configured), keep it selectable so we pre-select it and don't silently
  // drop the saved value.
  const optionList: AnthropicModel[] = useSelect ? [...selectOptions!] : [];
  if (useSelect && value && !optionList.some((o) => o.id === value)) {
    optionList.unshift({ id: value, display_name: `${value} (saved)` });
  }

  return (
    <div>
      <label className="text-xs font-medium text-gray-700 block mb-1">
        {field.label}
        {field.optional && (
          <span className="text-gray-400 font-normal ml-1">(optional)</span>
        )}
        {selectLoading && (
          <span className="text-gray-400 font-normal ml-2 inline-flex items-center gap-1">
            <Loader2 className="w-3 h-3 animate-spin" /> loading models...
          </span>
        )}
      </label>
      {useSelect ? (
        <select
          value={value}
          onChange={(e: React.ChangeEvent<HTMLSelectElement>) => onChange(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded text-sm text-gray-900 bg-white focus:outline-none focus:ring-1 focus:ring-bv"
        >
          {/* Allow clearing back to the backend default */}
          <option value="">Use default</option>
          {optionList.map((o) => (
            <option key={o.id} value={o.id}>
              {o.display_name}
            </option>
          ))}
        </select>
      ) : (
        <div className="flex gap-2">
          <input
            type={field.secret && !showValue ? 'password' : 'text'}
            value={value}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
            placeholder={
              hasStored && field.secret
                ? 'Configured - type a new value to replace'
                : (field.placeholder ?? '')
            }
            autoComplete={field.secret ? 'new-password' : 'off'}
            spellCheck={false}
            className="flex-1 px-3 py-2 border border-gray-300 rounded text-sm text-gray-900 font-mono focus:outline-none focus:ring-1 focus:ring-bv"
          />
          {field.secret && (
            <button
              type="button"
              onClick={onToggleShow}
              className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-500 transition"
              title={showValue ? 'Hide' : 'Show'}
            >
              {showValue ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          )}
        </div>
      )}
      {field.help && (
        <p className="text-[11px] text-gray-400 mt-1">{field.help}</p>
      )}
      {hasStored && field.secret && !value && (
        <p className="text-[11px] text-gray-400 mt-1">
          A value is stored (masked). Leave blank to keep it.
        </p>
      )}
    </div>
  );
}
