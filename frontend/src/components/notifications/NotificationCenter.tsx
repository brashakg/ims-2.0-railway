// ============================================================================
// IMS 2.0 - Notification Center
// ============================================================================
// Real-time notifications with toast alerts and notification panel

import { useState, useCallback } from 'react';
import { AlertCircle, CheckCircle2, Info, AlertTriangle, X, Bell } from 'lucide-react';
import clsx from 'clsx';

export type NotificationType = 'success' | 'error' | 'warning' | 'info';
export type NotificationPriority = 'low' | 'normal' | 'high' | 'critical';

export interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  message: string;
  priority: NotificationPriority;
  timestamp: number;
  read: boolean;
  action?: {
    label: string;
    onClick: () => void;
  };
  dismissible?: boolean;
}

interface NotificationCenterProps {
  notifications: Notification[];
  onDismiss: (id: string) => void;
  onMarkAsRead: (id: string) => void;
  onClearAll: () => void;
}

const typeConfig = {
  success: {
    icon: CheckCircle2,
    bgColor: 'bg-green-50 dark:bg-green-900/20',
    borderColor: 'border-green-200 dark:border-green-800',
    titleColor: 'text-green-900 dark:text-green-300',
    messageColor: 'text-green-700 dark:text-green-200',
    iconColor: 'text-green-600 dark:text-green-400',
  },
  error: {
    icon: AlertCircle,
    bgColor: 'bg-red-50 dark:bg-red-900/20',
    borderColor: 'border-red-200 dark:border-red-800',
    titleColor: 'text-red-900 dark:text-red-300',
    messageColor: 'text-red-700 dark:text-red-200',
    iconColor: 'text-red-600 dark:text-red-400',
  },
  warning: {
    icon: AlertTriangle,
    bgColor: 'bg-amber-50 dark:bg-amber-900/20',
    borderColor: 'border-amber-200 dark:border-amber-800',
    titleColor: 'text-amber-900 dark:text-amber-300',
    messageColor: 'text-amber-700 dark:text-amber-200',
    iconColor: 'text-amber-600 dark:text-amber-400',
  },
  info: {
    icon: Info,
    bgColor: 'bg-blue-50 dark:bg-blue-900/20',
    borderColor: 'border-blue-200 dark:border-blue-800',
    titleColor: 'text-blue-900 dark:text-blue-300',
    messageColor: 'text-blue-700 dark:text-blue-200',
    iconColor: 'text-blue-600 dark:text-blue-400',
  },
};

/**
 * Toast notification (single notification display)
 */
interface ToastProps {
  notification: Notification;
  onDismiss: (id: string) => void;
  onMarkAsRead: (id: string) => void;
}

export function Toast({ notification, onDismiss, onMarkAsRead }: ToastProps) {
  const config = typeConfig[notification.type];
  const Icon = config.icon;

  return (
    <div
      className={clsx(
        'fixed bottom-4 right-4 max-w-sm w-full rounded-lg border shadow-lg animate-in fade-in slide-in-from-right-4 z-50',
        config.bgColor,
        config.borderColor
      )}
      role="alert"
      aria-live={notification.priority === 'critical' ? 'assertive' : 'polite'}
    >
      <div className="flex gap-3 p-4">
        <Icon className={clsx('w-5 h-5 flex-shrink-0 mt-0.5', config.iconColor)} />
        <div className="flex-1">
          <h3 className={clsx('font-semibold', config.titleColor)}>
            {notification.title}
          </h3>
          {notification.message && (
            <p className={clsx('text-sm mt-1', config.messageColor)}>
              {notification.message}
            </p>
          )}
          {notification.action && (
            <button
              onClick={() => {
                notification.action?.onClick();
                onDismiss(notification.id);
              }}
              className={clsx(
                'text-xs font-medium mt-2 underline hover:no-underline',
                config.titleColor
              )}
            >
              {notification.action?.label}
            </button>
          )}
        </div>
        {notification.dismissible !== false && (
          <button
            onClick={() => {
              onMarkAsRead(notification.id);
              onDismiss(notification.id);
            }}
            className={clsx('p-1 hover:bg-white/20 rounded transition-colors', config.titleColor)}
            aria-label="Dismiss notification"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Notification center panel
 */
export function NotificationPanel({
  notifications,
  onDismiss,
  onMarkAsRead,
  onClearAll,
}: NotificationCenterProps) {
  const unreadCount = notifications.filter(n => !n.read).length;
  const prioritizedNotifications = notifications.sort((a, b) => {
    const priorityOrder = { critical: 0, high: 1, normal: 2, low: 3 };
    if (priorityOrder[a.priority] !== priorityOrder[b.priority]) {
      return priorityOrder[a.priority] - priorityOrder[b.priority];
    }
    return b.timestamp - a.timestamp;
  });

  return (
    <div className="max-w-md w-full">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 rounded-t-lg">
        <div className="flex items-center gap-2">
          <Bell className="w-5 h-5 text-gray-600 dark:text-gray-400" />
          <h2 className="font-bold text-gray-900 dark:text-white">
            Notifications
            {unreadCount > 0 && (
              <span className="ml-2 bg-red-600 text-white text-xs rounded-full px-2 py-0.5">
                {unreadCount}
              </span>
            )}
          </h2>
        </div>
        {notifications.length > 0 && (
          <button
            onClick={onClearAll}
            className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
          >
            Clear all
          </button>
        )}
      </div>

      {/* Notifications List */}
      <div className="max-h-96 overflow-y-auto bg-white dark:bg-gray-900">
        {prioritizedNotifications.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <Bell className="w-8 h-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm">No notifications</p>
          </div>
        ) : (
          <div className="divide-y divide-gray-200 dark:divide-gray-800">
            {prioritizedNotifications.map(notification => {
              const config = typeConfig[notification.type];
              const Icon = config.icon;

              return (
                <button
                  key={notification.id}
                  onClick={() => onMarkAsRead(notification.id)}
                  className={clsx(
                    'w-full text-left p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors border-l-4',
                    notification.read
                      ? 'border-transparent opacity-75'
                      : clsx('border-l-4', {
                        'border-green-500': notification.type === 'success',
                        'border-red-500': notification.type === 'error',
                        'border-amber-500': notification.type === 'warning',
                        'border-blue-500': notification.type === 'info',
                      })
                  )}
                >
                  <div className="flex gap-3">
                    <Icon className={clsx('w-5 h-5 flex-shrink-0 mt-0.5', config.iconColor)} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between gap-2">
                        <h3 className={clsx('font-semibold text-sm', config.titleColor)}>
                          {notification.title}
                        </h3>
                        <span className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">
                          {formatTime(notification.timestamp)}
                        </span>
                      </div>
                      {notification.message && (
                        <p className={clsx('text-xs mt-1 line-clamp-2', config.messageColor)}>
                          {notification.message}
                        </p>
                      )}
                      <div className="flex items-center justify-between mt-2">
                        {notification.action && (
                          <button
                            onClick={e => {
                              e.stopPropagation();
                              notification.action?.onClick();
                            }}
                            className={clsx('text-xs font-medium underline hover:no-underline', config.titleColor)}
                          >
                            {notification.action?.label}
                          </button>
                        )}
                        <button
                          onClick={e => {
                            e.stopPropagation();
                            onDismiss(notification.id);
                          }}
                          className="p-1 hover:bg-gray-200 dark:hover:bg-gray-700 rounded text-gray-400 transition-colors"
                          aria-label="Dismiss"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer */}
      {prioritizedNotifications.length > 0 && (
        <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/50 rounded-b-lg text-xs text-gray-600 dark:text-gray-400">
          {prioritizedNotifications.length} notification{prioritizedNotifications.length !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  );
}

/**
 * Notification center hook for managing notifications
 */
export function useNotifications(maxNotifications = 50) {
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const addNotification = useCallback((
    notification: Omit<Notification, 'id' | 'timestamp' | 'read'>
  ) => {
    const id = Date.now().toString();
    const newNotification: Notification = {
      ...notification,
      id,
      timestamp: Date.now(),
      read: false,
    };

    setNotifications(prev => {
      const updated = [newNotification, ...prev];
      return updated.slice(0, maxNotifications);
    });

    // Auto-dismiss non-critical notifications after 6 seconds
    if (notification.priority !== 'critical') {
      const timer = setTimeout(() => {
        dismissNotification(id);
      }, 6000);

      return () => clearTimeout(timer);
    }
  }, [maxNotifications]);

  const dismissNotification = useCallback((id: string) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  }, []);

  const markAsRead = useCallback((id: string) => {
    setNotifications(prev =>
      prev.map(n => (n.id === id ? { ...n, read: true } : n))
    );
  }, []);

  const clearAll = useCallback(() => {
    setNotifications([]);
  }, []);

  return {
    notifications,
    addNotification,
    dismissNotification,
    markAsRead,
    clearAll,
  };
}

function formatTime(timestamp: number): string {
  const now = Date.now();
  const diff = now - timestamp;
  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (seconds < 60) return 'now';
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 7) return `${days}d ago`;

  return new Date(timestamp).toLocaleDateString();
}
