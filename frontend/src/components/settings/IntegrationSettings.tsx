// ============================================================================
// IMS 2.0 - Integration Settings UI
// ============================================================================

import { useState, useEffect, useCallback } from 'react';
import { Eye, EyeOff, Zap, Calendar, Copy, Check, Loader2, Download, RefreshCw, AlertTriangle, FileDown } from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { settingsApi, adminIntegrationApi } from '../../services/api/settings';
import { IntegrationStatusCard } from '../integrations/IntegrationStatusCard';

interface Integration {
  id: string;
  name: string;
  description: string;
  icon: string;
  connected: boolean;
  apiKeyField?: string;
  lastSync?: string;
  testable: boolean;
}

// Default integration definitions (metadata only)
const DEFAULT_INTEGRATIONS: Integration[] = [
  { id: 'razorpay', name: 'Razorpay', description: 'Payment gateway for credit/debit cards', icon: 'PAY', connected: false, testable: true },
  { id: 'whatsapp', name: 'WhatsApp Business', description: 'Send customer notifications and updates', icon: 'MSG', connected: false, testable: true },
  { id: 'tally', name: 'Tally ERP9', description: 'Synchronize inventory and accounts', icon: 'ERP', connected: false, testable: true },
  { id: 'shopify', name: 'Shopify', description: 'Sync products and orders', icon: 'SHOP', connected: false, testable: true },
  { id: 'shiprocket', name: 'Shiprocket', description: 'Manage shipping and logistics', icon: 'SHIP', connected: false, testable: true },
  { id: 'gst-portal', name: 'GST Portal', description: 'File GST returns and compliance', icon: 'GST', connected: false, testable: false },
];

export function IntegrationSettings() {
  const [integrations, setIntegrations] = useState<Integration[]>(DEFAULT_INTEGRATIONS);
  const [, setIsLoading] = useState(true);
  const [showApiKey, setShowApiKey] = useState<Record<string, boolean>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const toast = useToast();

  // Load integration configs from API
  useEffect(() => {
    (async () => {
      try {
        const response = await settingsApi.getIntegrations();
        const apiIntegrations = response?.integrations || [];
        if (apiIntegrations.length > 0) {
          // Merge API data with defaults for metadata
          const merged = DEFAULT_INTEGRATIONS.map(def => {
            const apiData = apiIntegrations.find(
              (a: Record<string, unknown>) => (a.type || a.id || '').toString().toLowerCase() === def.id
            );
            if (apiData) {
              return {
                ...def,
                connected: apiData.enabled ?? apiData.connected ?? def.connected,
                apiKeyField: apiData.api_key || apiData.apiKeyField || def.apiKeyField,
                lastSync: apiData.last_sync || apiData.lastSync || def.lastSync,
              };
            }
            return def;
          });
          setIntegrations(merged);
        }
      } catch {
        // Fall back to defaults
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

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

  return (
    <div className="space-y-6">
      {/* Read-only, SUPERADMIN-only honest status (renders null otherwise) */}
      <IntegrationStatusCard />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {integrations.map(integration => (
          <div
            key={integration.id}
            className={clsx(
              'p-6 rounded-lg border-2 transition',
              integration.connected
                ? 'bg-white border-green-600 border-opacity-30'
                : 'bg-white border-gray-200'
            )}
          >
            {/* Header with icon and name */}
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

            {/* API Key Field (if connected) */}
            {integration.apiKeyField && (
              <div className="mb-4">
                <label className="block text-xs font-medium text-gray-500 mb-2">API Key</label>
                <div className="flex gap-2">
                  <input
                    type={showApiKey[integration.id] ? 'text' : 'password'}
                    value={integration.apiKeyField}
                    readOnly
                    className="flex-1 px-3 py-2 bg-gray-100 border border-gray-300 rounded text-sm text-gray-700 font-mono"
                  />
                  <button
                    onClick={() => setShowApiKey(prev => ({ ...prev, [integration.id]: !prev[integration.id] }))}
                    className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-500 transition"
                  >
                    {showApiKey[integration.id] ? (
                      <EyeOff className="w-4 h-4" />
                    ) : (
                      <Eye className="w-4 h-4" />
                    )}
                  </button>
                  <button
                    onClick={() => handleCopyApiKey(integration.id, integration.apiKeyField)}
                    className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-500 transition"
                  >
                    {copiedId === integration.id ? (
                      <Check className="w-4 h-4 text-green-500" />
                    ) : (
                      <Copy className="w-4 h-4" />
                    )}
                  </button>
                </div>
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
              <button className="flex-1 px-3 py-2 rounded text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition">
                Configure
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Tally per-store voucher export panel (Phase I-6).
          One row per active store that had qualifying orders on the
          selected date. The CA's Remote-Desktop Tally companies (one
          per branch) consume these XML files — operator clicks
          Download to grab one and import in the matching company. */}
      <TallyExportsPanel />

      {/* Info box */}
      <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700">
        <p>
          <strong>API Keys:</strong> Keep your API keys secure. Never share them publicly. Contact support if you need to rotate your keys.
        </p>
      </div>
    </div>
  );
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
                            ? `${row.balance_check.mismatch_count} mismatched, batch delta ₹${row.balance_check.batch_delta}`
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
