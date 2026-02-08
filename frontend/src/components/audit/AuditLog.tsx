// ============================================================================
// IMS 2.0 - Audit Log Component
// ============================================================================
// Display action history and track user activities with timestamps

import { useState } from 'react';
import { ChevronDown, Filter, X } from 'lucide-react';
import clsx from 'clsx';

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  userId: string;
  username: string;
  action: string; // 'CREATE', 'UPDATE', 'DELETE', 'VIEW', etc.
  entityType: string; // 'Product', 'Order', 'Customer', etc.
  entityId: string;
  entityName: string;
  changes?: {
    field: string;
    oldValue: any;
    newValue: any;
  }[];
  ipAddress?: string;
  userAgent?: string;
  status: 'success' | 'failure';
  details?: string;
}

interface AuditLogProps {
  entries: AuditLogEntry[];
  loading?: boolean;
  onFilterChange?: (filters: AuditFilters) => void;
  maxRows?: number;
}

export interface AuditFilters {
  action?: string;
  entityType?: string;
  userId?: string;
  status?: 'success' | 'failure';
  dateRange?: { from: string; to: string };
}

const ACTION_COLORS: Record<string, string> = {
  CREATE: 'bg-green-100 text-green-700',
  UPDATE: 'bg-blue-100 text-blue-700',
  DELETE: 'bg-red-100 text-red-700',
  VIEW: 'bg-gray-100 text-gray-700',
  EXPORT: 'bg-purple-100 text-purple-700',
  IMPORT: 'bg-orange-100 text-orange-700',
  LOGIN: 'bg-green-100 text-green-700',
  LOGOUT: 'bg-gray-100 text-gray-700',
};

export function AuditLog({ entries, loading = false, onFilterChange, maxRows = 50 }: AuditLogProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);
  const [filters, setFilters] = useState<AuditFilters>({});
  const [displayRows, setDisplayRows] = useState(maxRows);

  const handleFilterChange = (newFilters: AuditFilters) => {
    setFilters(newFilters);
    onFilterChange?.(newFilters);
  };

  const clearFilters = () => {
    setFilters({});
    onFilterChange?.({});
  };

  const filteredEntries = entries.filter(entry => {
    if (filters.action && entry.action !== filters.action) return false;
    if (filters.entityType && entry.entityType !== filters.entityType) return false;
    if (filters.userId && entry.userId !== filters.userId) return false;
    if (filters.status && entry.status !== filters.status) return false;
    return true;
  });

  const displayedEntries = filteredEntries.slice(0, displayRows);

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="animate-pulse space-y-3">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-12 bg-gray-100 rounded" />
          ))}
        </div>
      </div>
    );
  }

  const uniqueActions = Array.from(new Set(entries.map(e => e.action)));
  const uniqueEntities = Array.from(new Set(entries.map(e => e.entityType)));
  const uniqueUsers = Array.from(new Set(entries.map(e => e.userId)));

  return (
    <div className="bg-white rounded-lg border border-gray-200">
      {/* Header with filters */}
      <div className="border-b border-gray-200 p-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Audit Log</h2>
          <p className="text-sm text-gray-600 mt-1">
            Showing {displayedEntries.length} of {filteredEntries.length} actions
          </p>
        </div>
        <button
          onClick={() => setShowFilters(!showFilters)}
          className={clsx(
            'inline-flex items-center gap-2 px-3 py-2 rounded-lg font-medium transition-colors',
            showFilters
              ? 'bg-blue-100 text-blue-700'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
          )}
          aria-label="Toggle filters"
          aria-expanded={showFilters}
        >
          <Filter className="w-4 h-4" />
          <span className="text-sm">Filters</span>
          {Object.keys(filters).length > 0 && (
            <span className="bg-blue-600 text-white text-xs rounded-full px-2">
              {Object.keys(filters).length}
            </span>
          )}
        </button>
      </div>

      {/* Filter Panel */}
      {showFilters && (
        <div className="border-b border-gray-200 p-4 bg-gray-50 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            {/* Action Filter */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Action</label>
              <select
                value={filters.action || ''}
                onChange={e => handleFilterChange({ ...filters, action: e.target.value || undefined })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Actions</option>
                {uniqueActions.map(action => (
                  <option key={action} value={action}>
                    {action}
                  </option>
                ))}
              </select>
            </div>

            {/* Entity Type Filter */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Entity Type</label>
              <select
                value={filters.entityType || ''}
                onChange={e => handleFilterChange({ ...filters, entityType: e.target.value || undefined })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Types</option>
                {uniqueEntities.map(entity => (
                  <option key={entity} value={entity}>
                    {entity}
                  </option>
                ))}
              </select>
            </div>

            {/* User Filter */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">User</label>
              <select
                value={filters.userId || ''}
                onChange={e => handleFilterChange({ ...filters, userId: e.target.value || undefined })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Users</option>
                {uniqueUsers.map(userId => (
                  <option key={userId} value={userId}>
                    {userId}
                  </option>
                ))}
              </select>
            </div>

            {/* Status Filter */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Status</label>
              <select
                value={filters.status || ''}
                onChange={e => handleFilterChange({ ...filters, status: (e.target.value as any) || undefined })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Statuses</option>
                <option value="success">Success</option>
                <option value="failure">Failure</option>
              </select>
            </div>
          </div>

          {Object.keys(filters).length > 0 && (
            <button
              onClick={clearFilters}
              className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 font-medium"
            >
              <X className="w-4 h-4" />
              Clear All Filters
            </button>
          )}
        </div>
      )}

      {/* Audit Log Entries */}
      <div className="divide-y divide-gray-200">
        {displayedEntries.length === 0 ? (
          <div className="px-4 py-8 text-center text-gray-500">
            No audit log entries found
          </div>
        ) : (
          displayedEntries.map(entry => (
            <div key={entry.id} className="hover:bg-gray-50 transition-colors">
              <button
                onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                className="w-full px-4 py-3 text-left flex items-center justify-between gap-3"
                aria-expanded={expandedId === entry.id}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className={clsx('px-2 py-1 text-xs font-bold rounded', ACTION_COLORS[entry.action] || 'bg-gray-100 text-gray-700')}
                    >
                      {entry.action}
                    </span>
                    <span className="text-sm font-medium text-gray-900">
                      {entry.entityType}: {entry.entityName}
                    </span>
                    <span className={clsx('text-xs px-2 py-1 rounded', entry.status === 'success' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
                      {entry.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-4 text-xs text-gray-600">
                    <span>By {entry.username}</span>
                    <span>{new Date(entry.timestamp).toLocaleString()}</span>
                  </div>
                </div>
                <ChevronDown
                  className={clsx('w-4 h-4 text-gray-400 transition-transform flex-shrink-0', expandedId === entry.id && 'rotate-180')}
                />
              </button>

              {/* Expanded Details */}
              {expandedId === entry.id && (
                <div className="px-4 py-4 bg-gray-50 border-t border-gray-200 text-sm">
                  <div className="grid grid-cols-2 gap-4 mb-4">
                    <div>
                      <p className="text-gray-600 font-medium mb-1">User ID</p>
                      <p className="text-gray-900 font-mono text-xs">{entry.userId}</p>
                    </div>
                    <div>
                      <p className="text-gray-600 font-medium mb-1">Entity ID</p>
                      <p className="text-gray-900 font-mono text-xs">{entry.entityId}</p>
                    </div>
                    {entry.ipAddress && (
                      <div>
                        <p className="text-gray-600 font-medium mb-1">IP Address</p>
                        <p className="text-gray-900 font-mono text-xs">{entry.ipAddress}</p>
                      </div>
                    )}
                    <div>
                      <p className="text-gray-600 font-medium mb-1">Timestamp</p>
                      <p className="text-gray-900 font-mono text-xs">{new Date(entry.timestamp).toISOString()}</p>
                    </div>
                  </div>

                  {/* Changes */}
                  {entry.changes && entry.changes.length > 0 && (
                    <div className="mt-4 pt-4 border-t border-gray-200">
                      <p className="text-gray-600 font-medium mb-3">Changes</p>
                      <div className="space-y-2 bg-white rounded border border-gray-200 p-3">
                        {entry.changes.map((change, i) => (
                          <div key={i} className="text-xs">
                            <p className="font-mono text-gray-700">{change.field}</p>
                            <div className="flex items-center gap-2 ml-2 mt-1">
                              <span className="text-red-600 line-through">{String(change.oldValue)}</span>
                              <span className="text-gray-400">→</span>
                              <span className="text-green-600">{String(change.newValue)}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Details */}
                  {entry.details && (
                    <div className="mt-4 pt-4 border-t border-gray-200">
                      <p className="text-gray-600 font-medium mb-2">Details</p>
                      <p className="text-gray-900 bg-white rounded border border-gray-200 p-2 text-xs">{entry.details}</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Load More */}
      {filteredEntries.length > displayRows && (
        <div className="border-t border-gray-200 p-4 text-center">
          <button
            onClick={() => setDisplayRows(displayRows + maxRows)}
            className="text-sm font-medium text-blue-600 hover:text-blue-700"
          >
            Load More ({filteredEntries.length - displayRows} remaining)
          </button>
        </div>
      )}
    </div>
  );
}
