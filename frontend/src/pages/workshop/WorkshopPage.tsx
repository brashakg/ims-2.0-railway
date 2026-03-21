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
import { WorkshopJobCardPrint } from '../../components/print/WorkshopJobCardPrint';
import type { JobStatus, JobPriority } from '../../types';
import { workshopApi, orderApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
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
  PENDING: { label: 'Pending', class: 'bg-gray-700 text-gray-300', step: 1 },
  PROCESSING: { label: 'Fitting', class: 'bg-yellow-900 text-yellow-300', step: 2 },
  COMPLETED: { label: 'Completed', class: 'bg-blue-900 text-blue-300', step: 3 },
  QC_FAILED: { label: 'QC Failed', class: 'bg-red-900 text-red-300', step: 2 },
  READY: { label: 'Ready for Pickup', class: 'bg-green-900 text-green-300', step: 4 },
  DELIVERED: { label: 'Delivered', class: 'bg-emerald-900 text-emerald-300', step: 5 },
  // Fallback for legacy statuses
  CREATED: { label: 'Created', class: 'bg-gray-700 text-gray-400', step: 1 },
  LENS_ORDERED: { label: 'Lens Ordered', class: 'bg-blue-900 text-blue-300', step: 2 },
  LENS_RECEIVED: { label: 'Lens Received', class: 'bg-indigo-900 text-indigo-300', step: 3 },
  QC_PENDING: { label: 'QC Pending', class: 'bg-orange-900 text-orange-300', step: 3 },
  QC_PASSED: { label: 'QC Passed', class: 'bg-teal-900 text-teal-300', step: 4 },
  CANCELLED: { label: 'Cancelled', class: 'bg-red-900 text-red-300', step: 0 },
};

const PRIORITY_CONFIG: Record<JobPriority, { label: string; class: string; icon: React.ComponentType<{ className?: string }> }> = {
  NORMAL: { label: 'Normal', class: 'text-gray-400', icon: Clock },
  EXPRESS: { label: 'Express', class: 'text-orange-500', icon: Timer },
  URGENT: { label: 'Urgent', class: 'text-red-500', icon: Zap },
};

export function WorkshopPage() {
  const { user } = useAuth();
  const toast = useToast();

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
    if (!user?.activeStoreId) {
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await workshopApi.getJobs(user.activeStoreId);
      const jobsData = response?.jobs || response || [];
      setJobs(Array.isArray(jobsData) ? jobsData : []);
      
      // Load store info for printing
      if (!storeInfo) {
        try {
          const store = await (orderApi as any).getStore?.(user.activeStoreId);
          if (store) {
            setStoreInfo({
              storeName: store.storeName || store.name || 'Better Vision Optics',
              address: store.address || '',
              city: store.city || '',
              state: store.state || '',
              pincode: store.pincode || '',
            });
          }
        } catch {
          // Store info is optional
        }
      }
    } catch {
      setError('Failed to load workshop jobs. Please try again.');
      setJobs([]);
    } finally {
      setIsLoading(false);
    }
  };;

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

  const [showCreateJob, setShowCreateJob] = useState(false);
  const [printJob, setPrintJob] = useState<Job | null>(null);  const [storeInfo, setStoreInfo] = useState<any>(null);
  const [createOrderSearch, setCreateOrderSearch] = useState('');
  const [createOrders, setCreateOrders] = useState<any[]>([]);
  const [createSelectedOrder, setCreateSelectedOrder] = useState<any>(null);
  const [createFitting, setCreateFitting] = useState('');
  const [createNotes, setCreateNotes] = useState('');
  const [createPriority, setCreatePriority] = useState<'NORMAL' | 'EXPRESS' | 'URGENT'>('NORMAL');
  const [createExpectedDate, setCreateExpectedDate] = useState(new Date(Date.now() + 3 * 86400000).toISOString().split('T')[0]);
  const [createLoading, setCreateLoading] = useState(false);

  const searchOrdersForJob = async () => {
    if (!createOrderSearch.trim()) return;
    try {
      const res = await orderApi.getOrders({ storeId: user?.activeStoreId });
      const all = res?.orders || res || [];
      setCreateOrders(all.filter((o: any) =>
        (o.orderNumber || '').toLowerCase().includes(createOrderSearch.toLowerCase()) ||
        (o.customerName || '').toLowerCase().includes(createOrderSearch.toLowerCase())
      ).slice(0, 10));
    } catch { setCreateOrders([]); }
  };

  const handleCreateJob = async () => {
    if (!createSelectedOrder) return;
    setCreateLoading(true);
    try {
      const rxItem = (createSelectedOrder.items || []).find((i: any) => i.category === 'RX_LENSES' || i.is_optical);
      await workshopApi.createJob({
        order_id: createSelectedOrder.id,
        frame_details: { items: (createSelectedOrder.items || []).filter((i: any) => i.category === 'FRAMES' || i.category === 'SUNGLASSES') },
        lens_details: rxItem?.lens_details || { type: 'STANDARD' },
        prescription_id: rxItem?.prescription_id || '',
        fitting_instructions: createFitting || undefined,
        special_notes: createNotes || undefined,
        expected_date: createExpectedDate,
      });
      setShowCreateJob(false);
      setCreateSelectedOrder(null);
      setCreateFitting('');
      setCreateNotes('');
      await loadJobs();
    } catch {
      // Error handling — silently retry
    } finally {
      setCreateLoading(false);
    }
  };

  const handleStatusChange = async (jobId: string, newStatus: string) => {
    try {
      await workshopApi.updateJobStatus(jobId, newStatus);
      toast.success(`Job status updated to ${newStatus}`);
      setSelectedJob(null);
      await loadJobs();
    } catch {
      toast.error('Failed to update job status');
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Workshop</h1>
          <p className="text-gray-400">Manage lens fitting and job orders</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowCreateJob(true)}
            className="btn-primary flex items-center gap-2 text-sm">
            <Wrench className="w-4 h-4" /> Create Job from Order
          </button>
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
      </div>

      {/* Error State */}
      {error && (
        <div className="card bg-red-900 border-red-700">
          <div className="flex items-center gap-3 text-red-200">
            <AlertTriangle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadJobs} className="ml-auto text-sm underline hover:text-red-100">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-900 rounded-lg flex items-center justify-center">
              <Wrench className="w-5 h-5 text-blue-400" />
            </div>
            <div>
              <p className="text-sm text-gray-400">Active Jobs</p>
              <p className="text-2xl font-bold text-white">{activeJobs.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-900 rounded-lg flex items-center justify-center">
              <Zap className="w-5 h-5 text-red-400" />
            </div>
            <div>
              <p className="text-sm text-gray-400">Urgent</p>
              <p className="text-2xl font-bold text-red-400">{urgentJobs.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-900 rounded-lg flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-green-400" />
            </div>
            <div>
              <p className="text-sm text-gray-400">Ready for Pickup</p>
              <p className="text-2xl font-bold text-green-400">{readyJobs.length}</p>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-orange-900 rounded-lg flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-orange-400" />
            </div>
            <div>
              <p className="text-sm text-gray-400">Overdue</p>
              <p className="text-2xl font-bold text-orange-400">{overdueJobs.length}</p>
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
          <div className="card text-center py-12 text-gray-400">
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
                  job.priority === 'URGENT' && 'border-red-700 bg-red-900/20',
                  overdue && job.priority !== 'URGENT' && 'border-orange-700 bg-orange-900/20'
                )}
              >
                <div className="flex items-start justify-between gap-4">
                  {/* Job Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-2">
                      <span className="font-bold text-white">{job.jobNumber}</span>
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
                        <p className="text-gray-400">Customer</p>
                        <p className="font-medium flex items-center gap-1">
                          <User className="w-3 h-3" />
                          {job.customerName}
                        </p>
                        <p className="text-gray-400 flex items-center gap-1">
                          <Phone className="w-3 h-3" />
                          {job.customerPhone}
                        </p>
                      </div>
                      <div>
                        <p className="text-gray-400">Frame & Lens</p>
                        <p className="font-medium">{job.frameName}</p>
                        <p className="text-gray-400">{job.lensType}</p>
                      </div>
                    </div>

                    {job.notes && (
                      <p className="mt-2 text-sm text-yellow-200 bg-yellow-900/30 px-2 py-1 rounded">
                        Note: {job.notes}
                      </p>
                    )}
                  </div>

                  {/* Dates & Actions */}
                  <div className="text-right">
                    <div className="mb-3">
                      <p className="text-xs text-gray-400">Promise Date</p>
                      <p className={clsx(
                        'font-medium',
                        overdue ? 'text-red-600' : 'text-white'
                      )}>
                        {formatDate(job.promisedDate)}
                      </p>
                    </div>
                    {job.assignedTo && (
                      <p className="text-xs text-gray-400 mb-3">
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
                <div className="mt-4 pt-4 border-t border-gray-700">
                  <div className="flex items-center justify-between text-xs mb-2">
                    <span className="text-gray-400">Progress</span>
                    <span className="text-gray-400">{statusConfig.label}</span>
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
          <div className="bg-gray-800 rounded-xl shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-white">
                  Job {selectedJob.jobNumber}
                </h2>
                <button
                  onClick={() => setSelectedJob(null)}
                  className="p-2 hover:bg-gray-700 rounded-lg text-gray-400"
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
                    <span className="px-2 py-1 bg-red-900 text-red-300 text-xs rounded-full font-medium">Overdue</span>
                  )}
                </div>

                {/* Customer */}
                <div className="bg-gray-900 rounded-lg p-4 space-y-2">
                  <h3 className="text-sm font-medium text-gray-400">Customer</h3>
                  <p className="font-medium text-white flex items-center gap-2">
                    <User className="w-4 h-4" /> {selectedJob.customerName}
                  </p>
                  <p className="text-sm text-gray-400 flex items-center gap-2">
                    <Phone className="w-4 h-4" /> {selectedJob.customerPhone}
                  </p>
                </div>

                {/* Job Details */}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-sm text-gray-400">Order Number</p>
                    <p className="font-medium">{selectedJob.orderNumber}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-400">Frame</p>
                    <p className="font-medium">{selectedJob.frameName}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-400">Lens Type</p>
                    <p className="font-medium">{selectedJob.lensType}</p>
                  </div>
                  {selectedJob.frameBarcode && (
                    <div>
                      <p className="text-sm text-gray-400">Frame Barcode</p>
                      <p className="font-medium font-mono text-sm">{selectedJob.frameBarcode}</p>
                    </div>
                  )}
                </div>

                {/* Dates */}
                <div className="grid grid-cols-2 gap-4 bg-gray-900 rounded-lg p-4">
                  <div>
                    <p className="text-sm text-gray-400">Created</p>
                    <p className="font-medium">{formatDate(selectedJob.createdAt)}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-400">Promised Date</p>
                    <p className={clsx('font-medium', isOverdue(selectedJob.promisedDate) && 'text-red-600')}>
                      {formatDate(selectedJob.promisedDate)}
                    </p>
                  </div>
                  {selectedJob.assignedTo && (
                    <div>
                      <p className="text-sm text-gray-400">Assigned To</p>
                      <p className="font-medium">{selectedJob.assignedTo}</p>
                    </div>
                  )}
                  {selectedJob.completedAt && (
                    <div>
                      <p className="text-sm text-gray-400">Completed</p>
                      <p className="font-medium">{formatDate(selectedJob.completedAt)}</p>
                    </div>
                  )}
                </div>

                {/* Notes */}
                {selectedJob.notes && (
                  <div className="bg-yellow-900/20 border border-yellow-700 rounded-lg p-3">
                    <p className="text-sm font-medium text-yellow-300">Notes</p>
                    <p className="text-sm text-yellow-200 mt-1">{selectedJob.notes}</p>
                  </div>
                )}

                {/* Progress */}
                <div>
                  <p className="text-sm text-gray-400 mb-2">Progress: {STATUS_CONFIG[selectedJob.status].label}</p>
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

                {/* Status Transition Buttons */}
                <div className="flex gap-2 flex-wrap">
                  {selectedJob.status === 'PENDING' && (
                    <button onClick={() => handleStatusChange(selectedJob.id, 'PROCESSING')} className="btn-primary text-sm">Start Processing</button>
                  )}
                  {selectedJob.status === 'PROCESSING' && (
                    <button onClick={() => handleStatusChange(selectedJob.id, 'COMPLETED')} className="btn-primary text-sm">Mark Completed</button>
                  )}
                  {selectedJob.status === 'COMPLETED' && (
                    <>
                      <button onClick={() => handleStatusChange(selectedJob.id, 'READY')} className="btn-success text-sm">QC Passed - Ready</button>
                      <button onClick={() => handleStatusChange(selectedJob.id, 'QC_FAILED')} className="btn-outline text-sm text-red-600 border-red-600">QC Failed</button>
                    </>
                  )}
                  {selectedJob.status === 'QC_FAILED' && (
                    <button onClick={() => handleStatusChange(selectedJob.id, 'PROCESSING')} className="btn-primary text-sm">Rework</button>
                  )}
                  {selectedJob.status === 'READY' && (
                    <button onClick={() => handleStatusChange(selectedJob.id, 'DELIVERED')} className="btn-success text-sm">Mark Delivered</button>
                  )}
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      setPrintJob(selectedJob);
                      setSelectedJob(null);
                    }}
                    className="btn-primary flex-1 flex items-center justify-center gap-2"
                  >
                    <Eye className="w-4 h-4" />
                    Print Card
                  </button>
                  <button
                    onClick={() => setSelectedJob(null)}
                    className="btn-outline flex-1"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Print Job Card Modal */}
      {printJob && storeInfo && (
        <WorkshopJobCardPrint
          job={{
            jobNumber: printJob.jobNumber,
            orderNumber: printJob.orderNumber,
            customerName: printJob.customerName,
            customerPhone: printJob.customerPhone,
            frameBrand: (printJob.frameName || '').split(' ')[0],
            frameModel: (printJob.frameName || '').replace(/^[^ ]+ /, ''),
            frameColor: '',
            lensType: printJob.lensType,
            priority: printJob.priority,
            dueDate: printJob.promisedDate,
            assignedTechnician: printJob.assignedTo,
            status: STATUS_CONFIG[printJob.status].label,
            createdDate: printJob.createdAt,
          }}
          store={storeInfo}
          onClose={() => setPrintJob(null)}
        />
      )}

      {/* CREATE JOB MODAL */}
      {showCreateJob && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-800 rounded-xl shadow-2xl w-full max-w-lg max-h-[85vh] overflow-y-auto">
            <div className="p-5 border-b border-gray-700 flex items-center justify-between">
              <h3 className="font-semibold text-white">Create Workshop Job from Order</h3>
              <button onClick={() => { setShowCreateJob(false); setCreateSelectedOrder(null); setCreateOrders([]); }} className="p-1 hover:bg-gray-700 rounded text-gray-400 hover:text-gray-200">
                ×
              </button>
            </div>
            <div className="p-5 space-y-4">
              {!createSelectedOrder ? (
                <>
                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                      <input value={createOrderSearch} onChange={e => setCreateOrderSearch(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && searchOrdersForJob()}
                        placeholder="Search order number or customer..."
                        className="w-full pl-9 pr-4 py-2.5 border border-gray-600 bg-gray-700 text-white rounded-lg text-sm placeholder-gray-400" />
                    </div>
                    <button onClick={searchOrdersForJob} className="px-4 py-2 bg-bv-gold-500 text-white rounded-lg text-sm font-semibold hover:bg-bv-gold-600">Search</button>
                  </div>
                  {createOrders.length > 0 && (
                    <div className="space-y-1.5 max-h-60 overflow-y-auto">
                      {createOrders.map((o: any) => (
                        <button key={o.id} onClick={() => setCreateSelectedOrder(o)}
                          className="w-full flex items-center justify-between p-3 rounded-lg border border-gray-600 hover:border-bv-gold-400 hover:bg-gray-700 text-left text-white transition-colors">
                          <div>
                            <p className="text-sm font-medium">{o.orderNumber}</p>
                            <p className="text-xs text-gray-400">{o.customerName} · {(o.items || []).length} items</p>
                          </div>
                          <span className="text-sm font-bold text-bv-gold-300">₹{Math.round(o.grandTotal || 0).toLocaleString('en-IN')}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="bg-gray-900 rounded-lg p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium text-sm">{createSelectedOrder.orderNumber}</p>
                        <p className="text-xs text-gray-400">{createSelectedOrder.customerName}</p>
                      </div>
                      <button onClick={() => setCreateSelectedOrder(null)} className="text-xs text-bv-gold-600 hover:underline">Change</button>
                    </div>
                    <div className="mt-2 space-y-1">
                      {(createSelectedOrder.items || []).map((item: any, i: number) => (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <span className="text-white">{item.productName || item.product_name || item.name}</span>
                          <span className="text-gray-400 text-xs">{item.category}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="text-xs text-gray-400 block mb-1">Priority</label>
                    <div className="flex gap-2">
                      {(['NORMAL', 'EXPRESS', 'URGENT'] as const).map(p => (
                        <button key={p} onClick={() => setCreatePriority(p)}
                          className={clsx('flex-1 py-2 rounded-lg text-xs font-medium border-2 transition-all',
                            createPriority === p
                              ? p === 'URGENT' ? 'border-red-500 bg-red-900 text-red-300'
                                : p === 'EXPRESS' ? 'border-amber-500 bg-amber-900 text-amber-300'
                                  : 'border-bv-gold-500 bg-bv-gold-900 text-bv-gold-300'
                              : 'border-gray-600 text-gray-300 bg-gray-700')}>
                          {p}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="text-xs text-gray-400 block mb-1">Expected Delivery Date</label>
                    <input type="date" value={createExpectedDate} onChange={e => setCreateExpectedDate(e.target.value)}
                      min={new Date().toISOString().split('T')[0]}
                      className="w-full px-3 py-2 border border-gray-600 bg-gray-700 text-white rounded-lg text-sm" />
                  </div>

                  <div>
                    <label className="text-xs text-gray-400 block mb-1">Fitting Instructions</label>
                    <textarea value={createFitting} onChange={e => setCreateFitting(e.target.value)}
                      placeholder="PD, segment height, tilt, wrap angle, frame adjustments..."
                      className="w-full px-3 py-2 border border-gray-600 bg-gray-700 text-white rounded-lg text-sm h-16 resize-none placeholder-gray-500" />
                  </div>

                  <div>
                    <label className="text-xs text-gray-400 block mb-1">Special Notes for Workshop</label>
                    <textarea value={createNotes} onChange={e => setCreateNotes(e.target.value)}
                      placeholder="Tint, drill mount, special coating, customer preferences..."
                      className="w-full px-3 py-2 border border-gray-600 bg-gray-700 text-white rounded-lg text-sm h-16 resize-none placeholder-gray-500" />
                  </div>
                </>
              )}
            </div>
            {createSelectedOrder && (
              <div className="p-5 border-t border-gray-700 flex gap-2">
                <button onClick={() => { setShowCreateJob(false); setCreateSelectedOrder(null); }}
                  className="flex-1 px-4 py-2.5 border border-gray-600 text-gray-300 rounded-lg text-sm hover:bg-gray-700">Cancel</button>
                <button onClick={handleCreateJob} disabled={createLoading}
                  className="flex-1 px-4 py-2.5 bg-bv-gold-500 text-white rounded-lg text-sm font-semibold hover:bg-bv-gold-600 disabled:opacity-50">
                  {createLoading ? 'Creating...' : 'Create Job'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default WorkshopPage;
