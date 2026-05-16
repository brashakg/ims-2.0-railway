// ============================================================================
// IMS 2.0 — Loyalty engine settings (SUPERADMIN only — write)
// ============================================================================
// Reads /loyalty/settings, lets the user tune the live engine, then PUTs
// the patch back. Reads work for any logged-in user; PUT is gated server-
// side at SUPERADMIN. The form mirrors DEFAULT_SETTINGS in
// backend/database/repositories/loyalty_repository.py.

import { useEffect, useState } from 'react';
import { Loader2, Save, RotateCcw, Plus, Trash2 } from 'lucide-react';
import { loyaltyApi, type LoyaltySettings } from '../../services/api/loyalty';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

// Tier thresholds + multipliers share the same key set in practice but
// are stored separately on the doc; we render them side-by-side.
const KNOWN_TIERS = ['BRONZE', 'SILVER', 'GOLD', 'PLATINUM'];
const KNOWN_CATEGORIES = ['FRAMES', 'RX_LENSES', 'CONTACT_LENSES', 'SUNGLASSES', 'WATCHES', 'ACCESSORIES'];

export function LoyaltySettingsSection() {
  const { hasRole } = useAuth();
  const toast = useToast();
  const canEdit = hasRole(['SUPERADMIN']);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [original, setOriginal] = useState<LoyaltySettings | null>(null);
  const [draft, setDraft] = useState<LoyaltySettings | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loyaltyApi
      .getSettings()
      .then((s) => {
        if (cancelled) return;
        setOriginal(s);
        setDraft(JSON.parse(JSON.stringify(s)));
      })
      .catch(() => {
        if (!cancelled) toast.error('Failed to load loyalty settings');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading || !draft) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
      </div>
    );
  }

  const dirty = JSON.stringify(original) !== JSON.stringify(draft);

  const update = <K extends keyof LoyaltySettings>(k: K, v: LoyaltySettings[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  const handleSave = async () => {
    if (!draft || !original) return;
    if (!canEdit) {
      toast.error('SUPERADMIN required');
      return;
    }
    // Send only the changed keys
    const patch: Partial<LoyaltySettings> = {};
    (Object.keys(draft) as Array<keyof LoyaltySettings>).forEach((k) => {
      if (JSON.stringify(draft[k]) !== JSON.stringify(original[k])) {
        (patch as Record<string, unknown>)[k] = draft[k];
      }
    });
    setSaving(true);
    try {
      const r = await loyaltyApi.updateSettings(patch);
      setOriginal(r);
      setDraft(JSON.parse(JSON.stringify(r)));
      toast.success('Loyalty settings updated');
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Save failed';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    if (!original) return;
    setDraft(JSON.parse(JSON.stringify(original)));
  };

  // ---- Helpers for the dynamic-key sections ----
  const setMapEntry = (
    field: 'category_multipliers' | 'tier_thresholds' | 'tier_multipliers',
    key: string,
    value: number,
  ) => {
    setDraft((d) =>
      d ? { ...d, [field]: { ...d[field], [key]: value } } : d,
    );
  };

  const removeMapEntry = (
    field: 'category_multipliers' | 'tier_thresholds' | 'tier_multipliers',
    key: string,
  ) => {
    setDraft((d) => {
      if (!d) return d;
      const next = { ...d[field] } as Record<string, number>;
      delete next[key];
      return { ...d, [field]: next };
    });
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">Loyalty engine</h2>
        <p className="text-sm text-gray-500 mt-1">
          Earn rate, expiry, redemption rules, and tier thresholds for the
          rewards programme. Changes apply on the next earn / redeem call.
        </p>
        {!canEdit && (
          <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mt-2 inline-block">
            Read-only — SUPERADMIN required to save
          </p>
        )}
      </div>

      {/* Master switch */}
      <div className="flex items-center justify-between p-4 rounded-lg border border-gray-200 bg-white">
        <div>
          <p className="font-medium text-gray-900">Programme enabled</p>
          <p className="text-xs text-gray-500">Disabling stops earn/redeem; existing balances stay intact.</p>
        </div>
        <button
          type="button"
          disabled={!canEdit}
          onClick={() => update('enabled', !draft.enabled)}
          className={`relative w-11 h-6 rounded-full transition-colors ${draft.enabled ? 'bg-bv-red-600' : 'bg-gray-300'} ${canEdit ? '' : 'opacity-60'}`}
        >
          <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${draft.enabled ? 'translate-x-5' : ''}`} />
        </button>
      </div>

      {/* Earn config */}
      <Section title="Earn rules" subtitle="How customers accumulate points on a sale">
        <NumField
          label="Points per ₹"
          help="0.01 = 1 point per ₹100 of order value"
          value={draft.points_per_rupee}
          step={0.001}
          min={0}
          disabled={!canEdit}
          onChange={(v) => update('points_per_rupee', v)}
        />
        <NumField
          label="Min order to earn (₹)"
          help="Orders below this earn no points"
          value={draft.min_order_for_earn}
          min={0}
          step={1}
          disabled={!canEdit}
          onChange={(v) => update('min_order_for_earn', v)}
        />
        <NumField
          label="Expiry (days)"
          help="0 = points never expire"
          value={draft.expiry_days}
          min={0}
          step={1}
          disabled={!canEdit}
          onChange={(v) => update('expiry_days', v)}
        />
      </Section>

      {/* Redeem config */}
      <Section title="Redeem rules" subtitle="How customers spend points">
        <NumField
          label="₹ per point"
          help="1.0 = 1 point worth ₹1"
          value={draft.redeem_rupee_per_point}
          step={0.1}
          min={0}
          disabled={!canEdit}
          onChange={(v) => update('redeem_rupee_per_point', v)}
        />
        <NumField
          label="Min points to redeem"
          help="Lower bound per redemption"
          value={draft.min_redeem_points}
          step={10}
          min={0}
          disabled={!canEdit}
          onChange={(v) => update('min_redeem_points', v)}
        />
        <NumField
          label="Max % of order value"
          help="Cap on points-discount as a % of order"
          value={draft.max_redeem_pct_of_order}
          step={1}
          min={0}
          max={100}
          disabled={!canEdit}
          onChange={(v) => update('max_redeem_pct_of_order', v)}
        />
      </Section>

      {/* Category multipliers */}
      <Section
        title="Category multipliers"
        subtitle="Per-category boost on earn (e.g. lenses earn 1.5×)"
      >
        <KeyValueEditor
          entries={draft.category_multipliers}
          knownKeys={KNOWN_CATEGORIES}
          onChange={(k, v) => setMapEntry('category_multipliers', k, v)}
          onRemove={(k) => removeMapEntry('category_multipliers', k)}
          step={0.1}
          disabled={!canEdit}
          placeholder="Category key"
        />
      </Section>

      {/* Tier thresholds */}
      <Section
        title="Tier thresholds"
        subtitle="Lifetime points required to reach each tier"
      >
        <KeyValueEditor
          entries={draft.tier_thresholds}
          knownKeys={KNOWN_TIERS}
          onChange={(k, v) => setMapEntry('tier_thresholds', k, v)}
          onRemove={(k) => removeMapEntry('tier_thresholds', k)}
          step={100}
          disabled={!canEdit}
          placeholder="Tier"
        />
      </Section>

      {/* Tier multipliers */}
      <Section
        title="Tier multipliers"
        subtitle="Earn boost applied based on the customer's current tier"
      >
        <KeyValueEditor
          entries={draft.tier_multipliers}
          knownKeys={KNOWN_TIERS}
          onChange={(k, v) => setMapEntry('tier_multipliers', k, v)}
          onRemove={(k) => removeMapEntry('tier_multipliers', k)}
          step={0.05}
          disabled={!canEdit}
          placeholder="Tier"
        />
      </Section>

      {/* Sticky save bar */}
      <div className="sticky bottom-0 bg-white border-t border-gray-200 -mx-4 px-4 py-3 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={handleReset}
          disabled={!dirty || saving}
          className="px-3 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded flex items-center gap-1.5 disabled:opacity-40"
        >
          <RotateCcw className="w-4 h-4" />
          Discard
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || saving || !canEdit}
          className="px-4 py-2 bg-bv-red-600 hover:bg-bv-red-700 text-white text-sm font-semibold rounded flex items-center gap-2 disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          Save changes
        </button>
      </div>
    </div>
  );
}

// ============================================================================
// Internal helpers
// ============================================================================

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3">
        <p className="font-medium text-gray-900">{title}</p>
        {subtitle && <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">{children}</div>
    </div>
  );
}

function NumField({
  label,
  help,
  value,
  step = 1,
  min,
  max,
  disabled,
  onChange,
}: {
  label: string;
  help?: string;
  value: number;
  step?: number;
  min?: number;
  max?: number;
  disabled?: boolean;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          onChange(Number.isFinite(n) ? n : 0);
        }}
        className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded disabled:bg-gray-50"
      />
      {help && <p className="text-[11px] text-gray-500 mt-1">{help}</p>}
    </div>
  );
}

function KeyValueEditor({
  entries,
  knownKeys,
  onChange,
  onRemove,
  step = 1,
  disabled,
  placeholder,
}: {
  entries: Record<string, number>;
  knownKeys: string[];
  onChange: (k: string, v: number) => void;
  onRemove: (k: string) => void;
  step?: number;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [newKey, setNewKey] = useState('');
  const [newVal, setNewVal] = useState('');

  const sortedKeys = Array.from(
    new Set([...knownKeys, ...Object.keys(entries)]),
  ).filter((k) => k in entries);

  const handleAdd = () => {
    const k = newKey.trim().toUpperCase();
    if (!k) return;
    const v = parseFloat(newVal);
    onChange(k, Number.isFinite(v) ? v : 0);
    setNewKey('');
    setNewVal('');
  };

  return (
    <div className="md:col-span-3 space-y-2">
      {sortedKeys.length === 0 && (
        <p className="text-xs text-gray-500">No entries yet — add one below.</p>
      )}
      {sortedKeys.map((k) => (
        <div key={k} className="flex items-center gap-2">
          <span className="font-mono text-xs px-2 py-1.5 bg-gray-50 border border-gray-200 rounded w-44">
            {k}
          </span>
          <input
            type="number"
            step={step}
            value={entries[k]}
            disabled={disabled}
            onChange={(e) => {
              const n = parseFloat(e.target.value);
              onChange(k, Number.isFinite(n) ? n : 0);
            }}
            className="px-2 py-1.5 text-sm border border-gray-300 rounded w-32 disabled:bg-gray-50"
          />
          {!disabled && (
            <button
              type="button"
              onClick={() => onRemove(k)}
              className="p-1.5 text-gray-400 hover:text-red-600"
              title="Remove"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>
      ))}

      {!disabled && (
        <div className="flex items-center gap-2 pt-2 border-t border-gray-100">
          <input
            type="text"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            placeholder={placeholder || 'key'}
            list="loyalty-known-keys"
            className="px-2 py-1.5 text-sm border border-gray-300 rounded w-44 font-mono uppercase"
          />
          <input
            type="number"
            step={step}
            value={newVal}
            onChange={(e) => setNewVal(e.target.value)}
            placeholder="value"
            className="px-2 py-1.5 text-sm border border-gray-300 rounded w-32"
          />
          <button
            type="button"
            onClick={handleAdd}
            className="px-2 py-1.5 bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-semibold rounded flex items-center gap-1"
          >
            <Plus className="w-3.5 h-3.5" />
            Add
          </button>
          <datalist id="loyalty-known-keys">
            {knownKeys.map((k) => (
              <option key={k} value={k} />
            ))}
          </datalist>
        </div>
      )}
    </div>
  );
}

export default LoyaltySettingsSection;
