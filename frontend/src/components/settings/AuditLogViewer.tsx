// ============================================================================
// IMS 2.0 - Audit Log Viewer
// ============================================================================

import { useState, useEffect } from 'react';
import { Search, Loader2 } from 'lucide-react';
import clsx from 'clsx';

interface AuditLog {
  id: string;
  timestamp: string;
  user_id: string;
  user_name: string;
  action: string;
  entity_type: string;
  entity_id?: string;
  details: string;
  ip_address?: string;
}

interface AuditLogViewerProps {
  logs?: AuditLog[];
}


const ACTION_COLORS: Record<string, string> = {
  CREATE: 'bg-green-900 text-green-200',
  UPDATE: 'bg-blue-900 text-blue-200',
  DELETE: 'bg-red-900 text-red-200',
  LOGIN: 'bg-purple-900 text-purple-200',
  LOGOUT: 'bg-purple-900 text-purple-200',
  EXPORT: 'bg-orange-900 text-orange-200',
};

export function AuditLogViewer({ logs = [] }: AuditLogViewerProps) {
  const [filteredLogs, setFilteredLogs] = useState<AuditLog[]>(logs);
  const [searchQuery, setSearchQuery] = useState('');
  const [actionFilter, setActionFilter] = useState<string>('all');
  const [userFilter, setUserFilter] = useState<string>('all');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [isLoading] = useState(false);

  useEffect(() => {
    filterLogs();
  }, [searchQuery, actionFilter, userFilter, dateFrom, dateTo, logs]);

  const filterLogs = () => {
    let result = [...logs];

    // Search filter
    if (searchQuery) {
      result = result.filter(log =>
        log.user_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.details.toLowerCase().includes(searchQuery.toLowerCase()) ||
        log.entity_id?.toLowerCase().includes(searchQuery.toLowerCase())
      );
    }

    // Action filter
    if (actionFilter !== 'all') {
      result = result.filter(log => log.action === actionFilter);
    }

    // User filter
    if (userFilter !== 'all') {
      result = result.filter(log => log.user_id === userFilter);
    }

    // Date range filter
    if (dateFrom) {
      const from = new Date(dateFrom).getTime();
      result = result.filter(log => new Date(log.timestamp).getTime() >= from);
    }

    if (dateTo) {
      const to = new Date(dateTo).getTime();
      result = result.filter(log => new Date(log.timestamp).getTime() <= to);
    }

    setFilteredLogs(result);
  };

  const uniqueActions = Array.from(new Set(logs.map(l => l.action)));
  const uniqueUsers = Array.from(new Set(logs.map(l => ({ id: l.user_id, name: l.user_name }))));

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return new Intl.DateTimeFormat('en-IN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(date);
  };

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-600 mb-2">Search</label>
          <div className="relative">
            <Search className="absolute left-3 top-3 w-4 h-4 text-gray-500" />
            <input
              type="text"
              placeholder="User, action, entity..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-white border border-gray-300 rounded text-gray-900 placeholder-gray-500 focus:border-blue-500 outline-none"
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-600 mb-2">Action</label>
          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 focus:border-blue-500 outline-none"
          >
            <option value="all">All Actions</option>
            {uniqueActions.map(action => (
              <option key={action} value={action}>{action}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-600 mb-2">User</label>
          <select
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
            className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 focus:border-blue-500 outline-none"
          >
            <option value="all">All Users</option>
            {uniqueUsers.map(user => (
              <option key={user.id} value={user.id}>{user.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-600 mb-2">From Date</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 focus:border-blue-500 outline-none"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-600 mb-2">To Date</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="w-full px-4 py-2 bg-white border border-gray-300 rounded text-gray-900 focus:border-blue-500 outline-none"
          />
        </div>
      </div>

      {/* Results count */}
      <div className="text-sm text-gray-400">
        Showing {filteredLogs.length} of {logs.length} logs
      </div>

      {/* Logs Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-600">Timestamp</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-600">User</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-600">Action</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-600">Entity</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-600">Details</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-600">IP Address</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center">
                  <Loader2 className="w-5 h-5 animate-spin mx-auto text-gray-400" />
                </td>
              </tr>
            ) : filteredLogs.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-gray-400">
                  No audit logs found
                </td>
              </tr>
            ) : (
              filteredLogs.map(log => (
                <tr key={log.id} className="border-b border-gray-200 hover:bg-gray-50 transition">
                  <td className="px-4 py-3 text-sm text-gray-600">{formatDate(log.timestamp)}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{log.user_name}</td>
                  <td className="px-4 py-3 text-sm">
                    <span className={clsx('px-3 py-1 rounded-full text-xs font-medium', ACTION_COLORS[log.action] || 'bg-gray-200 text-gray-600')}>
                      {log.action}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-600">
                    {log.entity_type}
                    {log.entity_id && ` (#${log.entity_id})`}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-400">{log.details}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">{log.ip_address || '-'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
