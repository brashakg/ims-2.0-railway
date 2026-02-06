// ============================================================================
// IMS 2.0 - Notification Settings Component
// ============================================================================
// Configure SMS/WhatsApp notification providers and templates

import { useState, useEffect } from 'react';
import {
  MessageSquare,
  Mail,
  Bell,
  Settings,
  Save,
  TestTube2,
  AlertCircle,
  Loader2,
  Eye,
  EyeOff,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  type NotificationProvider,
  type NotificationProviderConfig,
  type NotificationTemplate,
  NOTIFICATION_TEMPLATES,
  getTemplatesByCategory,
  populateTemplate,
} from '../../constants/notifications';
import clsx from 'clsx';

export function NotificationSettings() {
  const { hasRole } = useAuth();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<'provider' | 'templates'>('provider');
  const [isSaving, setIsSaving] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);

  // Provider Configuration
  const [providerConfig, setProviderConfig] = useState<NotificationProviderConfig>({
    provider: 'MSG91',
    apiKey: '',
    apiSecret: '',
    senderId: '',
    webhookUrl: '',
    isActive: false,
  });

  // Template states
  const [_templates, setTemplates] = useState<NotificationTemplate[]>(
    Object.values(NOTIFICATION_TEMPLATES)
  );
  const [selectedTemplate, setSelectedTemplate] = useState<NotificationTemplate | null>(null);
  const [testPhone, setTestPhone] = useState('');

  const canManageSettings = hasRole(['SUPERADMIN', 'ADMIN']);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    // In production, fetch from API
    // For now, load from local storage or use defaults
    const saved = localStorage.getItem('notificationProviderConfig');
    if (saved) {
      setProviderConfig(JSON.parse(saved));
    }
  };

  const handleSaveProvider = async () => {
    setIsSaving(true);
    try {
      // In production, save to backend API
      localStorage.setItem('notificationProviderConfig', JSON.stringify(providerConfig));
      await new Promise(resolve => setTimeout(resolve, 500));
      toast.success('Provider settings saved successfully');
    } catch (error: any) {
      toast.error(error?.message || 'Failed to save settings');
    } finally {
      setIsSaving(false);
    }
  };

  const handleToggleTemplate = (templateId: string) => {
    setTemplates(prev =>
      prev.map(t =>
        t.id === templateId ? { ...t, isActive: !t.isActive } : t
      )
    );
  };

  const handleTestNotification = async () => {
    if (!selectedTemplate || !testPhone) {
      toast.error('Please select a template and enter a phone number');
      return;
    }

    if (!providerConfig.isActive || !providerConfig.apiKey) {
      toast.error('Please configure and activate a notification provider first');
      return;
    }

    try {
      // Mock variables for testing
      const mockVariables = {
        customerName: 'John Doe',
        orderNumber: 'ORD-12345',
        amount: '2500',
        deliveryDate: '10 Feb 2026',
        storeName: 'Better Vision Optics',
        storeAddress: 'Shop 123, MG Road, Mumbai',
        storePhone: '+91 98765 43210',
        trackingLink: 'https://example.com/track',
      };

      const message = populateTemplate(selectedTemplate.template, mockVariables);

      // In production, call API to send test notification
      console.log('Sending test notification:', {
        phone: testPhone,
        message,
        channel: selectedTemplate.channel,
        provider: providerConfig.provider,
      });

      await new Promise(resolve => setTimeout(resolve, 1000));

      toast.success(`Test ${selectedTemplate.channel} sent to ${testPhone}`);
    } catch (error: any) {
      toast.error(error?.message || 'Failed to send test notification');
    }
  };

  if (!canManageSettings) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <AlertCircle className="w-16 h-16 mx-auto text-gray-300 mb-4" />
          <h2 className="text-xl font-semibold text-gray-700">Access Denied</h2>
          <p className="text-gray-500">You don't have permission to manage notification settings.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Notification Settings</h1>
        <p className="text-gray-500">Configure SMS/WhatsApp providers and templates</p>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200">
        <button
          onClick={() => setActiveTab('provider')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'provider'
              ? 'border-purple-600 text-purple-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          )}
        >
          <Settings className="w-4 h-4" />
          Provider Configuration
        </button>
        <button
          onClick={() => setActiveTab('templates')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors',
            activeTab === 'templates'
              ? 'border-purple-600 text-purple-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          )}
        >
          <Bell className="w-4 h-4" />
          Notification Templates
        </button>
      </div>

      {/* Provider Configuration Tab */}
      {activeTab === 'provider' && (
        <div className="space-y-4">
          {/* Info Banner */}
          <div className="card bg-blue-50 border-blue-200">
            <div className="flex gap-3">
              <MessageSquare className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-blue-900">
                <p className="font-medium mb-1">Supported Providers</p>
                <p className="text-blue-800">
                  Choose from MSG91, Twilio, or Gupshup for SMS and WhatsApp Business API integration.
                  Ensure your provider account has DLT (Distributed Ledger Technology) compliance for India.
                </p>
              </div>
            </div>
          </div>

          {/* Provider Selection */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Select Provider</h3>
            <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
              {(['MSG91', 'TWILIO', 'GUPSHUP'] as NotificationProvider[]).map((provider) => (
                <button
                  key={provider}
                  onClick={() => setProviderConfig({ ...providerConfig, provider })}
                  className={clsx(
                    'p-4 rounded-lg border-2 transition-all text-center',
                    providerConfig.provider === provider
                      ? 'border-purple-500 bg-purple-50'
                      : 'border-gray-200 hover:border-gray-300'
                  )}
                >
                  <MessageSquare className="w-8 h-8 mx-auto mb-2 text-purple-600" />
                  <span className="font-medium text-gray-900">{provider}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Provider Credentials */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              {providerConfig.provider} Configuration
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  API Key <span className="text-red-500">*</span>
                </label>
                <div className="relative">
                  <input
                    type={showApiKey ? 'text' : 'password'}
                    value={providerConfig.apiKey}
                    onChange={(e) =>
                      setProviderConfig({ ...providerConfig, apiKey: e.target.value })
                    }
                    className="input-field w-full pr-10"
                    placeholder="Enter your API key"
                  />
                  <button
                    onClick={() => setShowApiKey(!showApiKey)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500"
                  >
                    {showApiKey ? (
                      <EyeOff className="w-4 h-4" />
                    ) : (
                      <Eye className="w-4 h-4" />
                    )}
                  </button>
                </div>
              </div>

              {providerConfig.provider === 'MSG91' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Auth Key (Sender ID)
                  </label>
                  <input
                    type="text"
                    value={providerConfig.senderId}
                    onChange={(e) =>
                      setProviderConfig({ ...providerConfig, senderId: e.target.value })
                    }
                    className="input-field w-full"
                    placeholder="e.g., BTRVST"
                  />
                </div>
              )}

              {providerConfig.provider === 'TWILIO' && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Account SID
                    </label>
                    <input
                      type="text"
                      value={providerConfig.apiSecret}
                      onChange={(e) =>
                        setProviderConfig({ ...providerConfig, apiSecret: e.target.value })
                      }
                      className="input-field w-full"
                      placeholder="Enter Account SID"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Phone Number (Sender)
                    </label>
                    <input
                      type="text"
                      value={providerConfig.senderId}
                      onChange={(e) =>
                        setProviderConfig({ ...providerConfig, senderId: e.target.value })
                      }
                      className="input-field w-full"
                      placeholder="+1234567890"
                    />
                  </div>
                </>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Webhook URL (Optional)
                </label>
                <input
                  type="text"
                  value={providerConfig.webhookUrl}
                  onChange={(e) =>
                    setProviderConfig({ ...providerConfig, webhookUrl: e.target.value })
                  }
                  className="input-field w-full"
                  placeholder="https://your-domain.com/webhook"
                />
                <p className="text-xs text-gray-500 mt-1">
                  For delivery reports and message status updates
                </p>
              </div>

              <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                <div>
                  <p className="font-medium text-gray-900">Enable Notifications</p>
                  <p className="text-sm text-gray-500">
                    Activate this provider to start sending notifications
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={providerConfig.isActive}
                    onChange={(e) =>
                      setProviderConfig({ ...providerConfig, isActive: e.target.checked })
                    }
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-purple-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-purple-500"></div>
                </label>
              </div>

              <div className="flex justify-end">
                <button
                  onClick={handleSaveProvider}
                  disabled={isSaving}
                  className="btn-primary flex items-center gap-2"
                >
                  {isSaving ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Save className="w-4 h-4" />
                  )}
                  Save Configuration
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Templates Tab */}
      {activeTab === 'templates' && (
        <div className="grid grid-cols-1 laptop:grid-cols-3 gap-4">
          {/* Templates List */}
          <div className="laptop:col-span-2 space-y-4">
            {['TRANSACTIONAL', 'SERVICE', 'REMINDER', 'GREETING', 'PROMOTIONAL'].map((category) => {
              const categoryTemplates = getTemplatesByCategory(category as any);
              if (categoryTemplates.length === 0) return null;

              return (
                <div key={category} className="card">
                  <h3 className="text-lg font-semibold text-gray-900 mb-3">
                    {category.charAt(0) + category.slice(1).toLowerCase()} Notifications
                  </h3>
                  <div className="space-y-2">
                    {categoryTemplates.map((template) => (
                      <div
                        key={template.id}
                        className={clsx(
                          'flex items-center justify-between p-3 rounded-lg border transition-colors cursor-pointer',
                          selectedTemplate?.id === template.id
                            ? 'border-purple-300 bg-purple-50'
                            : 'border-gray-200 hover:border-gray-300'
                        )}
                        onClick={() => setSelectedTemplate(template)}
                      >
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <p className="font-medium text-gray-900">{template.name}</p>
                            <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                              {template.channel}
                            </span>
                          </div>
                          <p className="text-xs text-gray-500 mt-1 line-clamp-1">
                            {template.template}
                          </p>
                        </div>
                        <label
                          className="relative inline-flex items-center cursor-pointer ml-4"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={template.isActive}
                            onChange={() => handleToggleTemplate(template.id)}
                            className="sr-only peer"
                          />
                          <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-purple-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-500"></div>
                        </label>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Template Preview & Test */}
          <div className="space-y-4">
            {selectedTemplate ? (
              <>
                <div className="card">
                  <h3 className="text-lg font-semibold text-gray-900 mb-3">
                    {selectedTemplate.name}
                  </h3>
                  <div className="space-y-3 text-sm">
                    <div>
                      <span className="text-gray-600">Channel:</span>{' '}
                      <span className="font-medium">{selectedTemplate.channel}</span>
                    </div>
                    <div>
                      <span className="text-gray-600">Category:</span>{' '}
                      <span className="font-medium">{selectedTemplate.category}</span>
                    </div>
                    <div>
                      <span className="text-gray-600">Template:</span>
                      <p className="mt-2 p-3 bg-gray-50 rounded text-gray-900 whitespace-pre-wrap">
                        {selectedTemplate.template}
                      </p>
                    </div>
                    <div>
                      <span className="text-gray-600">Variables:</span>
                      <div className="flex flex-wrap gap-1 mt-2">
                        {selectedTemplate.variables.map((variable) => (
                          <span
                            key={variable}
                            className="text-xs px-2 py-1 bg-purple-100 text-purple-700 rounded"
                          >
                            {'{' + variable + '}'}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Test Notification */}
                <div className="card">
                  <h3 className="text-lg font-semibold text-gray-900 mb-3 flex items-center gap-2">
                    <TestTube2 className="w-5 h-5" />
                    Test Notification
                  </h3>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Phone Number
                      </label>
                      <input
                        type="text"
                        value={testPhone}
                        onChange={(e) => setTestPhone(e.target.value)}
                        className="input-field w-full"
                        placeholder="+91 98765 43210"
                      />
                    </div>
                    <button
                      onClick={handleTestNotification}
                      className="btn-primary w-full flex items-center justify-center gap-2"
                    >
                      <Mail className="w-4 h-4" />
                      Send Test
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <div className="card text-center py-12 text-gray-500">
                <Bell className="w-12 h-12 mx-auto mb-2 opacity-50" />
                <p>Select a template to preview</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default NotificationSettings;
