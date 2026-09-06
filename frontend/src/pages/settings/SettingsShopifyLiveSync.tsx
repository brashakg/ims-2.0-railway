// ============================================================================
// IMS 2.0 - Shopify live sync — Settings section (SUPERADMIN)
// ============================================================================
// Owner ruling 2026-09-06: "anytime a product that has already been pushed to
// shopify, edited or changed in our ims, it should automatically reflect on
// shopify. if needed a sync everyday twice should be done. one before store
// opening around 9 am and next at 1 am." + "make a module/section in settings
// for superadmin to tweak it as required."
//
// REUSE: a thin view over the E2 policy engine — the three keys are read and
// written through policiesApi (/settings/policies/*), GLOBAL scope. It defines
// NO new settings store; validation (1-6 valid HH:MM faces, 1..2000 per run)
// is enforced server-side in the policy registry and mirrored here only so a
// typo is caught before the round-trip. The schedule itself runs in the
// backend agents scheduler (services/shopify_live_sync); this page only
// changes what it reads on its next tick — no restart.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw, Save, Loader2, Plus, X, Info, ExternalLink } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { policiesApi } from '../../services/api/settings';

const K_ENABLED = 'shopify.live_sync.enabled';
const K_SLOTS = 'shopify.live_sync.slots';
const K_MAX = 'shopify.live_sync.max_products_per_run';

export const MAX_SLOTS = 6;
const DEFAULT_SLOTS = ['01:00', '09:00'];

interface FormState {
  enabled: boolean;
  slots: string[];
  maxPerRun: number;
}

const DEFAULTS: FormState = { enabled: true, slots: DEFAULT_SLOTS, maxPerRun: 200 };

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** IST wall-clock {h, m} of an instant — via Intl, never the browser's zone. */
function istClock(now: Date): { h: number; m: number } {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(now);
  const h = Number(parts.find((p) => p.type === 'hour')?.value ?? 0) % 24;
  const m = Number(parts.find((p) => p.type === 'minute')?.value ?? 0);
  return { h, m };
}

/** "Today 09:00 IST" / "Tomorrow 01:00 IST" for the next configured slot after
 *  `now` — the same rule the backend's next_slot applies (strictly after now,
 *  rolling to the first slot tomorrow). Null when no valid slot is set. */
export function nextRunLabel(slots: string[], now: Date = new Date()): string | null {
  const valid = [...new Set(slots.filter((s) => HHMM.test(s)))].sort();
  if (valid.length === 0) return null;
  const { h, m } = istClock(now);
  const nowMin = h * 60 + m;
  const today = valid.find((s) => {
    const [hh, mm] = s.split(':').map(Number);
    return hh * 60 + mm > nowMin;
  });
  return today ? `Today ${today} IST` : `Tomorrow ${valid[0]} IST`;
}

export function ShopifyLiveSyncSection() {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await policiesApi.getAll('global');
      const pol = res?.policies || {};
      const next: FormState = { ...DEFAULTS };
      if (typeof pol[K_ENABLED]?.value === 'boolean') next.enabled = pol[K_ENABLED].value;
      if (Array.isArray(pol[K_SLOTS]?.value) && pol[K_SLOTS].value.length > 0) {
        next.slots = pol[K_SLOTS].value.map(String);
      }
      const mx = Number(pol[K_MAX]?.value);
      if (Number.isFinite(mx) && mx >= 1) next.maxPerRun = mx;
      setForm(next);
    } catch {
      setForm(DEFAULTS); // keep code defaults on a read error (page never blanks out)
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const slotsValid = form.slots.length >= 1 && form.slots.length <= MAX_SLOTS && form.slots.every((s) => HHMM.test(s));
  const maxValid = Number.isInteger(form.maxPerRun) && form.maxPerRun >= 1 && form.maxPerRun <= 2000;
  const canSave = slotsValid && maxValid && !saving;

  const setSlot = (i: number, v: string) =>
    setForm((f) => ({ ...f, slots: f.slots.map((s, j) => (j === i ? v : s)) }));
  const addSlot = () =>
    setForm((f) => (f.slots.length >= MAX_SLOTS ? f : { ...f, slots: [...f.slots, '12:00'] }));
  const removeSlot = (i: number) =>
    setForm((f) => (f.slots.length <= 1 ? f : { ...f, slots: f.slots.filter((_, j) => j !== i) }));

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      // GLOBAL scope (scope=null); the backend dedupes + sorts the slots.
      await Promise.all([
        policiesApi.set(K_ENABLED, form.enabled, null),
        policiesApi.set(K_SLOTS, [...new Set(form.slots)].sort(), null),
        policiesApi.set(K_MAX, form.maxPerRun, null),
      ]);
      toast.success('Shopify live sync settings saved. The scheduler reads them on its next tick.');
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || 'Failed to save live sync settings');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-gray-500">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        <span className="text-sm">Loading live sync settings…</span>
      </div>
    );
  }

  const preview = form.enabled ? nextRunLabel(form.slots) : null;

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex items-center gap-2 mb-1">
          <RefreshCw className="w-5 h-5 text-gray-500" />
          <h2 className="text-lg font-semibold text-gray-900">Shopify live sync</h2>
        </div>
        <p className="text-sm text-gray-500 mb-4">
          Products that are <strong>already on Shopify</strong> and were edited in IMS are re-pushed
          automatically at these IST times. A product that was never pushed is only counted — its
          first publish stays a human press on the product. Times are Indian Standard Time.
        </p>

        {/* On / off */}
        <div className="flex items-start justify-between gap-4 p-3 bg-gray-50 rounded-lg mb-4">
          <div>
            <p className="font-medium text-gray-900">Scheduled sync enabled</p>
            <p className="text-sm text-gray-500">
              OFF stops the schedule only — the &quot;Sync live products now&quot; button keeps working.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={form.enabled}
            aria-label="Scheduled sync enabled"
            onClick={() => setForm((f) => ({ ...f, enabled: !f.enabled }))}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
              form.enabled ? 'bg-blue-600' : 'bg-gray-300'
            }`}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                form.enabled ? 'translate-x-5' : 'translate-x-0.5'
              }`}
            />
          </button>
        </div>

        {/* Slots */}
        <label className="block text-sm font-medium text-gray-600 mb-1">Sync times (IST)</label>
        <div className="space-y-2">
          {form.slots.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                type="time"
                aria-label={`Sync time ${i + 1}`}
                value={s}
                step={60}
                onChange={(e) => setSlot(i, e.target.value)}
                className="input-field w-40"
              />
              <button
                type="button"
                aria-label={`Remove sync time ${i + 1}`}
                onClick={() => removeSlot(i)}
                disabled={form.slots.length <= 1}
                className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-40"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={addSlot}
          disabled={form.slots.length >= MAX_SLOTS}
          className="mt-2 inline-flex items-center gap-1 text-sm text-blue-700 hover:underline disabled:opacity-40 disabled:no-underline"
        >
          <Plus className="w-4 h-4" /> Add time
        </button>
        <p className="text-xs text-gray-400 mt-1">
          1 to {MAX_SLOTS} times a day. Default: 01:00 and 09:00 (before the stores open). A time the
          server missed (redeploy, restart) runs on its next check within 55 minutes — never twice.
        </p>
        {!slotsValid && (
          <p className="mt-2 text-xs text-red-600">Every time must be a valid HH:MM; keep between 1 and {MAX_SLOTS} entries.</p>
        )}

        {/* Cap */}
        <div className="mt-4 max-w-xs">
          <label className="block text-sm font-medium text-gray-600 mb-1">Max products per run</label>
          <input
            type="number"
            aria-label="Max products per run"
            min={1}
            max={2000}
            step={1}
            value={form.maxPerRun}
            onChange={(e) => setForm((f) => ({ ...f, maxPerRun: Number(e.target.value) }))}
            className="input-field"
          />
          <p className="text-xs text-gray-400 mt-1">
            A run that hits the cap says so on the sync page; the rest go next run. 1 to 2000.
          </p>
          {!maxValid && <p className="mt-1 text-xs text-red-600">Enter a whole number from 1 to 2000.</p>}
        </div>

        {/* Next run preview */}
        <p className="mt-4 text-sm text-gray-700 inline-flex items-center gap-1" data-testid="next-run-preview">
          <Info className="w-4 h-4 text-gray-400" />
          {preview ? <>Next run: <strong>{preview}</strong></> : <>Schedule is off — nothing runs automatically.</>}
        </p>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button type="button" onClick={save} disabled={!canSave} className="btn-primary">
            {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
            {saving ? 'Saving…' : 'Save live sync settings'}
          </button>
          <Link to="/online-store/shopify" className="inline-flex items-center gap-1 text-sm text-blue-700 hover:underline">
            <ExternalLink className="w-4 h-4" /> Open the Shopify sync page (run now, last run, failures)
          </Link>
        </div>
      </div>
    </div>
  );
}

export default ShopifyLiveSyncSection;
