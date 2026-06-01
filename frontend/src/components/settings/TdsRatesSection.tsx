// ============================================================================
// IMS 2.0 - Settings: TDS Rates (SUPERADMIN editable)
// ============================================================================
// National TDS rate set used when paying vendors / contractors / landlords.
// The AP/payment path resolves each section's rate from here (an admin override
// over the code defaults in backend ap_engine.TDS_SECTIONS), so when the Budget
// revises a rate the owner edits it here — no redeploy. SUPERADMIN-only.

import { useEffect, useState } from 'react';
import { Save, RefreshCw, AlertCircle, Loader2, Info, RotateCcw } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { settingsApi } from '../../services/api/settings';

// Friendly labels for the canonical TDS sections (backend ap_engine.TDS_SECTIONS).
const SECTION_LABELS: Record<string, string> = {
  NONE: 'No TDS',
  '194C_IND': '194C — Contractor (individual / HUF)',
  '194C_OTHER': '194C — Contractor (company / firm)',
  '194J': '194J — Professional services',
  '194J_TECH': '194J — Technical services',
  '194Q': '194Q — Purchase of goods',
  '194H': '194H — Commission / brokerage',
  '194I_PLANT': '194I — Rent (plant & machinery)',
  '194I_LAND': '194I — Rent (land / building)',
};

export function TdsRatesSection() {
  const toast = useToast();
  const { hasRole } = useAuth();
  const canEdit = hasRole?.(['SUPERADMIN']);

  const [rates, setRates] = useState<Record<string, number>>({});
  const [defaults, setDefaults] = useState<Record<string, number>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await settingsApi.getTdsRates();
      setRates(r.rates || {});
      setDefaults(r.defaults || {});
      const d: Record<string, string> = {};
      Object.entries(r.rates || {}).forEach(([k, v]) => { d[k] = String(v); });
      setDraft(d);
    } catch {
      setError('Could not load TDS rates.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    // Only send sections that actually changed vs the loaded effective rates.
    const payload: Record<string, number> = {};
    for (const [sec, val] of Object.entries(draft)) {
      const n = parseFloat(val);
      if (isNaN(n)) { toast.error(`Rate for ${sec} must be a number`); return; }
      if (n < 0 || n > 30) { toast.error(`Rate for ${sec} must be 0–30%`); return; }
      if (n !== rates[sec]) payload[sec] = n;
    }
    if (Object.keys(payload).length === 0) { toast.info('No changes to save'); return; }
    setSaving(true);
    try {
      await settingsApi.updateTdsRates(payload);
      toast.success('TDS rates updated');
      await load();
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Could not save TDS rates');
    } finally {
      setSaving(false);
    }
  };

  const sections = Object.keys(rates).length
    ? Object.keys(rates)
    : Object.keys(SECTION_LABELS);

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">TDS Rates</h2>
          <p className="text-sm text-gray-500">Tax deducted at source on vendor / rent / contractor payments.</p>
        </div>
        <button onClick={load} className="btn-outline text-sm flex items-center gap-1" disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      <div className="flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
        <Info className="w-4 h-4 mt-0.5 flex-shrink-0" />
        <span>National rate set. Confirm rates with your accountant — the system applies the rate to the payment base; it does not enforce monetary thresholds.</span>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {loading ? (
        <div className="py-8 text-center text-gray-400"><Loader2 className="w-5 h-5 animate-spin inline" /> Loading…</div>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg divide-y divide-gray-100">
          {sections.map((sec) => {
            const def = defaults[sec];
            const changed = draft[sec] !== undefined && parseFloat(draft[sec]) !== rates[sec];
            return (
              <div key={sec} className="flex items-center justify-between gap-4 px-4 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">{SECTION_LABELS[sec] || sec}</p>
                  {def !== undefined && (
                    <p className="text-xs text-gray-400">Default {def}%{changed ? ' · edited' : ''}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="number" step="0.1" min="0" max="30"
                    value={draft[sec] ?? ''}
                    disabled={!canEdit}
                    onChange={(e) => setDraft((p) => ({ ...p, [sec]: e.target.value }))}
                    className={`input-field w-24 text-right ${changed ? 'border-amber-400' : ''}`}
                  />
                  <span className="text-sm text-gray-500 w-4">%</span>
                  {canEdit && def !== undefined && parseFloat(draft[sec] ?? '') !== def && (
                    <button
                      title="Reset to default"
                      onClick={() => setDraft((p) => ({ ...p, [sec]: String(def) }))}
                      className="text-gray-400 hover:text-gray-600"
                    >
                      <RotateCcw className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {canEdit ? (
        <div className="flex justify-end">
          <button onClick={save} disabled={saving || loading} className="btn-primary flex items-center gap-2">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save Rates
          </button>
        </div>
      ) : (
        <p className="text-xs text-gray-400 text-right">Read-only — only a Superadmin can edit tax rates.</p>
      )}
    </div>
  );
}

export default TdsRatesSection;
