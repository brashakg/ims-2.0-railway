// ============================================================================
// IMS 2.0 - Integration Settings UI
// ============================================================================

import { useState, useEffect } from 'react';
import { Eye, EyeOff, Zap, Calendar, Copy, Check, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { settingsApi } from '../../services/api/settings';

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
                  ? 'bg-green-50 text-green-200'
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

      {/* Info box */}
      <div className="p-4 bg-blue-50 border border-blue-700 rounded-lg text-sm text-blue-200">
        <p>
          <strong>API Keys:</strong> Keep your API keys secure. Never share them publicly. Contact support if you need to rotate your keys.
        </p>
      </div>
    </div>
  );
}
