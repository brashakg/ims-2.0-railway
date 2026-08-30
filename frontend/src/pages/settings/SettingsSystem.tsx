// ============================================================================
// IMS 2.0 - Settings · System (/settings/system)
// ============================================================================
// Wave 1 split: the inline SystemSection (+ TargetTickerSettings) and its data
// loading moved verbatim out of the old SettingsPage tab container. Renders
// inside SettingsLayout.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState, useEffect } from 'react';
import { RefreshCw, Database, Target, Save, ChevronRight } from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { adminSystemApi, policiesApi } from '../../services/api';
import { financeApi } from '../../services/api/finance';
import { AdminControlPanel } from '../../components/settings/AdminControlPanel';
import { AutoLogoutSettings } from '../../components/settings/AutoLogoutSettings';

export function SystemSettingsPage() {
  const [isLoading, setIsLoading] = useState(true);
  const [systemStatus, setSystemStatus] = useState<{ database: string; api: string; version: string } | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      setIsLoading(true);
      try {
        const statusRes = await adminSystemApi.getSystemStatus().catch(() => null);
        if (alive && statusRes) setSystemStatus(statusRes);
      } finally {
        if (alive) setIsLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-48">
        <RefreshCw className="w-8 h-8 animate-spin" style={{ color: 'var(--bv)' }} />
      </div>
    );
  }
  return <SystemSection systemStatus={systemStatus} />;
}

function SystemSection({ systemStatus }: { systemStatus: { database: string; api: string; version: string } | null }) {
  const toast = useToast();

  return (
    <div className="space-y-4">
      <div className="card">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">System Status</h2>
        <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-3 gap-4">
          <div className={clsx('p-4 rounded-lg', systemStatus?.database === 'connected' ? 'bg-green-50' : 'bg-amber-50')}>
            <p className="text-sm text-gray-500">Database</p>
            <p className={clsx('font-medium', systemStatus?.database === 'connected' ? 'text-green-700' : 'text-amber-700')}>
              {systemStatus?.database || 'Checking...'}
            </p>
          </div>
          <div className={clsx('p-4 rounded-lg', systemStatus?.api === 'healthy' ? 'bg-green-50' : 'bg-amber-50')}>
            <p className="text-sm text-gray-500">API Status</p>
            <p className={clsx('font-medium', systemStatus?.api === 'healthy' ? 'text-green-700' : 'text-amber-700')}>
              {systemStatus?.api || 'Checking...'}
            </p>
          </div>
          <div className="p-4 bg-gray-50 rounded-lg">
            <p className="text-sm text-gray-500">Version</p>
            <p className="font-medium text-gray-900">{systemStatus?.version || '2.0.0'}</p>
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
