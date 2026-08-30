// ============================================================================
// IMS 2.0 - Settings · Audit Logs (/settings/audit-logs)
// ============================================================================
// Wave 1 split: the inline AuditLogSection + its types, styles and data
// loading moved verbatim out of the old SettingsPage tab container. Renders
// inside SettingsLayout.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState, useEffect } from 'react';
import {
  RefreshCw, Search, Calendar, Filter, X, Shield, LogOut, History, AlertCircle, Plus,
} from 'lucide-react';
import clsx from 'clsx';
import { settingsApi } from '../../services/api';

export function AuditLogSettingsPage() {
  const [isLoading, setIsLoading] = useState(true);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [auditSummary, setAuditSummary] = useState<{
    today: { total_actions: number; logins: number; orders_created: number };
  } | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [auditActionFilter, setAuditActionFilter] = useState<AuditAction | ''>('');
  const [auditSearchQuery, setAuditSearchQuery] = useState('');
  const [auditDateFrom, setAuditDateFrom] = useState('');
  const [auditDateTo, setAuditDateTo] = useState('');

  const loadLogs = async () => {
    setIsLoading(true);
    setAuditError(null);
    try {
      const [logsRes, summaryRes] = await Promise.all([
        settingsApi.getAuditLogs({ limit: 50 }),
        settingsApi.getAuditSummary().catch(() => null),
      ]);
      setAuditLogs(logsRes.logs || []);
      setAuditSummary(summaryRes || null);
    } catch (err) {
      setAuditLogs([]);
      setAuditSummary(null);
      setAuditError(err instanceof Error ? err.message : 'Failed to load audit logs');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-48">
        <RefreshCw className="w-8 h-8 animate-spin" style={{ color: 'var(--bv)' }} />
      </div>
    );
  }
  return (
    <AuditLogSection
      auditLogs={auditLogs}
      auditSummary={auditSummary}
      auditError={auditError}
      auditActionFilter={auditActionFilter}
      setAuditActionFilter={setAuditActionFilter}
      auditSearchQuery={auditSearchQuery}
      setAuditSearchQuery={setAuditSearchQuery}
      auditDateFrom={auditDateFrom}
      setAuditDateFrom={setAuditDateFrom}
      auditDateTo={auditDateTo}
      setAuditDateTo={setAuditDateTo}
      onRefresh={loadLogs}
    />
  );
}

type AuditAction = 'LOGIN' | 'LOGOUT' | 'CREATE' | 'UPDATE' | 'DELETE' | 'EXPORT';

interface AuditLogEntry {
  id: string;
  timestamp: string;
  user_id: string;
  user_name: string;
  action: AuditAction;
  details: string;
  ip_address: string;
  entity_type?: string;
  entity_id?: string;
  changes?: Record<string, any>;
}

const AUDIT_ACTION_STYLES: Record<AuditAction, { bg: string; text: string; label: string }> = {
  LOGIN:  { bg: 'bg-gray-100',   text: 'text-gray-700',   label: 'Login' },
  LOGOUT: { bg: 'bg-gray-100',   text: 'text-gray-600',   label: 'Logout' },
  CREATE: { bg: 'bg-green-100',  text: 'text-green-700',  label: 'Create' },
  UPDATE: { bg: 'bg-blue-100',   text: 'text-blue-700',   label: 'Update' },
  DELETE: { bg: 'bg-red-100',    text: 'text-red-700',    label: 'Delete' },
  EXPORT: { bg: 'bg-amber-100',  text: 'text-amber-700',  label: 'Export' },
};

const AUDIT_ACTION_ROW_STYLES: Record<AuditAction, string> = {
  LOGIN:  '',
  LOGOUT: '',
  CREATE: 'bg-green-50/40',
  UPDATE: '',
  DELETE: 'bg-red-50/40',
  EXPORT: '',
};
function AuditLogSection({
  auditLogs,
  auditSummary,
  auditError,
  auditActionFilter,
  setAuditActionFilter,
  auditSearchQuery,
  setAuditSearchQuery,
  auditDateFrom,
  setAuditDateFrom,
  auditDateTo,
  setAuditDateTo,
  onRefresh,
}: {
  auditLogs: AuditLogEntry[];
  auditSummary: { today: { total_actions: number; logins: number; orders_created: number } } | null;
  auditError: string | null;
  auditActionFilter: AuditAction | '';
  setAuditActionFilter: (v: AuditAction | '') => void;
  auditSearchQuery: string;
  setAuditSearchQuery: (v: string) => void;
  auditDateFrom: string;
  setAuditDateFrom: (v: string) => void;
  auditDateTo: string;
  setAuditDateTo: (v: string) => void;
  onRefresh: () => void;
}) {
  const filteredLogs = auditLogs.filter(log => {
    if (auditActionFilter && log.action !== auditActionFilter) return false;
    if (auditSearchQuery && !log.user_name.toLowerCase().includes(auditSearchQuery.toLowerCase())) return false;
    if (auditDateFrom) {
      const logDate = new Date(log.timestamp);
      const fromDate = new Date(auditDateFrom);
      fromDate.setHours(0, 0, 0, 0);
      if (logDate < fromDate) return false;
    }
    if (auditDateTo) {
      const logDate = new Date(log.timestamp);
      const toDate = new Date(auditDateTo);
      toDate.setHours(23, 59, 59, 999);
      if (logDate > toDate) return false;
    }
    return true;
  });

  const hasActiveFilters = !!(auditActionFilter || auditSearchQuery || auditDateFrom || auditDateTo);

  return (
    <div className="space-y-4">
      {/* Error Banner */}
      {auditError && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <span className="text-sm text-red-700">{auditError}</span>
          <button onClick={onRefresh} className="ml-auto text-sm text-red-600 hover:underline flex-shrink-0">
            Retry
          </button>
        </div>
      )}

      {/* Summary Cards */}
      {auditSummary && (
        <div className="grid grid-cols-1 tablet:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-1">
              <Shield className="w-4 h-4 text-gray-500" />
              <p className="text-sm text-gray-500">Total Actions</p>
            </div>
            <p className="text-2xl font-bold text-gray-900">{auditSummary.today.total_actions}</p>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-1">
              <LogOut className="w-4 h-4 text-green-600" />
              <p className="text-sm text-gray-500">Logins</p>
            </div>
            <p className="text-2xl font-bold text-green-600">{auditSummary.today.logins}</p>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-1">
              <Plus className="w-4 h-4 text-blue-600" />
              <p className="text-sm text-gray-500">Orders Created</p>
            </div>
            <p className="text-2xl font-bold text-blue-600">{auditSummary.today.orders_created}</p>
          </div>
          <div className="card p-4">
            <div className="flex items-center gap-2 mb-1">
              <AlertCircle className="w-4 h-4 text-green-600" />
              <p className="text-sm text-gray-500">System Health</p>
            </div>
            <p className="text-2xl font-bold text-green-600">Good</p>
          </div>
        </div>
      )}

      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <History className="w-5 h-5 text-gray-500" />
            <h2 className="text-lg font-semibold text-gray-900">Activity Log</h2>
            <span className="text-sm text-gray-500 ml-1">
              ({filteredLogs.length}{hasActiveFilters ? ` of ${auditLogs.length}` : ''} entries)
            </span>
          </div>
          <button onClick={onRefresh} className="btn-outline flex items-center gap-1" title="Refresh logs">
            <RefreshCw className="w-4 h-4" />
            <span className="hidden sm:inline text-sm">Refresh</span>
          </button>
        </div>

        {/* Filters Row */}
        <div className="flex flex-wrap items-end gap-3 mb-4 p-3 bg-gray-50 rounded-lg border border-gray-200">
          <div className="flex items-center gap-1 text-sm font-medium text-gray-500">
            <Filter className="w-4 h-4" />
            Filters
          </div>

          <div className="flex-1 min-w-[180px]">
            <label className="block text-xs text-gray-500 mb-1">Search User</label>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
              <input
                type="text"
                placeholder="Search by user name..."
                value={auditSearchQuery}
                onChange={e => setAuditSearchQuery(e.target.value)}
                className="input-field pl-8 w-full"
              />
            </div>
          </div>

          <div className="min-w-[150px]">
            <label className="block text-xs text-gray-500 mb-1">Action Type</label>
            <select
              value={auditActionFilter}
              onChange={e => setAuditActionFilter(e.target.value as AuditAction | '')}
              className="input-field w-full"
            >
              <option value="">All Actions</option>
              <option value="LOGIN">Login</option>
              <option value="LOGOUT">Logout</option>
              <option value="CREATE">Create</option>
              <option value="UPDATE">Update</option>
              <option value="DELETE">Delete</option>
              <option value="EXPORT">Export</option>
            </select>
          </div>

          <div className="min-w-[150px]">
            <label className="block text-xs text-gray-500 mb-1">
              <span className="flex items-center gap-1"><Calendar className="w-3 h-3" /> From</span>
            </label>
            <input
              type="date"
              value={auditDateFrom}
              onChange={e => setAuditDateFrom(e.target.value)}
              className="input-field w-full"
            />
          </div>

          <div className="min-w-[150px]">
            <label className="block text-xs text-gray-500 mb-1">
              <span className="flex items-center gap-1"><Calendar className="w-3 h-3" /> To</span>
            </label>
            <input
              type="date"
              value={auditDateTo}
              onChange={e => setAuditDateTo(e.target.value)}
              className="input-field w-full"
            />
          </div>

          {hasActiveFilters && (
            <button
              onClick={() => {
                setAuditActionFilter('');
                setAuditSearchQuery('');
                setAuditDateFrom('');
                setAuditDateTo('');
              }}
              className="btn-outline text-sm flex items-center gap-1 self-end"
            >
              <X className="w-3.5 h-3.5" />
              Clear
            </button>
          )}
        </div>

        {/* Table */}
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Timestamp</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">User</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Details</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">IP Address</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {filteredLogs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-gray-500">
                    <History className="w-12 h-12 mx-auto mb-3 opacity-50" />
                    {auditLogs.length === 0 && !hasActiveFilters ? (
                      <p className="font-medium">No audit logs yet</p>
                    ) : (
                      <>
                        <p className="font-medium">No audit logs found</p>
                        {hasActiveFilters && (
                          <p className="text-sm mt-1">Try adjusting your filters to see more results.</p>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ) : (
                filteredLogs.map(log => {
                  const actionKey = log.action as AuditAction;
                  const style = AUDIT_ACTION_STYLES[actionKey] || AUDIT_ACTION_STYLES.UPDATE;
                  const rowBg = AUDIT_ACTION_ROW_STYLES[actionKey] || '';

                  return (
                    <tr key={log.id} className={clsx('hover:bg-gray-100 transition-colors', rowBg)}>
                      <td className="px-4 py-3 text-sm text-gray-500 whitespace-nowrap">
                        <div>{new Date(log.timestamp).toLocaleDateString()}</div>
                        <div className="text-xs text-gray-500">{new Date(log.timestamp).toLocaleTimeString()}</div>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <p className="text-sm font-medium text-gray-900">{log.user_name}</p>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <span className={clsx(
                          'inline-flex items-center text-xs font-semibold px-2.5 py-1 rounded-full',
                          style.bg, style.text
                        )}>
                          {style.label}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <p className={clsx(
                          'text-sm',
                          actionKey === 'DELETE' ? 'text-red-700' :
                          actionKey === 'CREATE' ? 'text-green-700' :
                          'text-gray-600'
                        )}>
                          {log.details}
                        </p>
                        {log.entity_type && (
                          <p className="text-xs text-gray-500 mt-0.5">
                            {log.entity_type}{log.entity_id ? ` / ${log.entity_id}` : ''}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500 font-mono whitespace-nowrap">
                        {log.ip_address || '-'}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {filteredLogs.length > 0 && (
          <p className="text-xs text-gray-500 mt-3 text-right">
            Showing {filteredLogs.length} log {filteredLogs.length === 1 ? 'entry' : 'entries'}
            {hasActiveFilters ? ' (filtered)' : ''}
          </p>
        )}
      </div>
    </div>
  );
}

