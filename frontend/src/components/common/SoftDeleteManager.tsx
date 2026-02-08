// ============================================================================
// IMS 2.0 - Soft Delete Manager with Change History
// ============================================================================
// Features #14 & #15: Soft deletes and change history tracking

import { useState } from 'react';
import { Trash2, RotateCcw, History, User, Clock } from 'lucide-react';
import clsx from 'clsx';

export interface ChangeRecord {
  id: string;
  timestamp: string;
  userId: string;
  username: string;
  action: 'created' | 'updated' | 'deleted' | 'restored';
  field?: string;
  oldValue?: any;
  newValue?: any;
  reason?: string;
}

export interface SoftDeletedItem {
  id: string;
  name: string;
  deletedAt: string;
  deletedBy: string;
  reason?: string;
  changes?: ChangeRecord[];
}

interface SoftDeleteManagerProps {
  items: SoftDeletedItem[];
  onRestore: (id: string) => Promise<void> | void;
  onPermanentDelete: (id: string) => Promise<void> | void;
  onShowHistory: (id: string) => void;
  loading?: boolean;
}

export function SoftDeleteManager({
  items,
  onRestore,
  onPermanentDelete,
  onShowHistory,
  loading = false,
}: SoftDeleteManagerProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const handleRestore = async (id: string) => {
    setActionLoading(id);
    try {
      await Promise.resolve(onRestore(id));
      setExpandedId(null);
    } finally {
      setActionLoading(null);
    }
  };

  const handlePermanentDelete = async (id: string) => {
    setActionLoading(id);
    try {
      await Promise.resolve(onPermanentDelete(id));
      setConfirmDelete(null);
      setExpandedId(null);
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-4">
        <div className="animate-pulse space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-16 bg-gray-100 dark:bg-gray-800 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-8 text-center">
        <Trash2 className="w-12 h-12 text-gray-300 dark:text-gray-700 mx-auto mb-3" />
        <p className="text-gray-600 dark:text-gray-400">No deleted items found</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {items.map(item => (
        <div key={item.id} className="bg-white dark:bg-gray-900 rounded-lg border border-red-200 dark:border-red-800">
          {/* Item Header */}
          <button
            onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
            className="w-full p-4 text-left hover:bg-red-50 dark:hover:bg-red-900/10 transition-colors"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1">
                <h3 className="font-medium text-gray-900 dark:text-white line-through opacity-75">
                  {item.name}
                </h3>
                <div className="flex items-center gap-4 mt-2 text-xs text-gray-600 dark:text-gray-400">
                  <div className="flex items-center gap-1">
                    <User className="w-3 h-3" />
                    <span>Deleted by {item.deletedBy}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    <span>{new Date(item.deletedAt).toLocaleString()}</span>
                  </div>
                </div>
                {item.reason && (
                  <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">
                    Reason: {item.reason}
                  </p>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={e => {
                    e.stopPropagation();
                    onShowHistory(item.id);
                  }}
                  className="p-2 hover:bg-blue-100 dark:hover:bg-blue-900/20 rounded-lg transition-colors text-blue-600"
                  title="View history"
                  aria-label="View history"
                >
                  <History className="w-4 h-4" />
                </button>
              </div>
            </div>
          </button>

          {/* Expanded Details */}
          {expandedId === item.id && (
            <div className="border-t border-red-200 dark:border-red-800 p-4 bg-red-50/50 dark:bg-red-900/5">
              {/* Change History */}
              {item.changes && item.changes.length > 0 && (
                <div className="mb-4 pb-4 border-b border-red-200 dark:border-red-800">
                  <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">
                    Change History
                  </h4>
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {item.changes.map((change, i) => (
                      <div key={i} className="text-xs bg-white dark:bg-gray-800 p-2 rounded border border-gray-200 dark:border-gray-700">
                        <p className="font-medium text-gray-900 dark:text-white">
                          {change.action} by {change.username}
                        </p>
                        <p className="text-gray-600 dark:text-gray-400">
                          {new Date(change.timestamp).toLocaleString()}
                        </p>
                        {change.field && (
                          <p className="text-gray-700 dark:text-gray-300 mt-1">
                            <strong>{change.field}:</strong> {String(change.oldValue)} → {String(change.newValue)}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Actions */}
              <div className="flex gap-2">
                <button
                  onClick={() => handleRestore(item.id)}
                  disabled={actionLoading === item.id}
                  className={clsx(
                    'flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors',
                    actionLoading === item.id
                      ? 'opacity-50 cursor-not-allowed'
                      : 'bg-green-600 text-white hover:bg-green-700'
                  )}
                >
                  <RotateCcw className="w-4 h-4" />
                  {actionLoading === item.id ? 'Restoring...' : 'Restore'}
                </button>
                <button
                  onClick={() => setConfirmDelete(item.id)}
                  className="flex-1 px-4 py-2 border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 rounded-lg font-medium hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                >
                  Permanently Delete
                </button>
              </div>
            </div>
          )}

          {/* Confirmation Modal */}
          {confirmDelete === item.id && (
            <div
              className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
              onClick={() => setConfirmDelete(null)}
            >
              <div
                className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-sm w-full"
                onClick={e => e.stopPropagation()}
              >
                <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-2">
                  Permanently Delete Item?
                </h2>
                <p className="text-gray-600 dark:text-gray-400 mb-6">
                  This action cannot be undone. The item <strong>{item.name}</strong> will be permanently deleted from the database.
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={() => setConfirmDelete(null)}
                    className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors font-medium"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => handlePermanentDelete(item.id)}
                    disabled={actionLoading === item.id}
                    className={clsx(
                      'flex-1 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors font-medium',
                      actionLoading === item.id && 'opacity-50 cursor-not-allowed'
                    )}
                  >
                    {actionLoading === item.id ? 'Deleting...' : 'Delete Permanently'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Change history timeline component
 */
interface ChangeHistoryProps {
  changes: ChangeRecord[];
  title?: string;
}

export function ChangeHistory({ changes, title = 'Change History' }: ChangeHistoryProps) {
  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-4">
      <h3 className="text-lg font-bold text-gray-900 dark:text-white mb-4">{title}</h3>
      <div className="space-y-4">
        {changes.length === 0 ? (
          <p className="text-gray-500 dark:text-gray-400 text-sm">No changes recorded</p>
        ) : (
          <div className="relative">
            {/* Timeline Line */}
            <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-gray-200 dark:bg-gray-700" />

            {/* Timeline Items */}
            <div className="space-y-6 pl-16">
              {changes.map((change, i) => {
                const actionColors = {
                  created: 'bg-green-100 text-green-700 dark:bg-green-900/20 dark:text-green-400',
                  updated: 'bg-blue-100 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400',
                  deleted: 'bg-red-100 text-red-700 dark:bg-red-900/20 dark:text-red-400',
                  restored: 'bg-purple-100 text-purple-700 dark:bg-purple-900/20 dark:text-purple-400',
                };

                return (
                  <div key={i} className="relative">
                    {/* Timeline Dot */}
                    <div className={clsx(
                      'absolute -left-12 top-1 w-8 h-8 rounded-full border-4 border-white dark:border-gray-900 flex items-center justify-center text-xs font-bold',
                      actionColors[change.action]
                    )}>
                      {change.action[0].toUpperCase()}
                    </div>

                    {/* Content */}
                    <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
                      <div className="flex items-baseline justify-between gap-2">
                        <p className="font-medium text-gray-900 dark:text-white capitalize">
                          {change.action}
                        </p>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          {new Date(change.timestamp).toLocaleString()}
                        </p>
                      </div>
                      <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">
                        By <strong>{change.username}</strong>
                      </p>
                      {change.field && (
                        <p className="text-xs text-gray-700 dark:text-gray-300 mt-2 p-2 bg-white dark:bg-gray-700 rounded">
                          <strong>{change.field}:</strong>
                          <br />
                          <span className="text-red-600">- {String(change.oldValue)}</span>
                          <br />
                          <span className="text-green-600">+ {String(change.newValue)}</span>
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
