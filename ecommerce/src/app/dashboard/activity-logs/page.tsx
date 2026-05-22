'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  ScrollText,
  Search,
  Filter,
  ChevronLeft,
  ChevronRight,
  Calendar,
  User,
  Activity,
  Database,
  Clock,
  Download,
  RefreshCw,
  X,
} from 'lucide-react';
import Topbar from '@/components/Topbar';

interface LogEntry {
  id: string;
  userId: string | null;
  userName: string | null;
  userEmail: string | null;
  action: string;
  entity: string;
  entityId: string | null;
  details: string | null;
  metadata: string | null;
  ipAddress: string | null;
  createdAt: string;
}

interface FilterOptions {
  users: { userEmail: string; userName: string | null }[];
  actions: string[];
  entities: string[];
}

const ACTION_COLORS: Record<string, string> = {
  CREATE: 'bg-green-100 text-green-800',
  UPDATE: 'bg-blue-100 text-blue-800',
  DELETE: 'bg-red-100 text-red-800',
  SYNC: 'bg-purple-100 text-purple-800',
  LOGIN: 'bg-yellow-100 text-yellow-800',
  LOGOUT: 'bg-gray-100 text-gray-800',
  EXPORT: 'bg-indigo-100 text-indigo-800',
  UPLOAD: 'bg-cyan-100 text-cyan-800',
  PULL: 'bg-orange-100 text-orange-800',
  PUSH: 'bg-teal-100 text-teal-800',
  WEBHOOK: 'bg-pink-100 text-pink-800',
};

const ENTITY_ICONS: Record<string, string> = {
  PRODUCT: '📦',
  ORDER: '🛒',
  CUSTOMER: '👤',
  COLLECTION: '📁',
  USER: '👥',
  SETTINGS: '⚙️',
  IMAGE: '🖼️',
  STOCK_TRANSFER: '🔄',
  WEBHOOK: '🔗',
  SHOPIFY: '🛍️',
};

export default function ActivityLogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [filterOptions, setFilterOptions] = useState<FilterOptions>({
    users: [],
    actions: [],
    entities: [],
  });

  // Filters
  const [search, setSearch] = useState('');
  const [selectedAction, setSelectedAction] = useState('');
  const [selectedEntity, setSelectedEntity] = useState('');
  const [selectedUser, setSelectedUser] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [showFilters, setShowFilters] = useState(false);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('page', String(page));
      params.set('limit', '50');
      if (search) params.set('search', search);
      if (selectedAction) params.set('action', selectedAction);
      if (selectedEntity) params.set('entity', selectedEntity);
      if (selectedUser) params.set('userEmail', selectedUser);
      if (dateFrom) params.set('dateFrom', dateFrom);
      if (dateTo) params.set('dateTo', dateTo);

      const res = await fetch(`/api/activity-logs?${params.toString()}`);
      const json = await res.json();
      if (json.success) {
        setLogs(json.data.logs);
        setTotalPages(json.data.pagination.totalPages);
        setTotal(json.data.pagination.total);
        setFilterOptions(json.data.filters);
      }
    } catch (err) {
      console.error('Failed to fetch logs:', err);
    } finally {
      setLoading(false);
    }
  }, [page, search, selectedAction, selectedEntity, selectedUser, dateFrom, dateTo]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  // Debounced search
  const [searchInput, setSearchInput] = useState('');
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 400);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const clearFilters = () => {
    setSelectedAction('');
    setSelectedEntity('');
    setSelectedUser('');
    setDateFrom('');
    setDateTo('');
    setSearchInput('');
    setSearch('');
    setPage(1);
  };

  const activeFilterCount = [selectedAction, selectedEntity, selectedUser, dateFrom, dateTo].filter(Boolean).length;

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  };

  const formatTime = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  };

  const exportCSV = () => {
    const headers = ['Date', 'Time', 'User', 'Action', 'Entity', 'Entity ID', 'Details'];
    const rows = logs.map((log) => [
      formatDate(log.createdAt),
      formatTime(log.createdAt),
      log.userName || log.userEmail || 'System',
      log.action,
      log.entity,
      log.entityId || '',
      (log.details || '').replace(/,/g, ';'),
    ]);
    const csv = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `activity-logs-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <Topbar
        title="Activity Logs"
        subtitle="Track all changes and actions"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Activity Logs' }]}
        primaryAction={null}
        actions={
          <>
            <button
              type="button"
              onClick={fetchLogs}
              className="polaris-btn"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <button
              type="button"
              onClick={exportCSV}
              disabled={logs.length === 0}
              className="polaris-btn"
            >
              <Download className="w-3.5 h-3.5" />
              Export CSV
            </button>
          </>
        }
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>

      {/* Search + Filter Toggle */}
      <div className="flex flex-col sm:flex-row gap-3 mb-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="Search logs by details, user, or entity ID..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="w-full pl-10 pr-4 py-2.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />
        </div>
        <button
          onClick={() => setShowFilters(!showFilters)}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm rounded-lg border transition-colors ${
            showFilters || activeFilterCount > 0
              ? 'bg-blue-50 border-blue-300 text-blue-700'
              : 'bg-white border-slate-300 text-slate-700 hover:bg-slate-50'
          }`}
        >
          <Filter className="w-4 h-4" />
          Filters
          {activeFilterCount > 0 && (
            <span className="bg-blue-600 text-white text-xs rounded-full w-5 h-5 flex items-center justify-center">
              {activeFilterCount}
            </span>
          )}
        </button>
      </div>

      {/* Filter Panel */}
      {showFilters && (
        <div className="bg-white border border-slate-200 rounded-xl p-4 mb-4 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-700">Filter Logs</h3>
            {activeFilterCount > 0 && (
              <button
                onClick={clearFilters}
                className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1"
              >
                <X className="w-3 h-3" /> Clear all
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {/* Date From */}
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                <Calendar className="w-3 h-3 inline mr-1" /> From Date
              </label>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            {/* Date To */}
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                <Calendar className="w-3 h-3 inline mr-1" /> To Date
              </label>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            {/* User */}
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                <User className="w-3 h-3 inline mr-1" /> User
              </label>
              <select
                value={selectedUser}
                onChange={(e) => { setSelectedUser(e.target.value); setPage(1); }}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Users</option>
                {filterOptions.users.map((u) => (
                  <option key={u.userEmail} value={u.userEmail!}>
                    {u.userName || u.userEmail}
                  </option>
                ))}
              </select>
            </div>
            {/* Action */}
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                <Activity className="w-3 h-3 inline mr-1" /> Action
              </label>
              <select
                value={selectedAction}
                onChange={(e) => { setSelectedAction(e.target.value); setPage(1); }}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Actions</option>
                {filterOptions.actions.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </div>
            {/* Entity */}
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                <Database className="w-3 h-3 inline mr-1" /> Entity
              </label>
              <select
                value={selectedEntity}
                onChange={(e) => { setSelectedEntity(e.target.value); setPage(1); }}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Entities</option>
                {filterOptions.entities.map((e) => (
                  <option key={e} value={e}>{e}</option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}

      {/* Stats bar */}
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-slate-500">
          {total > 0
            ? `Showing ${(page - 1) * 50 + 1}–${Math.min(page * 50, total)} of ${total.toLocaleString()} logs`
            : 'No logs found'}
        </p>
      </div>

      {/* Logs Table */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="w-8 h-8 border-4 border-slate-200 border-t-blue-600 rounded-full animate-spin" />
          </div>
        ) : logs.length === 0 ? (
          <div className="text-center py-20">
            <ScrollText className="w-12 h-12 text-slate-300 mx-auto mb-3" />
            <p className="text-slate-500 font-medium">No activity logs yet</p>
            <p className="text-sm text-slate-400 mt-1">
              Actions will be recorded here as you use the system
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200">
                  <th className="text-left px-4 py-3 font-semibold text-slate-600">
                    <Clock className="w-3.5 h-3.5 inline mr-1" /> Date & Time
                  </th>
                  <th className="text-left px-4 py-3 font-semibold text-slate-600">
                    <User className="w-3.5 h-3.5 inline mr-1" /> User
                  </th>
                  <th className="text-left px-4 py-3 font-semibold text-slate-600">Action</th>
                  <th className="text-left px-4 py-3 font-semibold text-slate-600">Entity</th>
                  <th className="text-left px-4 py-3 font-semibold text-slate-600">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {logs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="text-slate-900 font-medium">{formatDate(log.createdAt)}</div>
                      <div className="text-slate-400 text-xs">{formatTime(log.createdAt)}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-slate-900">{log.userName || 'System'}</div>
                      <div className="text-slate-400 text-xs">{log.userEmail || '—'}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${
                        ACTION_COLORS[log.action] || 'bg-gray-100 text-gray-800'
                      }`}>
                        {log.action}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="text-sm">
                        {ENTITY_ICONS[log.entity] || '📋'}{' '}
                        <span className="text-slate-700">{log.entity}</span>
                      </span>
                      {log.entityId && (
                        <div className="text-xs text-slate-400 font-mono truncate max-w-[140px]">
                          {log.entityId}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 max-w-[350px]">
                      <p className="text-slate-700 truncate">{log.details || '—'}</p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="flex items-center gap-1 px-3 py-2 text-sm border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <ChevronLeft className="w-4 h-4" /> Previous
          </button>
          <span className="text-sm text-slate-600">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="flex items-center gap-1 px-3 py-2 text-sm border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}
      </div>
    </>
  );
}
