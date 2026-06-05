// ============================================================================
// IMS 2.0 - Unified SUPERADMIN Integrations Hub
// ============================================================================
// Renders the full catalog of every integration (fetched from the backend)
// grouped by category. Each card shows configured/not-configured status,
// all fields with masked secrets, and a Save button.
//
// This supersedes the hardcoded DEFAULT_INTEGRATIONS list in IntegrationSettings.
// The legacy IntegrationSettings component (Tally export panel + status card)
// is still rendered below the hub as a supplementary panel.
//
// Security contract (mirrors the backend):
//   - Sensitive fields start blank; placeholder text tells the user a value is
//     stored (shows "Configured - type to replace").
//   - Saved values are NEVER shown in toasts, console logs, or copied to URLs.
//   - Non-sensitive fields are pre-filled from the backend masked response for
//     editing convenience.

import React, { useState, useEffect, useCallback } from 'react';
import {
  Save, Eye, EyeOff, Loader2, CheckCircle, Circle, Info, RefreshCw,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api/settings';
import type { IntegrationCatalogEntry, IntegrationFieldDef } from '../../services/api/settings';
import { IntegrationSettings } from './IntegrationSettings';

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

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  const handleSaved = useCallback(async () => {
    await loadCatalog();
    setConfiguringType(null);
  }, [loadCatalog]);

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
              const handleConfigure = (): void => { setConfiguringType(entry.type); };
              return (
                <IntegrationCard
                  key={entry.type}
                  entry={entry}
                  stored={s}
                  onConfigure={handleConfigure}
                />
              );
            })}
          </div>
        </section>
      ))}

      {/* Legacy Tally export panel + status card (supplementary) */}
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
  onConfigure: () => void;
}

function IntegrationCard({ entry, stored, onConfigure }: CardProps) {
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
        {configured ? (
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
      {configured && !previewField && (
        <div className="text-xs text-gray-400 italic">Credentials stored (masked)</div>
      )}

      {/* Configure button */}
      <button
        type="button"
        onClick={onConfigure}
        className="mt-auto w-full text-sm font-medium px-3 py-1.5 rounded bg-bv text-white hover:bg-bv-600 transition"
      >
        Configure
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Configure modal
// ---------------------------------------------------------------------------

interface ModalProps {
  entry: IntegrationCatalogEntry;
  stored: StoredIntegration;
  onClose: () => void;
  onSaved: () => Promise<void>;
  toast: ReturnType<typeof useToast>;
}

function ConfigureModal({ entry, stored, onClose, onSaved, toast }: ModalProps) {
  const [enabled, setEnabled] = useState(stored.is_enabled);
  const [form, setForm] = useState<Record<string, string>>({});
  const [showField, setShowField] = useState<Record<string, boolean>>({});
  const [isSaving, setIsSaving] = useState(false);

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

  const update = (key: string, value: string) =>
    setForm((prev: Record<string, string>) => ({ ...prev, [key]: value }));

  const handleSave = async () => {
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

          {/* Enabled toggle */}
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

          {/* Fields */}
          {entry.fields.length === 0 && (
            <div className="flex items-start gap-2 text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded px-3 py-2">
              <Info className="w-4 h-4 mt-0.5 shrink-0" />
              <span>No credentials required for this integration.</span>
            </div>
          )}

          {entry.fields.map((field: IntegrationFieldDef) => {
            const handleToggle = (): void => {
              setShowField((prev: Record<string, boolean>) => ({ ...prev, [field.key]: !prev[field.key] }));
            };
            const handleChange = (v: string): void => { update(field.key, v); };
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
              />
            );
          })}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-gray-200">
          <button
            type="button"
            onClick={onClose}
            disabled={isSaving}
            className="px-4 py-2 rounded text-sm font-medium bg-gray-100 hover:bg-gray-200 text-gray-700 transition disabled:opacity-50"
          >
            Cancel
          </button>
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
}

function FieldRow({ field, value, hasStored, showValue, onToggleShow, onChange }: FieldRowProps) {
  return (
    <div>
      <label className="text-xs font-medium text-gray-700 block mb-1">
        {field.label}
        {field.optional && (
          <span className="text-gray-400 font-normal ml-1">(optional)</span>
        )}
      </label>
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
