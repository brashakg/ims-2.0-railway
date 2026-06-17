// ============================================================================
// IMS 2.0 - Auto-Logout & Session Security settings (SUPERADMIN)
// ============================================================================
// Configures the idle auto-logout policy persisted on the system_settings
// singleton (keys: auto_logout_enabled / auto_logout_minutes /
// auto_logout_warn_seconds) and served to every user on /health. SUPERADMIN can
// edit; everyone else (the System tab is ADMIN/SUPERADMIN-gated) sees it
// read-only. We fetch the FULL system settings object first and merge our keys
// in on save so other system keys are never wiped.

import { useEffect, useState } from 'react';
import { LogOut } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api/settings';

const DEFAULTS = { enabled: true, minutes: 15, warnSeconds: 60 };
const MINUTE_PRESETS = [5, 10, 15, 30, 60];

export function AutoLogoutSettings() {
  const toast = useToast();
  const { hasRole } = useAuth();
  const canEdit = hasRole(['SUPERADMIN']);

  // The full system settings object, kept so we can merge-on-save (never wipe
  // sibling keys like maintenance_mode / max_login_attempts).
  const [current, setCurrent] = useState<Record<string, unknown>>({});
  const [enabled, setEnabled] = useState(DEFAULTS.enabled);
  const [minutes, setMinutes] = useState(DEFAULTS.minutes);
  const [warnSeconds, setWarnSeconds] = useState(DEFAULTS.warnSeconds);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    settingsApi
      .getSystemSettings()
      .then((data: Record<string, unknown>) => {
        if (!alive) return;
        setCurrent(data || {});
        if (typeof data?.auto_logout_enabled === 'boolean') setEnabled(data.auto_logout_enabled);
        const m = Number(data?.auto_logout_minutes);
        if (Number.isFinite(m) && m > 0) setMinutes(m);
        const w = Number(data?.auto_logout_warn_seconds);
        if (Number.isFinite(w) && w > 0) setWarnSeconds(w);
      })
      .catch(() => {
        /* keep defaults */
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const save = async () => {
    const m = Math.round(Number(minutes));
    const w = Math.round(Number(warnSeconds));
    if (!Number.isFinite(m) || m < 1 || m > 480) {
      toast.error('Log-out time must be between 1 and 480 minutes');
      return;
    }
    if (!Number.isFinite(w) || w < 10 || w > 600) {
      toast.error('Warning time must be between 10 and 600 seconds');
      return;
    }
    if (w >= m * 60) {
      toast.error('Warning time must be shorter than the log-out time');
      return;
    }
    setSaving(true);
    try {
      const merged = {
        ...current,
        auto_logout_enabled: enabled,
        auto_logout_minutes: m,
        auto_logout_warn_seconds: w,
      };
      const res = await settingsApi.updateSystemSettings(merged);
      // The PUT echoes back the cleaned settings; keep our local copy in sync.
      setCurrent((prev) => ({ ...prev, ...(res?.settings || merged) }));
      setMinutes(m);
      setWarnSeconds(w);
      toast.success('Auto-logout settings saved. Applies to each user on their next sign-in / page load.');
    } catch {
      toast.error('Failed to save auto-logout settings');
    } finally {
      setSaving(false);
    }
  };

  const isPreset = MINUTE_PRESETS.includes(minutes);

  return (
    <div className="card mt-6">
      <div className="flex items-center gap-2 mb-1">
        <LogOut className="w-5 h-5 text-gray-500" />
        <h2 className="text-lg font-semibold text-gray-900">Auto-Logout &amp; Session Security</h2>
      </div>
      <p className="text-sm text-gray-500 mb-4">
        Automatically sign out idle users after a period of inactivity. A warning appears shortly
        before sign-out so staff can stay signed in. Changes apply to each user on their next
        sign-in or page load.
      </p>

      {!canEdit && (
        <p className="text-xs text-gray-400 mb-3">Superadmin can change this.</p>
      )}

      <div className="space-y-4">
        <label className="flex items-center gap-3">
          <input
            type="checkbox"
            checked={enabled}
            disabled={!canEdit || loading}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-4 w-4"
          />
          <span className="text-sm font-medium text-gray-700">
            Enable idle auto-logout
          </span>
        </label>

        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">
              Log out after (minutes)
            </label>
            <select
              value={isPreset ? String(minutes) : 'custom'}
              disabled={!canEdit || loading || !enabled}
              onChange={(e) => {
                if (e.target.value !== 'custom') setMinutes(parseInt(e.target.value, 10));
              }}
              className="input-field"
              title="Idle minutes before auto-logout"
            >
              {MINUTE_PRESETS.map((p) => (
                <option key={p} value={String(p)}>
                  {p} minutes
                </option>
              ))}
              <option value="custom">Custom…</option>
            </select>
            {!isPreset && (
              <input
                type="number"
                min={1}
                max={480}
                value={minutes}
                disabled={!canEdit || loading || !enabled}
                onChange={(e) => setMinutes(parseInt(e.target.value, 10) || 0)}
                placeholder="15"
                title="Custom idle minutes (1-480)"
                className="input-field mt-2"
              />
            )}
            <p className="text-xs text-gray-400 mt-1">Between 1 and 480 minutes.</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">
              Warn before (seconds)
            </label>
            <input
              type="number"
              min={10}
              max={600}
              value={warnSeconds}
              disabled={!canEdit || loading || !enabled}
              onChange={(e) => setWarnSeconds(parseInt(e.target.value, 10) || 0)}
              placeholder="60"
              title="Seconds of warning before auto-logout"
              className="input-field"
            />
            <p className="text-xs text-gray-400 mt-1">
              10-600 seconds, and shorter than the log-out time.
            </p>
          </div>
        </div>
      </div>

      {canEdit && (
        <button type="button" onClick={save} disabled={saving || loading} className="btn-primary mt-4">
          {saving ? 'Saving…' : 'Save Auto-Logout Settings'}
        </button>
      )}
    </div>
  );
}

export default AutoLogoutSettings;
