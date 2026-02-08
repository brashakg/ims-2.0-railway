// ============================================================================
// IMS 2.0 - Enterprise Integrations
// ============================================================================
// Scheduled tasks, API keys, and webhooks management

import { useState } from 'react';
import { Plus, Copy, Trash2, Edit2, Clock, Key, Zap, Play, Pause } from 'lucide-react';
import clsx from 'clsx';

// ============================================================================
// Feature #18: Scheduled Tasks
// ============================================================================

export interface ScheduledTask {
  id: string;
  name: string;
  description?: string;
  type: 'export' | 'report' | 'cleanup' | 'sync' | 'custom';
  schedule: {
    frequency: 'daily' | 'weekly' | 'monthly' | 'hourly';
    time?: string;
    dayOfWeek?: number;
    dayOfMonth?: number;
  };
  enabled: boolean;
  lastRun?: string;
  nextRun?: string;
  status: 'success' | 'failed' | 'pending';
  metadata?: Record<string, any>;
}

interface ScheduledTasksProps {
  tasks: ScheduledTask[];
  onCreateTask: (task: Omit<ScheduledTask, 'id' | 'lastRun' | 'status'>) => Promise<void>;
  onDeleteTask: (id: string) => Promise<void>;
  onRunNow: (id: string) => Promise<void>;
  onToggleTask: (id: string, enabled: boolean) => Promise<void>;
}

export function ScheduledTasks({
  tasks,
  onCreateTask,
  onDeleteTask,
  onRunNow,
  onToggleTask,
}: ScheduledTasksProps) {
  const [showModal, setShowModal] = useState(false);
  const [taskName, setTaskName] = useState('');
  const [frequency, setFrequency] = useState<'daily' | 'weekly' | 'monthly' | 'hourly'>('daily');
  const [taskType, setTaskType] = useState<'export' | 'report' | 'cleanup' | 'sync' | 'custom'>('export');

  const handleCreate = async () => {
    if (!taskName.trim()) return;
    await Promise.resolve(onCreateTask({
      name: taskName,
      type: taskType,
      schedule: { frequency },
      enabled: true,
    }));
    setTaskName('');
    setShowModal(false);
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Clock className="w-5 h-5 text-gray-600 dark:text-gray-400" />
          <div>
            <h3 className="font-bold text-gray-900 dark:text-white">Scheduled Tasks</h3>
            <p className="text-sm text-gray-600 dark:text-gray-400">Automate exports, reports, and cleanup</p>
          </div>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          Schedule Task
        </button>
      </div>

      {/* Tasks List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {tasks.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <Clock className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No scheduled tasks yet</p>
          </div>
        ) : (
          tasks.map(task => (
            <div key={task.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-center justify-between gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h4 className="font-medium text-gray-900 dark:text-white">
                      {task.name}
                    </h4>
                    <span className={clsx('text-xs px-2 py-1 rounded', {
                      'bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-400': task.enabled,
                      'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400': !task.enabled,
                    })}>
                      {task.enabled ? 'Active' : 'Disabled'}
                    </span>
                  </div>
                  <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">
                    {task.schedule.frequency} • {task.type}
                  </p>
                  {task.nextRun && (
                    <p className="text-xs text-gray-500 dark:text-gray-500 mt-1">
                      Next run: {new Date(task.nextRun).toLocaleString()}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => onToggleTask(task.id, !task.enabled)}
                    className="p-2 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors"
                    title={task.enabled ? 'Disable' : 'Enable'}
                  >
                    {task.enabled ? (
                      <Pause className="w-4 h-4 text-amber-600" />
                    ) : (
                      <Play className="w-4 h-4 text-gray-600 dark:text-gray-400" />
                    )}
                  </button>
                  <button
                    onClick={() => onRunNow(task.id)}
                    className="p-2 hover:bg-blue-100 dark:hover:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400"
                    title="Run now"
                  >
                    <Play className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => onDeleteTask(task.id)}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-sm w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">Schedule Task</h2>
            <div className="space-y-4">
              <input
                type="text"
                value={taskName}
                onChange={e => setTaskName(e.target.value)}
                placeholder="Task name"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <select
                value={taskType}
                onChange={e => setTaskType(e.target.value as any)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              >
                <option value="export">Export</option>
                <option value="report">Report</option>
                <option value="cleanup">Cleanup</option>
                <option value="sync">Sync</option>
              </select>
              <select
                value={frequency}
                onChange={e => setFrequency(e.target.value as any)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              >
                <option value="hourly">Hourly</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>
            <div className="flex gap-2 mt-6">
              <button onClick={() => setShowModal(false)} className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg">Cancel</button>
              <button onClick={handleCreate} className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Feature #19: API Keys
// ============================================================================

export interface APIKey {
  id: string;
  name: string;
  key: string;
  maskedKey: string;
  permissions: string[];
  createdAt: string;
  lastUsedAt?: string;
  expiresAt?: string;
  active: boolean;
}

interface APIKeysProps {
  keys: APIKey[];
  onCreateKey: (name: string, permissions: string[]) => Promise<APIKey>;
  onRevokeKey: (id: string) => Promise<void>;
  onRegenerate: (id: string) => Promise<APIKey>;
}

export function APIKeysManager({
  keys,
  onCreateKey,
  onRevokeKey,
  onRegenerate,
}: APIKeysProps) {
  const [showModal, setShowModal] = useState(false);
  const [keyName, setKeyName] = useState('');
  const [selectedPermissions, setSelectedPermissions] = useState<string[]>(['read']);

  const permissions = ['read', 'write', 'delete', 'admin'];

  const handleCreate = async () => {
    if (!keyName.trim()) return;
    await Promise.resolve(onCreateKey(keyName, selectedPermissions));
    setKeyName('');
    setSelectedPermissions(['read']);
    setShowModal(false);
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Key className="w-5 h-5 text-gray-600 dark:text-gray-400" />
          <div>
            <h3 className="font-bold text-gray-900 dark:text-white">API Keys</h3>
            <p className="text-sm text-gray-600 dark:text-gray-400">Manage third-party integrations</p>
          </div>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New API Key
        </button>
      </div>

      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {keys.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <Key className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No API keys yet</p>
          </div>
        ) : (
          keys.map(key => (
            <div key={key.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <h4 className="font-medium text-gray-900 dark:text-white">
                    {key.name}
                  </h4>
                  <p className="font-mono text-xs text-gray-600 dark:text-gray-400 mt-1">
                    {key.maskedKey}
                  </p>
                  <div className="flex items-center gap-2 mt-2">
                    {key.permissions.map(perm => (
                      <span key={perm} className="text-xs px-2 py-1 bg-gray-100 dark:bg-gray-800 rounded text-gray-700 dark:text-gray-300">
                        {perm}
                      </span>
                    ))}
                  </div>
                  {key.lastUsedAt && (
                    <p className="text-xs text-gray-500 dark:text-gray-500 mt-1">
                      Last used: {new Date(key.lastUsedAt).toLocaleDateString()}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => navigator.clipboard.writeText(key.maskedKey)}
                    className="p-2 hover:bg-blue-100 dark:hover:bg-blue-900/20 rounded-lg text-blue-600 dark:text-blue-400"
                    title="Copy"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => onRegenerate(key.id)}
                    className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg text-amber-600 dark:text-amber-400"
                    title="Regenerate"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => onRevokeKey(key.id)}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                    title="Revoke"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-sm w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">Create API Key</h2>
            <div className="space-y-4">
              <input
                type="text"
                value={keyName}
                onChange={e => setKeyName(e.target.value)}
                placeholder="Key name"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <div>
                <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Permissions</p>
                <div className="space-y-2">
                  {permissions.map(perm => (
                    <label key={perm} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedPermissions.includes(perm)}
                        onChange={e => {
                          if (e.target.checked) {
                            setSelectedPermissions([...selectedPermissions, perm]);
                          } else {
                            setSelectedPermissions(selectedPermissions.filter(p => p !== perm));
                          }
                        }}
                        className="w-4 h-4 rounded"
                      />
                      <span className="text-sm text-gray-700 dark:text-gray-300 capitalize">
                        {perm}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex gap-2 mt-6">
              <button onClick={() => setShowModal(false)} className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg">Cancel</button>
              <button onClick={handleCreate} className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Feature #20: Webhooks
// ============================================================================

export interface Webhook {
  id: string;
  name: string;
  url: string;
  events: string[];
  active: boolean;
  createdAt: string;
  lastTriggeredAt?: string;
  secretKey: string;
}

interface WebhooksProps {
  webhooks: Webhook[];
  onCreateWebhook: (webhook: Omit<Webhook, 'id' | 'createdAt' | 'secretKey'>) => Promise<Webhook>;
  onDeleteWebhook: (id: string) => Promise<void>;
  onTest: (id: string) => Promise<void>;
}

export function WebhooksManager({
  webhooks,
  onCreateWebhook,
  onDeleteWebhook,
  onTest,
}: WebhooksProps) {
  const [showModal, setShowModal] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState('');
  const [webhookName, setWebhookName] = useState('');
  const [selectedEvents, setSelectedEvents] = useState<string[]>(['order.created']);

  const availableEvents = ['order.created', 'order.updated', 'product.updated', 'customer.created', 'payment.completed'];

  const handleCreate = async () => {
    if (!webhookUrl.trim() || !webhookName.trim()) return;
    await Promise.resolve(onCreateWebhook({
      name: webhookName,
      url: webhookUrl,
      events: selectedEvents,
      active: true,
    }));
    setWebhookUrl('');
    setWebhookName('');
    setSelectedEvents(['order.created']);
    setShowModal(false);
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Zap className="w-5 h-5 text-gray-600 dark:text-gray-400" />
          <div>
            <h3 className="font-bold text-gray-900 dark:text-white">Webhooks</h3>
            <p className="text-sm text-gray-600 dark:text-gray-400">Real-time data synchronization</p>
          </div>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" />
          New Webhook
        </button>
      </div>

      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {webhooks.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <Zap className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No webhooks yet</p>
          </div>
        ) : (
          webhooks.map(webhook => (
            <div key={webhook.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <h4 className="font-medium text-gray-900 dark:text-white">
                    {webhook.name}
                  </h4>
                  <p className="text-xs text-gray-600 dark:text-gray-400 mt-1 break-all">
                    {webhook.url}
                  </p>
                  <div className="flex items-center gap-1 mt-2 flex-wrap">
                    {webhook.events.map(event => (
                      <span key={event} className="text-xs px-2 py-1 bg-gray-100 dark:bg-gray-800 rounded text-gray-700 dark:text-gray-300">
                        {event}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => onTest(webhook.id)}
                    className="p-2 hover:bg-green-100 dark:hover:bg-green-900/20 rounded-lg text-green-600 dark:text-green-400"
                    title="Test webhook"
                  >
                    <Zap className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => onDeleteWebhook(webhook.id)}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-sm w-full max-h-96 overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">Create Webhook</h2>
            <div className="space-y-4">
              <input
                type="text"
                value={webhookName}
                onChange={e => setWebhookName(e.target.value)}
                placeholder="Webhook name"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <input
                type="url"
                value={webhookUrl}
                onChange={e => setWebhookUrl(e.target.value)}
                placeholder="https://example.com/webhook"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
              <div>
                <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Events</p>
                <div className="space-y-2 max-h-32 overflow-y-auto">
                  {availableEvents.map(event => (
                    <label key={event} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedEvents.includes(event)}
                        onChange={e => {
                          if (e.target.checked) {
                            setSelectedEvents([...selectedEvents, event]);
                          } else {
                            setSelectedEvents(selectedEvents.filter(ev => ev !== event));
                          }
                        }}
                        className="w-4 h-4 rounded"
                      />
                      <span className="text-sm text-gray-700 dark:text-gray-300">
                        {event}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex gap-2 mt-6">
              <button onClick={() => setShowModal(false)} className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg">Cancel</button>
              <button onClick={handleCreate} className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
