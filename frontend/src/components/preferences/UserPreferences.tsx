// ============================================================================
// IMS 2.0 - User Preferences Manager
// ============================================================================
// Save and manage user preferences including dashboard layouts

import { useState, useCallback } from 'react';
import { Save, RotateCcw, Layout, Settings as SettingsIcon } from 'lucide-react';
import clsx from 'clsx';

export interface DashboardLayout {
  id: string;
  name: string;
  widgets: {
    id: string;
    type: string;
    position: { row: number; col: number };
    size: { width: number; height: number };
    settings?: Record<string, any>;
  }[];
  isDefault?: boolean;
}

export interface UserPreferences {
  userId: string;
  theme: 'light' | 'dark' | 'system';
  dashboardLayout: DashboardLayout;
  savedLayouts: DashboardLayout[];
  compactView: boolean;
  notifications: {
    email: boolean;
    browser: boolean;
    critical: boolean;
    muted: string[]; // notification IDs to mute
  };
  dataRetention: number; // days
  timezone: string;
  dateFormat: string;
  customSettings: Record<string, any>;
}

/**
 * User preferences hook
 */
export function useUserPreferences(userId: string) {
  const storageKey = `user_preferences_${userId}`;

  const [preferences, setPreferencesState] = useState<UserPreferences>(() => {
    const stored = localStorage.getItem(storageKey);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch (error) {
        console.error('Failed to load user preferences:', error);
      }
    }

    return getDefaultPreferences(userId);
  });

  const setPreferences = useCallback((newPreferences: UserPreferences) => {
    setPreferencesState(newPreferences);
    localStorage.setItem(storageKey, JSON.stringify(newPreferences));
  }, [storageKey]);

  const updatePreference = useCallback(<K extends keyof UserPreferences>(
    key: K,
    value: UserPreferences[K]
  ) => {
    setPreferences({ ...preferences, [key]: value });
  }, [preferences, setPreferences]);

  const saveDashboardLayout = useCallback((layout: DashboardLayout) => {
    const updatedLayouts = preferences.savedLayouts.filter(l => l.id !== layout.id);
    setPreferences({
      ...preferences,
      dashboardLayout: layout,
      savedLayouts: [layout, ...updatedLayouts],
    });
  }, [preferences, setPreferences]);

  const loadDashboardLayout = useCallback((layoutId: string) => {
    const layout = preferences.savedLayouts.find(l => l.id === layoutId);
    if (layout) {
      setPreferences({
        ...preferences,
        dashboardLayout: layout,
      });
    }
  }, [preferences, setPreferences]);

  const deleteDashboardLayout = useCallback((layoutId: string) => {
    setPreferences({
      ...preferences,
      savedLayouts: preferences.savedLayouts.filter(l => l.id !== layoutId),
    });
  }, [preferences, setPreferences]);

  const resetToDefaults = useCallback(() => {
    setPreferences(getDefaultPreferences(userId));
  }, [userId, setPreferences]);

  return {
    preferences,
    setPreferences,
    updatePreference,
    saveDashboardLayout,
    loadDashboardLayout,
    deleteDashboardLayout,
    resetToDefaults,
  };
}

function getDefaultPreferences(userId: string): UserPreferences {
  return {
    userId,
    theme: 'system',
    dashboardLayout: {
      id: 'default',
      name: 'Default',
      widgets: [],
      isDefault: true,
    },
    savedLayouts: [],
    compactView: false,
    notifications: {
      email: true,
      browser: true,
      critical: true,
      muted: [],
    },
    dataRetention: 90,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    dateFormat: 'MM/DD/YYYY',
    customSettings: {},
  };
}

/**
 * Preferences panel component
 */
interface UserPreferencesPanelProps {
  preferences: UserPreferences;
  onUpdate: <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => void;
  onReset: () => void;
  onSaveDashboardLayout: (layout: DashboardLayout) => void;
  onLoadDashboardLayout: (layoutId: string) => void;
  onDeleteDashboardLayout: (layoutId: string) => void;
  loading?: boolean;
}

export function UserPreferencesPanel({
  preferences,
  onUpdate,
  onReset,
  onSaveDashboardLayout,
  onLoadDashboardLayout,
  onDeleteDashboardLayout,
  loading = false,
}: UserPreferencesPanelProps) {
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [layoutName, setLayoutName] = useState('');
  const [activeTab, setActiveTab] = useState<'general' | 'notifications' | 'dashboards'>('general');

  const tabs = [
    { id: 'general', label: 'General', icon: SettingsIcon },
    { id: 'notifications', label: 'Notifications', icon: 'bell' },
    { id: 'dashboards', label: 'Dashboards', icon: Layout },
  ] as const;

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Tabs */}
      <div className="flex border-b border-gray-200 dark:border-gray-800">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={clsx(
              'flex-1 px-4 py-3 text-sm font-medium transition-colors',
              activeTab === tab.id
                ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600 dark:border-blue-400'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-300'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="p-6 space-y-6">
        {activeTab === 'general' && (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Theme
              </label>
              <select
                value={preferences.theme}
                onChange={e => onUpdate('theme', e.target.value as any)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              >
                <option value="light">Light</option>
                <option value="dark">Dark</option>
                <option value="system">System</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Timezone
              </label>
              <select
                value={preferences.timezone}
                onChange={e => onUpdate('timezone', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              >
                {Intl.supportedValuesOf('timeZone').map(tz => (
                  <option key={tz} value={tz}>
                    {tz}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Date Format
              </label>
              <select
                value={preferences.dateFormat}
                onChange={e => onUpdate('dateFormat', e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              >
                <option value="MM/DD/YYYY">MM/DD/YYYY</option>
                <option value="DD/MM/YYYY">DD/MM/YYYY</option>
                <option value="YYYY-MM-DD">YYYY-MM-DD</option>
              </select>
            </div>

            <div>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={preferences.compactView}
                  onChange={e => onUpdate('compactView', e.target.checked)}
                  className="w-4 h-4 rounded text-blue-600"
                />
                <span className="text-sm text-gray-700 dark:text-gray-300">
                  Compact view
                </span>
              </label>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Data Retention (days)
              </label>
              <input
                type="number"
                value={preferences.dataRetention}
                onChange={e => onUpdate('dataRetention', parseInt(e.target.value))}
                min="1"
                max="365"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
            </div>
          </div>
        )}

        {activeTab === 'notifications' && (
          <div className="space-y-4">
            <div>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={preferences.notifications.email}
                  onChange={e => onUpdate('notifications', {
                    ...preferences.notifications,
                    email: e.target.checked,
                  })}
                  className="w-4 h-4 rounded text-blue-600"
                />
                <span className="text-sm text-gray-700 dark:text-gray-300">
                  Email notifications
                </span>
              </label>
            </div>

            <div>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={preferences.notifications.browser}
                  onChange={e => onUpdate('notifications', {
                    ...preferences.notifications,
                    browser: e.target.checked,
                  })}
                  className="w-4 h-4 rounded text-blue-600"
                />
                <span className="text-sm text-gray-700 dark:text-gray-300">
                  Browser notifications
                </span>
              </label>
            </div>

            <div>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={preferences.notifications.critical}
                  onChange={e => onUpdate('notifications', {
                    ...preferences.notifications,
                    critical: e.target.checked,
                  })}
                  className="w-4 h-4 rounded text-blue-600"
                />
                <span className="text-sm text-gray-700 dark:text-gray-300">
                  Critical alerts only
                </span>
              </label>
            </div>
          </div>
        )}

        {activeTab === 'dashboards' && (
          <div className="space-y-4">
            <button
              onClick={() => setSaveModalOpen(true)}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
              disabled={loading}
            >
              <Save className="w-4 h-4" />
              Save Current Layout
            </button>

            <div className="space-y-2">
              <h3 className="font-medium text-gray-900 dark:text-white">Saved Layouts</h3>
              {preferences.savedLayouts.length === 0 ? (
                <p className="text-sm text-gray-500 dark:text-gray-400">No saved layouts</p>
              ) : (
                <div className="space-y-2">
                  {preferences.savedLayouts.map(layout => (
                    <div
                      key={layout.id}
                      className={clsx(
                        'p-3 border rounded-lg cursor-pointer transition-colors',
                        preferences.dashboardLayout.id === layout.id
                          ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
                          : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <button
                          onClick={() => onLoadDashboardLayout(layout.id)}
                          className="text-left flex-1"
                        >
                          <p className="font-medium text-gray-900 dark:text-white">
                            {layout.name}
                          </p>
                          <p className="text-xs text-gray-500 dark:text-gray-400">
                            {layout.widgets.length} widgets
                          </p>
                        </button>
                        <button
                          onClick={() => onDeleteDashboardLayout(layout.id)}
                          className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded text-red-600 transition-colors"
                          aria-label="Delete layout"
                        >
                          ✕
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex gap-3 p-6 border-t border-gray-200 dark:border-gray-800">
        <button
          onClick={onReset}
          className="flex items-center justify-center gap-2 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors font-medium"
        >
          <RotateCcw className="w-4 h-4" />
          Reset to Defaults
        </button>
      </div>

      {/* Save Layout Modal */}
      {saveModalOpen && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
          onClick={() => setSaveModalOpen(false)}
        >
          <div
            className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-sm w-full"
            onClick={e => e.stopPropagation()}
          >
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              Save Layout
            </h2>
            <input
              type="text"
              value={layoutName}
              onChange={e => setLayoutName(e.target.value)}
              placeholder="Layout name"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white mb-4"
              onKeyPress={e => {
                if (e.key === 'Enter' && layoutName.trim()) {
                  onSaveDashboardLayout({
                    id: Date.now().toString(),
                    name: layoutName,
                    widgets: preferences.dashboardLayout.widgets,
                  });
                  setLayoutName('');
                  setSaveModalOpen(false);
                }
              }}
              autoFocus
            />
            <div className="flex gap-2">
              <button
                onClick={() => setSaveModalOpen(false)}
                className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (layoutName.trim()) {
                    onSaveDashboardLayout({
                      id: Date.now().toString(),
                      name: layoutName,
                      widgets: preferences.dashboardLayout.widgets,
                    });
                    setLayoutName('');
                    setSaveModalOpen(false);
                  }
                }}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
