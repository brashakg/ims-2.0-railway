// ============================================================================
// IMS 2.0 - Reports Builder
// ============================================================================
// Custom report creation with filters, columns, and export options

import { useState } from 'react';
import { Plus, Eye, Download, Trash2, Edit2, BarChart3 } from 'lucide-react';

export interface ReportColumn {
  id: string;
  name: string;
  label: string;
  type: 'text' | 'number' | 'date' | 'currency' | 'percentage';
  visible: boolean;
  width?: number;
}

export interface ReportFilter {
  id: string;
  field: string;
  operator: 'equals' | 'contains' | 'greater' | 'less' | 'between' | 'in';
  value: any;
  value2?: any;
}

export interface CustomReport {
  id: string;
  name: string;
  description?: string;
  source: string; // orders, products, customers, etc.
  columns: ReportColumn[];
  filters: ReportFilter[];
  groupBy?: string;
  sortBy?: { field: string; direction: 'asc' | 'desc' };
  createdAt: string;
  updatedAt: string;
  lastRun?: string;
  rowCount?: number;
  owner: string;
  isPublic: boolean;
}

interface ReportsBuilderProps {
  reports: CustomReport[];
  dataSources: { id: string; label: string }[];
  onCreateReport: (report: CustomReport) => Promise<void>;
  onUpdateReport: (report: CustomReport) => Promise<void>;
  onDeleteReport: (id: string) => Promise<void>;
  onRunReport: (id: string) => Promise<any>;
  onExportReport: (id: string, format: 'csv' | 'excel' | 'pdf') => Promise<void>;
  loading?: boolean;
}

interface ReportBuilderModalProps {
  dataSource?: string;
  onSave: (report: Omit<CustomReport, 'id' | 'createdAt' | 'updatedAt' | 'owner'>) => void;
  onClose: () => void;
  initialReport?: CustomReport;
}

export function ReportsBuilder({
  reports,
  onCreateReport,
  onUpdateReport,
  onDeleteReport,
  onRunReport,
  onExportReport,
}: ReportsBuilderProps) {
  const [showBuilder, setShowBuilder] = useState(false);
  const [editingReport, setEditingReport] = useState<CustomReport | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this report?')) return;
    setActionLoading(id);
    try {
      await Promise.resolve(onDeleteReport(id));
    } finally {
      setActionLoading(null);
    }
  };

  const handleRun = async (id: string) => {
    setActionLoading(id);
    try {
      await Promise.resolve(onRunReport(id));
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className="p-6 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <BarChart3 className="w-5 h-5" />
            Custom Reports
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
            Create and manage custom reports with filters and exports
          </p>
        </div>
        <button
          onClick={() => {
            setEditingReport(null);
            setShowBuilder(true);
          }}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
        >
          <Plus className="w-4 h-4" />
          New Report
        </button>
      </div>

      {/* Reports List */}
      <div className="divide-y divide-gray-200 dark:divide-gray-800">
        {reports.length === 0 ? (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            <BarChart3 className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No custom reports yet. Create one to get started.</p>
          </div>
        ) : (
          reports.map(report => (
            <div key={report.id} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <h3 className="font-semibold text-gray-900 dark:text-white">
                    {report.name}
                  </h3>
                  {report.description && (
                    <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                      {report.description}
                    </p>
                  )}
                  <div className="flex items-center gap-4 mt-2 text-xs text-gray-500 dark:text-gray-400">
                    <span>Source: {report.source}</span>
                    <span>{report.columns.length} columns</span>
                    {report.rowCount !== undefined && (
                      <span>{report.rowCount.toLocaleString()} rows</span>
                    )}
                    {report.lastRun && (
                      <span>Last run: {new Date(report.lastRun).toLocaleDateString()}</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleRun(report.id)}
                    disabled={actionLoading === report.id}
                    className="p-2 hover:bg-blue-100 dark:hover:bg-blue-900/20 rounded-lg transition-colors text-blue-600 dark:text-blue-400 disabled:opacity-50"
                    title="Run report"
                  >
                    <Eye className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => onExportReport(report.id, 'csv')}
                    className="p-2 hover:bg-green-100 dark:hover:bg-green-900/20 rounded-lg transition-colors text-green-600 dark:text-green-400"
                    title="Export as CSV"
                  >
                    <Download className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      setEditingReport(report);
                      setShowBuilder(true);
                    }}
                    className="p-2 hover:bg-amber-100 dark:hover:bg-amber-900/20 rounded-lg transition-colors text-amber-600 dark:text-amber-400"
                    title="Edit report"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleDelete(report.id)}
                    disabled={actionLoading === report.id}
                    className="p-2 hover:bg-red-100 dark:hover:bg-red-900/20 rounded-lg transition-colors text-red-600 dark:text-red-400 disabled:opacity-50"
                    title="Delete report"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Report Builder Modal */}
      {showBuilder && (
        <ReportBuilderModal
          dataSource={editingReport?.source}
          onSave={async report => {
            if (editingReport) {
              await Promise.resolve(onUpdateReport({ ...report, id: editingReport.id, createdAt: editingReport.createdAt, updatedAt: editingReport.updatedAt, owner: editingReport.owner } as CustomReport));
            } else {
              await Promise.resolve(onCreateReport({ ...report, id: Date.now().toString(), createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), owner: 'current_user' } as CustomReport));
            }
            setShowBuilder(false);
            setEditingReport(null);
          }}
          onClose={() => {
            setShowBuilder(false);
            setEditingReport(null);
          }}
          initialReport={editingReport || undefined}
        />
      )}
    </div>
  );
}

/**
 * Report Builder Modal
 */
function ReportBuilderModal({
  dataSource,
  onSave,
  onClose,
  initialReport,
}: ReportBuilderModalProps) {
  const [name, setName] = useState(initialReport?.name || '');
  const [description, setDescription] = useState(initialReport?.description || '');
  const [source, setSource] = useState(initialReport?.source || dataSource || '');
  const [isPublic, setIsPublic] = useState(initialReport?.isPublic || false);

  const handleSave = () => {
    if (!name.trim() || !source) {
      alert('Please fill in all required fields');
      return;
    }

    onSave({
      name,
      description,
      source,
      columns: initialReport?.columns || [],
      filters: initialReport?.filters || [],
      isPublic,
      lastRun: initialReport?.lastRun,
    });
  };

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-2xl w-full max-h-96 overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
          {initialReport ? 'Edit Report' : 'Create New Report'}
        </h2>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Report Name *
            </label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g., Monthly Sales Summary"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Description
            </label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Optional description"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              rows={2}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Data Source *
            </label>
            <select
              value={source}
              onChange={e => setSource(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
            >
              <option value="">Select a source</option>
              <option value="orders">Orders</option>
              <option value="products">Products</option>
              <option value="customers">Customers</option>
              <option value="sales">Sales</option>
              <option value="inventory">Inventory</option>
            </select>
          </div>

          <div>
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={isPublic}
                onChange={e => setIsPublic(e.target.checked)}
                className="w-4 h-4 rounded text-blue-600"
              />
              <span className="text-sm text-gray-700 dark:text-gray-300">
                Make this report public
              </span>
            </label>
          </div>
        </div>

        <div className="flex gap-2 mt-6">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors font-medium"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
          >
            {initialReport ? 'Update' : 'Create'} Report
          </button>
        </div>
      </div>
    </div>
  );
}
