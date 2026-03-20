// ============================================================================
// IMS 2.0 - Integration Settings UI
// ============================================================================

import { useState } from 'react';
import { Eye, EyeOff, Zap, Calendar, Copy, Check } from 'lucide-react';
import clsx from 'clsx';

interface Integration {
  id: string;
  name: string;
  description: string;
  icon: any;
  connected: boolean;
  apiKeyField?: string;
  lastSync?: string;
  testable: boolean;
}

const INTEGRATIONS: Integration[] = [
  {
    id: 'razorpay',
    name: 'Razorpay',
    description: 'Payment gateway for credit/debit cards',
    icon: '💳',
    connected: true,
    apiKeyField: 'rzp_live_XXXXXXXXXXXXXX',
    lastSync: new Date(Date.now() - 2 * 60 * 60000).toISOString(),
    testable: true,
  },
  {
    id: 'whatsapp',
    name: 'WhatsApp Business',
    description: 'Send customer notifications and updates',
    icon: '💬',
    connected: false,
    testable: true,
  },
  {
    id: 'tally',
    name: 'Tally ERP9',
    description: 'Synchronize inventory and accounts',
    icon: '📊',
    connected: true,
    lastSync: new Date(Date.now() - 30 * 60000).toISOString(),
    testable: true,
  },
  {
    id: 'shopify',
    name: 'Shopify',
    description: 'Sync products and orders',
    icon: '🛍️',
    connected: false,
    testable: true,
  },
  {
    id: 'shiprocket',
    name: 'Shiprocket',
    description: 'Manage shipping and logistics',
    icon: '📦',
    connected: false,
    testable: true,
  },
  {
    id: 'gst-portal',
    name: 'GST Portal',
    description: 'File GST returns and compliance',
    icon: '📋',
    connected: true,
    testable: false,
  },
];

export function IntegrationSettings() {
  const [integrations] = useState<Integration[]>(INTEGRATIONS);
  const [showApiKey, setShowApiKey] = useState<Record<string, boolean>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [_testingId, setTestingId] = useState<string | null>(null);

  const handleCopyApiKey = (id: string, apiKey: string | undefined) => {
    if (!apiKey) return;
    navigator.clipboard.writeText(apiKey);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleTestConnection = async (id: string) => {
    setTestingId(id);
    // Simulate test connection
    await new Promise(resolve => setTimeout(resolve, 1500));
    setTestingId(null);
    // TODO: Call API endpoint to test connection
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
                ? 'bg-gray-800 border-green-600 border-opacity-30'
                : 'bg-gray-800 border-gray-700'
            )}
          >
            {/* Header with icon and name */}
            <div className="flex items-start justify-between mb-4">
              <div className="flex items-start gap-3">
                <span className="text-3xl">{integration.icon}</span>
                <div>
                  <h3 className="font-semibold text-white">{integration.name}</h3>
                  <p className="text-sm text-gray-400">{integration.description}</p>
                </div>
              </div>
              <span className={clsx(
                'px-3 py-1 rounded-full text-xs font-medium',
                integration.connected
                  ? 'bg-green-900 text-green-200'
                  : 'bg-gray-700 text-gray-300'
              )}>
                {integration.connected ? 'Connected' : 'Not Connected'}
              </span>
            </div>

            {/* API Key Field (if connected) */}
            {integration.apiKeyField && (
              <div className="mb-4">
                <label className="block text-xs font-medium text-gray-400 mb-2">API Key</label>
                <div className="flex gap-2">
                  <input
                    type={showApiKey[integration.id] ? 'text' : 'password'}
                    value={integration.apiKeyField}
                    readOnly
                    className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded text-sm text-gray-300 font-mono"
                  />
                  <button
                    onClick={() => setShowApiKey(prev => ({ ...prev, [integration.id]: !prev[integration.id] }))}
                    className="p-2 bg-gray-700 hover:bg-gray-600 rounded text-gray-400 transition"
                  >
                    {showApiKey[integration.id] ? (
                      <EyeOff className="w-4 h-4" />
                    ) : (
                      <Eye className="w-4 h-4" />
                    )}
                  </button>
                  <button
                    onClick={() => handleCopyApiKey(integration.id, integration.apiKeyField)}
                    className="p-2 bg-gray-700 hover:bg-gray-600 rounded text-gray-400 transition"
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
              <div className="mb-4 flex items-center gap-2 text-xs text-gray-400">
                <Calendar className="w-3 h-3" />
                <span>Last sync: {formatLastSync(integration.lastSync)}</span>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-2">
              {integration.testable && (
                <button
                  onClick={() => handleTestConnection(integration.id)}
                  disabled
                  title="Coming soon"
                  className={clsx(
                    'flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded text-sm font-medium transition',
                    'bg-gray-700 text-gray-500 cursor-not-allowed'
                  )}
                >
                  <Zap className="w-4 h-4" />
                  Test Connection
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
      <div className="p-4 bg-blue-900 border border-blue-700 rounded-lg text-sm text-blue-200">
        <p>
          <strong>API Keys:</strong> Keep your API keys secure. Never share them publicly. Contact support if you need to rotate your keys.
        </p>
      </div>
    </div>
  );
}
