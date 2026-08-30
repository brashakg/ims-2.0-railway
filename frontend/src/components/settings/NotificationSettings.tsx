// ============================================================================
// IMS 2.0 - Notification Settings Component
// ============================================================================
// Configure SMS/WhatsApp notification providers and templates

import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  MessageSquare,
  Mail,
  Bell,
  Settings,
  TestTube2,
  AlertCircle,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import {
  type NotificationProvider,
  type NotificationProviderConfig,
  type NotificationTemplate,
  NOTIFICATION_TEMPLATES,
} from '../../constants/notifications';
import clsx from 'clsx';
import { settingsApi } from '../../services/api/settings';

/** One channel's live readiness. Read-only by design - the credential that
 *  makes it green is entered in Settings -> Integrations. */
function ChannelStatusRow({
  label,
  provider,
  connected,
  detail,
}: {
  label: string;
  provider: string;
  connected: boolean;
  detail: string;
}) {
  return (
    <div className="flex items-center justify-between p-3 rounded-lg border border-gray-200">
      <div>
        <p className="font-medium text-gray-900">
          {label} <span className="text-gray-500 font-normal">via {provider}</span>
        </p>
        <p className="text-sm text-gray-500">{detail}</p>
      </div>
      <span
        className={clsx(
          'flex items-center gap-1.5 text-sm font-medium',
          connected ? 'text-green-700' : 'text-gray-500'
        )}
      >
        {connected ? (
          <CheckCircle2 className="w-4 h-4" />
        ) : (
          <XCircle className="w-4 h-4" />
        )}
        {connected ? 'Connected' : 'Not connected'}
      </span>
    </div>
  );
}

export function NotificationSettings() {
  const { hasRole } = useAuth();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<'provider' | 'templates'>('provider');

  // Live connection status, READ-ONLY. Credentials are entered once, in
  // Settings -> Integrations -> "WhatsApp Business (MSG91)", which is the
  // config the message sender actually reads. This screen used to offer its
  // own API-key box that nothing ever read, so a key typed here looked saved
  // and sent nothing.
  const [providerConfig, setProviderConfig] = useState<NotificationProviderConfig>({
    provider: 'MSG91',
    senderId: '',
    isActive: false,
  });
  const [smsReady, setSmsReady] = useState(false);
  // Server-side send switch (DISPATCH_MODE), exactly as the server reported it.
  // `undefined` means the request is still IN FLIGHT; `null` means it was
  // NEVER READ (401 / 500 / timeout), and that is a state of its own on
  // purpose: the caption printed under this value is an explicit promise to
  // the owner that nothing is reaching customers, so defaulting a failed read
  // to 'off' prints that promise over a server that may be LIVE. For the
  // channel tiles below, "not connected" is the conservative guess; for the
  // send gate the conservative answer is "I could not read it".
  const [dispatchMode, setDispatchMode] = useState<string | null | undefined>(undefined);

  // Template states
  const [templates, setTemplates] = useState<NotificationTemplate[]>(
    Object.values(NOTIFICATION_TEMPLATES)
  );
  const [selectedTemplate, setSelectedTemplate] = useState<NotificationTemplate | null>(null);
  const [testPhone, setTestPhone] = useState('');

  const canManageSettings = hasRole(['SUPERADMIN', 'ADMIN']);

  // The only three values the server's gate can report. Anything else is
  // rendered as "unrecognised", never verbatim with the assurance under it.
  const modeIsKnown =
    dispatchMode === 'off' || dispatchMode === 'test' || dispatchMode === 'live';

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    // Load live channel status from the API.
    // GET /settings/notifications/providers returns a NESTED shape:
    //   { whatsapp: { provider, enabled, sender }, sms: {...}, email: {...}, dispatch_mode }
    // `enabled` is the SAME resolution the sender uses (Settings ->
    // Integrations first, then the MSG91_* env vars), so "Connected" here
    // means a send would really go out. No credential value is ever returned.
    try {
      const data = await settingsApi.getNotificationProviders();
      if (data) {
        const wa = (data.whatsapp ?? {}) as { provider?: string; enabled?: boolean; sender?: string };
        const sms = (data.sms ?? {}) as { provider?: string; enabled?: boolean; sender?: string };
        setProviderConfig((prev) => ({
          ...prev,
          provider: (wa.provider ?? sms.provider ?? prev.provider) as NotificationProvider,
          senderId: wa.sender ?? sms.sender ?? '',
          isActive: Boolean(wa.enabled ?? false),
        }));
        setSmsReady(Boolean(sms.enabled ?? false));
        // No `?? 'off'`: a missing key is not a dark server, it is an
        // unanswered question, and the caption must not answer it for us.
        setDispatchMode(
          data.dispatch_mode == null ? null : String(data.dispatch_mode)
        );
      } else {
        // An empty body answered nothing: the gate stays UNREAD.
        setDispatchMode(null);
      }
    } catch {
      // The send gate stays UNREAD. The channel tiles fall back to "not
      // connected", which is only a display guess; the gate's caption is a
      // promise, so it must not be guessed. No toast on initial load.
      setDispatchMode(null);
    }

    // Load notification templates. The canonical set lives in the FE constants
    // (already the initial state); the DB stores per-template OVERRIDES
    // (is_enabled / edited content / subject) keyed by template_id. Overlay any
    // overrides onto the constant base so a partial save never shrinks the list.
    // GET returns { templates: [...] } (NOT a bare array — the old
    // Array.isArray(resp) guard always missed and silently fell back).
    try {
      const resp: any = await settingsApi.getNotificationTemplates();
      const rows: any[] = Array.isArray(resp?.templates)
        ? resp.templates
        : Array.isArray(resp)
          ? resp
          : [];
      if (rows.length > 0) {
        const overrides = new Map<string, any>(
          rows.map((t: any) => [t.template_id ?? t.id, t])
        );
        setTemplates((base) =>
          base.map((t) => {
            const o = overrides.get(t.id);
            if (!o) return t;
            return {
              ...t,
              subject: o.subject ?? t.subject,
              template: o.content ?? o.template ?? t.template,
              variables: o.variables ?? t.variables,
              isActive: Boolean(o.is_enabled ?? o.isActive ?? t.isActive),
            };
          })
        );
      }
    } catch {
      // Fall back to hardcoded NOTIFICATION_TEMPLATES already set as initial state
    }
  };


  const handleToggleTemplate = async (templateId: string) => {
    const current = templates.find((t) => t.id === templateId);
    if (!current) return;
    const nextActive = !current.isActive;

    // Optimistic: flip locally + keep the preview pane in sync.
    setTemplates((prev) =>
      prev.map((t) => (t.id === templateId ? { ...t, isActive: nextActive } : t))
    );
    setSelectedTemplate((s) =>
      s && s.id === templateId ? { ...s, isActive: nextActive } : s
    );

    try {
      // Backend NotificationTemplate model requires the full shape; send it.
      await settingsApi.updateNotificationTemplate(templateId, {
        template_id: templateId,
        template_type: current.channel,
        trigger_event: current.id,
        is_enabled: nextActive,
        subject: current.subject,
        content: current.template,
        variables: current.variables,
      });
      toast.success(`Template ${nextActive ? 'enabled' : 'disabled'}`);
    } catch (error: any) {
      // Revert so the UI never lies about persisted state.
      setTemplates((prev) =>
        prev.map((t) => (t.id === templateId ? { ...t, isActive: current.isActive } : t))
      );
      setSelectedTemplate((s) =>
        s && s.id === templateId ? { ...s, isActive: current.isActive } : s
      );
      toast.error(error?.message || 'Failed to update template');
    }
  };

  const handleTestNotification = async () => {
    if (!selectedTemplate || !testPhone) {
      toast.error('Please select a template and enter a phone number');
      return;
    }

    if (!providerConfig.isActive && !smsReady) {
      toast.error(
        'No messaging credentials yet. Add them in Settings -> Integrations -> WhatsApp Business (MSG91).'
      );
      return;
    }

    try {
      await settingsApi.testNotification(selectedTemplate.id, testPhone);
      toast.success(`Test ${selectedTemplate.channel} sent to ${testPhone}`);
    } catch (error: any) {
      toast.error(error?.message || 'Failed to send test notification');
    }
  };

  if (!canManageSettings) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <AlertCircle className="w-16 h-16 mx-auto text-gray-700 mb-4" />
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
          {/* Where credentials live. This screen deliberately has no key
              boxes: it shows what IS connected and sends you to the one
              place that sets it. */}
          <div className="card bg-blue-50 border-blue-200">
            <div className="flex gap-3">
              <MessageSquare className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-blue-900">
                <p className="font-medium mb-1">Messaging credentials live in Integrations</p>
                <p className="text-blue-800">
                  Better Vision sends WhatsApp and SMS through MSG91. The auth key,
                  WhatsApp number, SMS template ID and sender ID are entered once under{' '}
                  <Link to="/settings/integrations" className="underline font-medium">
                    Settings &rarr; Integrations &rarr; WhatsApp Business (MSG91)
                  </Link>
                  . Your MSG91 account must be DLT-registered for Indian telecom rules.
                </p>
              </div>
            </div>
          </div>

          {/* Live connection status */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Connection status</h3>
            <div className="space-y-3">
              <ChannelStatusRow
                label="WhatsApp"
                provider={providerConfig.provider}
                connected={providerConfig.isActive}
                detail={
                  providerConfig.isActive
                    ? `Sending from ${providerConfig.senderId || 'your MSG91 number'}`
                    : 'Auth key or WhatsApp number missing'
                }
              />
              <ChannelStatusRow
                label="SMS"
                provider={providerConfig.provider}
                connected={smsReady}
                detail={
                  smsReady
                    ? 'DLT template and sender ID are set'
                    : 'Auth key or SMS template ID missing'
                }
              />
            </div>

            {/* The mode is rendered VERBATIM only when it is one of the three
                values the server can legitimately report. Anything else --
                a padded " live" (invisible in HTML), an empty string, a
                malformed body stringified to "[object Object]" -- gets the
                honest unknown state: this screen must never print the
                "nothing is sent" assurance off a value it does not
                understand, because whether that value sends is decided by a
                Python default this file cannot see. */}
            <div className="mt-4 p-4 bg-gray-50 rounded-lg">
              <p className="font-medium text-gray-900">
                Sending mode:{' '}
                {dispatchMode === undefined ? (
                  <span className="text-gray-500">reading&hellip;</span>
                ) : modeIsKnown ? (
                  <span className="uppercase">{dispatchMode}</span>
                ) : dispatchMode === null ? (
                  <span className="text-amber-700">
                    couldn&rsquo;t be read &mdash; check with the server
                  </span>
                ) : (
                  <span className="text-amber-700">
                    unrecognised &mdash; check with the server
                  </span>
                )}
              </p>
              {dispatchMode === undefined ? null : modeIsKnown ? (
                <p className="text-sm text-gray-500 mt-1">
                  {dispatchMode === 'live'
                    ? 'Messages go to real customers.'
                    : dispatchMode === 'test'
                      ? 'Messages go only to the single test number set on the server.'
                      : 'Nothing is sent to customers yet. Messages are logged only.'}{' '}
                  This switch is set on the server, on purpose, so it can never be
                  flipped by accident from a screen.
                </p>
              ) : dispatchMode === null ? (
                <p className="text-sm text-gray-500 mt-1">
                  This screen could not reach the server, so it cannot tell you
                  whether messages are going out. Do not assume they are not.
                </p>
              ) : (
                <p className="text-sm text-gray-500 mt-1">
                  The server reported a sending mode this screen does not
                  recognise, so it cannot tell you whether messages are going
                  out. Do not assume they are not.
                </p>
              )}
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
              const categoryTemplates = templates.filter((t) => t.category === category);
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
