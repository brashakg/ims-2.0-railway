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

const SAMPLE_LOGS: AuditLog[] = [
  { id: 'al-001', timestamp: new Date(Date.now() - 5 * 60000).toISOString(), user_id: 'u1', user_name: 'Rajesh Kumar', action: 'LOGIN', details: 'User logged in via web', ip_address: '192.168.1.101', entity_type: 'Session' },
  { id: 'al-002', timestamp: new Date(Date.now() - 12 * 60000).toISOString(), user_id: 'u2', user_name: 'Priya Sharma', action: 'CREATE', details: 'Created new order #ORD-2025-0847', ip_address: '192.168.1.105', entity_type: 'Order', entity_id: 'ORD-2025-0847' },
  { id: 'al-003', timestamp: new Date(Date.now() - 25 * 60000).toISOString(), user_id: 'u1', user_name: 'Rajesh Kumar', action: 'UPDATE', details: 'Updated product price for Ray-Ban Aviator', ip_address: '192.168.1.101', entity_type: 'Product', entity_id: 'PRD-00412' },
  { id: 'al-004', timestamp: new Date(Date.now() - 38 * 60000).toISOString(), user_id: 'u3', user_name: 'Amit Patel', action: 'DELETE', details: 'Deleted draft invoice #INV-2025-0092', ip_address: '192.168.1.110', entity_type: 'Invoice', entity_id: 'INV-2025-0092' },
  { id: 'al-005', timestamp: new Date(Date.now() - 45 * 60000).toISOString(), user_id: 'u4', user_name: 'Sneha Reddy', action: 'EXPORT', details: 'Exported sales report for Jan 2025', ip_address: '192.168.1.108', entity_type: 'Report' },
];

const ACTION_COLORS: Record<string, string> = {
  CREATE: 'bg-green-900 text-green-200',
  UPDATE: 'bg-blue-900 text-blue-200',
  DELETE: 'bg-red-900 text-red-200',
  LOGIN: 'bg-purple-900 text-purple-200',
  LOGOUT: 'bg-purple-900 text-purple-200',
  EXPORT: 'bg-orange-900 text-orange-200',
};

export function AuditLogViewer({ logs = SAMPLE_LOGS }: AuditLogViewerProps) {
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
          <label className="block text-sm font-medium text-gray-300 mb-2">Search</label>
          <div className="relative">
            <Search className="absolute left-3 top-3 w-4 h-4 text-gray-500" />
            <input
              type="text"
              placeholder="User, action, entity..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-gray-700 border border-gray-600 rounded text-white placeholder-gray-400 focus:border-blue-500 outline-none"
            />
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">Action</label>
          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:border-blue-500 outline-none"
          >
            <option value="all">All Actions</option>
            {uniqueActions.map(action => (
              <option key={action} value={action}>{action}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">User</label>
          <select
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
            className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:border-blue-500 outline-none"
          >
            <option value="all">All Users</option>
            {uniqueUsers.map(user => (
              <option key={user.id} value={user.id}>{user.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">From Date</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:border-blue-500 outline-none"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">To Date</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:border-blue-500 outline-none"
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
            <tr className="border-b border-gray-700">
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Timestamp</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">User</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Action</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Entity</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">Details</th>
              <th className="px-4 py-3 text-left text-sm font-semibold text-gray-300">IP Address</th>
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
                <tr key={log.id} className="border-b border-gray-700 hover:bg-gray-800 transition">
                  <td className="px-4 py-3 text-sm text-gray-300">{formatDate(log.timestamp)}</td>
                  <td className="px-4 py-3 text-sm text-gray-300">{log.user_name}</td>
                  <td className="px-4 py-3 text-sm">
                    <span className={clsx('px-3 py-1 rounded-full text-xs font-medium', ACTION_COLORS[log.action] || 'bg-gray-700 text-gray-300')}>
                      {log.action}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-300">
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
