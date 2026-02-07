// ============================================================================
// IMS 2.0 - Workshop Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import {
  Wrench,
  Clock,
  CheckCircle,
  AlertTriangle,
  Search,
  Eye,
  Phone,
  User,
  Zap,
  Timer,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import type { JobStatus, JobPriority } from '../../types';
import { workshopApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import clsx from 'clsx';

// Job type
interface Job {
  id: string;
  jobNumber: string;
  orderNumber: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  frameName: string;
  frameBarcode?: string;
  lensType: string;
  status: JobStatus;
  priority: JobPriority;
  assignedTo?: string;
  expectedDate: string;
  promisedDate: string;
  createdAt: string;
  completedAt?: string;
  notes?: string;
}

const STATUS_CONFIG: Record<JobStatus, { label: string; class: string; step: number }> = {
  CREATED: { label: 'Created', class: 'bg-gray-100 text-gray-600', step: 1 },
  LENS_ORDERED: { label: 'Lens Ordered', class: 'bg-blue-100 text-blue-600', step: 2 },
  LENS_RECEIVED: { label: 'Lens Received', class: 'bg-indigo-100 text-indigo-600', step: 3 },
  IN_PROGRESS: { label: 'Fitting', class: 'bg-yellow-100 text-yellow-600', step: 4 },
  QC_PENDING: { label: 'QC Pending', class: 'bg-orange-100 text-orange-600', step: 5 },
  QC_PASSED: { label: 'QC Passed', class: 'bg-teal-100 text-teal-600', step: 6 },
  QC_FAILED: { label: 'QC Failed', class: 'bg-red-100 text-red-600', step: 5 },
  READY: { label: 'Ready', class: 'bg-green-100 text-green-600', step: 7 },
  DELIVERED: { label: 'Delivered', class: 'bg-emerald-100 text-emerald-600', step: 8 },
  CANCELLED: { label: 'Cancelled', class: 'bg-red-100 text-red-600', step: 0 },
};

const PRIORITY_CONFIG: Record<JobPriority, { label: string; class: string; icon: React.ComponentType<{ className?: string }> }> = {
  NORMAL: { label: 'Normal', class: 'text-gray-500', icon: Clock },
  EXPRESS: { label: 'Express', class: 'text-orange-500', icon: Timer },
  URGENT: { label: 'Urgent', class: 'text-red-500', icon: Zap },
};

export function WorkshopPage() {
  const { user } = useAuth();

  // Data state
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<JobStatus | 'ALL' | 'ACTIVE'>('ACTIVE');
  const [priorityFilter, setPriorityFilter] = useState<JobPriority | 'ALL'>('ALL');

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load jobs on mount
  useEffect(() => {
    loadJobs();
  }, [user?.activeStoreId]);

  const loadJobs = async () => {
    if (!user?.activeStoreId) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await workshopApi.getJobs(user.activeStoreId);
      const jobsData = response?.jobs || response || [];
      setJobs(Array.isArray(jobsData) ? jobsData : []);
    } catch {
      setError('Failed to load workshop jobs. Please try again.');
      setJobs([]);
    } finally {
      setIsLoading(false);
    }
  };

  // Filter jobs locally
  const filteredJobs = jobs.filter(job => {
    const matchesSearch = !searchQuery ||
      job.jobNumber?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      job.customerName?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      job.orderNumber?.toLowerCase().includes(searchQuery.toLowerCase());

    let matchesStatus = true;
    if (statusFilter === 'ACTIVE') {
      matchesStatus = !['DELIVERED', 'CANCELLED'].includes(job.status);
    } else if (statusFilter !== 'ALL') {
      matchesStatus = job.status === statusFilter;
    }

    const matchesPriority = priorityFilter === 'ALL' || job.priority === priorityFilter;

    return matchesSearch && matchesStatus && matchesPriority;
  });

  // Stats
  const activeJobs = jobs.filter(j => !['DELIVERED', 'CANCELLED'].includes(j.status));
  const urgentJobs = activeJobs.filter(j => j.priority === 'URGENT');
  const readyJobs = jobs.filter(j => j.status === 'READY');
  const overdueJobs = activeJobs.filter(j => new Date(j.promisedDate) < new Date());

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'short',
    });
  };

  const isOverdue = (promisedDate: string) => {
    return new Date(promisedDate) < new Date();
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Workshop</h1>
          <p className="text-gray-500">Manage lens fitting and job orders</p>
        </div>
        <button
          onClick={loadJobs}
          disabled={isLoading}
          className="btn-outline flex items-center gap-2"
        >
          {isLoading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          Refresh
        </button>
      </div>

      {/* Error State */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-600">
            <AlertTriangle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadJobs} className="ml-auto text-sm underline">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
              <Wrench className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Active Jobs</p>
              <p className="text-2xl font-bold text-gray-900">{activeJobs.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
              <Zap className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Urgent</p>
              <p className="text-2xl font-bold text-red-600">{urgentJobs.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-100 rounded-lg flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Ready for Pickup</p>
              <p className="text-2xl font-bold text-green-600">{readyJobs.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-orange-100 rounded-lg flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Overdue</p>
              <p className="text-2xl font-bold text-orange-600">{overdueJobs.length}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="input-field pl-10"
              placeholder="Search by job number, customer, order..."
            />
          </div>
          <div className="flex gap-2 flex-wrap">
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value as typeof statusFilter)}
              className="input-field w-auto"
            >
              <option value="ACTIVE">Active Jobs</option>
              <option value="ALL">All Status</option>
              {Object.entries(STATUS_CONFIG).map(([status, config]) => (
                <option key={status} value={status}>{config.label}</option>
              ))}
            </select>
            <select
              value={priorityFilter}
              onChange={e => setPriorityFilter(e.target.value as typeof priorityFilter)}
              className="input-field w-auto"
            >
              <option value="ALL">All Priority</option>
              <option value="URGENT">Urgent</option>
              <option value="EXPRESS">Express</option>
              <option value="NORMAL">Normal</option>
            </select>
          </div>
        </div>
      </div>

      {/* Jobs List */}
      <div className="space-y-3">
        {isLoading ? (
          <div className="card flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
          </div>
        ) : filteredJobs.length === 0 ? (
          <div className="card text-center py-12 text-gray-500">
            <Wrench className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>{searchQuery || statusFilter !== 'ACTIVE' || priorityFilter !== 'ALL' ? 'No jobs found matching your filters' : 'No workshop jobs'}</p>
          </div>
        ) : (
          filteredJobs.map(job => {
            const statusConfig = STATUS_CONFIG[job.status];
            const priorityConfig = PRIORITY_CONFIG[job.priority];
            const PriorityIcon = priorityConfig.icon;
            const overdue = isOverdue(job.promisedDate) && !['READY', 'DELIVERED', 'CANCELLED'].includes(job.status);

            return (
              <div
                key={job.id}
                className={clsx(
                  'card',
                  job.priority === 'URGENT' && 'border-red-300 bg-red-50',
                  overdue && job.priority !== 'URGENT' && 'border-orange-300 bg-orange-50'
                )}
              >
                <div className="flex items-start justify-between gap-4">
                  {/* Job Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-2">
                      <span className="font-bold text-gray-900">{job.jobNumber}</span>
                      <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', statusConfig.class)}>
                        {statusConfig.label}
                      </span>
                      <span className={clsx('flex items-center gap-1 text-xs font-medium', priorityConfig.class)}>
                        <PriorityIcon className="w-3 h-3" />
                        {priorityConfig.label}
                      </span>
                      {overdue && (
                        <span className="badge-error flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3" />
                          Overdue
                        </span>
                      )}
                    </div>

                    <div className="grid grid-cols-2 gap-4 text-sm">
                      <div>
                        <p className="text-gray-500">Customer</p>
                        <p className="font-medium flex items-center gap-1">
                          <User className="w-3 h-3" />
                          {job.customerName}
                        </p>
                        <p className="text-gray-500 flex items-center gap-1">
                          <Phone className="w-3 h-3" />
                          {job.customerPhone}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-500">Frame & Lens</p>
                        <p className="font-medium">{job.frameName}</p>
                        <p className="text-gray-500">{job.lensType}</p>
                      </div>
                    </div>

                    {job.notes && (
                      <p className="mt-2 text-sm text-yellow-700 bg-yellow-50 px-2 py-1 rounded">
                        Note: {job.notes}
                      </p>
                    )}
                  </div>

                  {/* Dates & Actions */}
                  <div className="text-right">
                    <div className="mb-3">
                      <p className="text-xs text-gray-500">Promise Date</p>
                      <p className={clsx(
                        'font-medium',
                        overdue ? 'text-red-600' : 'text-gray-900'
                      )}>
                        {formatDate(job.promisedDate)}
                      </p>
                    </div>
                    {job.assignedTo && (
                      <p className="text-xs text-gray-500 mb-3">
                        Assigned: {job.assignedTo}
                      </p>
                    )}
                    <button
                      onClick={() => setSelectedJob(job)}
                      className="btn-outline text-sm flex items-center gap-1"
                    >
                      <Eye className="w-4 h-4" />
                      View
                    </button>
                  </div>
                </div>

                {/* Progress Bar */}
                <div className="mt-4 pt-4 border-t border-gray-200">
                  <div className="flex items-center justify-between text-xs mb-2">
                    <span className="text-gray-500">Progress</span>
                    <span className="text-gray-500">{statusConfig.label}</span>
                  </div>
                  <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className={clsx(
                        'h-full transition-all duration-300',
                        job.status === 'QC_FAILED' ? 'bg-red-500' : 'bg-bv-red-600'
                      )}
                      style={{ width: `${(statusConfig.step / 8) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Job Detail Modal */}
      {selectedJob && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-gray-900">
                  Job {selectedJob.jobNumber}
                </h2>
                <button
                  onClick={() => setSelectedJob(null)}
                  className="p-2 hover:bg-gray-100 rounded-lg text-gray-500"
                >
                  ×
                </button>
              </div>

              <div className="space-y-4">
                {/* Status & Priority */}
                <div className="flex items-center gap-2">
                  <span className={clsx('px-3 py-1 rounded-full text-sm font-medium', STATUS_CONFIG[selectedJob.status].class)}>
                    {STATUS_CONFIG[selectedJob.status].label}
                  </span>
                  <span className={clsx('text-sm font-medium', PRIORITY_CONFIG[selectedJob.priority].class)}>
                    {selectedJob.priority}
                  </span>
                  {isOverdue(selectedJob.promisedDate) && !['READY', 'DELIVERED', 'CANCELLED'].includes(selectedJob.status) && (
                    <span className="px-2 py-1 bg-red-100 text-red-700 text-xs rounded-full font-medium">Overdue</span>
                  )}
                </div>

                {/* Customer */}
                <div className="bg-gray-50 rounded-lg p-4 space-y-2">
                  <h3 className="text-sm font-medium text-gray-500">Customer</h3>
                  <p className="font-medium text-gray-900 flex items-center gap-2">
                    <User className="w-4 h-4" /> {selectedJob.customerName}
                  </p>
                  <p className="text-sm text-gray-600 flex items-center gap-2">
                    <Phone className="w-4 h-4" /> {selectedJob.customerPhone}
                  </p>
                </div>

                {/* Job Details */}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-sm text-gray-500">Order Number</p>
                    <p className="font-medium">{selectedJob.orderNumber}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Frame</p>
                    <p className="font-medium">{selectedJob.frameName}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Lens Type</p>
                    <p className="font-medium">{selectedJob.lensType}</p>
                  </div>
                  {selectedJob.frameBarcode && (
                    <div>
                      <p className="text-sm text-gray-500">Frame Barcode</p>
                      <p className="font-medium font-mono text-sm">{selectedJob.frameBarcode}</p>
                    </div>
                  )}
                </div>

                {/* Dates */}
                <div className="grid grid-cols-2 gap-4 bg-gray-50 rounded-lg p-4">
                  <div>
                    <p className="text-sm text-gray-500">Created</p>
                    <p className="font-medium">{formatDate(selectedJob.createdAt)}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Promised Date</p>
                    <p className={clsx('font-medium', isOverdue(selectedJob.promisedDate) && 'text-red-600')}>
                      {formatDate(selectedJob.promisedDate)}
                    </p>
                  </div>
                  {selectedJob.assignedTo && (
                    <div>
                      <p className="text-sm text-gray-500">Assigned To</p>
                      <p className="font-medium">{selectedJob.assignedTo}</p>
                    </div>
                  )}
                  {selectedJob.completedAt && (
                    <div>
                      <p className="text-sm text-gray-500">Completed</p>
                      <p className="font-medium">{formatDate(selectedJob.completedAt)}</p>
                    </div>
                  )}
                </div>

                {/* Notes */}
                {selectedJob.notes && (
                  <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3">
                    <p className="text-sm font-medium text-yellow-800">Notes</p>
                    <p className="text-sm text-yellow-700 mt-1">{selectedJob.notes}</p>
                  </div>
                )}

                {/* Progress */}
                <div>
                  <p className="text-sm text-gray-500 mb-2">Progress: {STATUS_CONFIG[selectedJob.status].label}</p>
                  <div className="h-3 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className={clsx(
                        'h-full transition-all',
                        selectedJob.status === 'QC_FAILED' ? 'bg-red-500' : 'bg-bv-red-600'
                      )}
                      style={{ width: `${(STATUS_CONFIG[selectedJob.status].step / 8) * 100}%` }}
                    />
                  </div>
                </div>

                <button
                  onClick={() => setSelectedJob(null)}
                  className="btn-outline w-full"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default WorkshopPage;
