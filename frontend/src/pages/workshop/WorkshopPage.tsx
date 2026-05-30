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
  Printer,
  Tag,
  ClipboardCheck,
  X,
} from 'lucide-react';
import { WorkshopJobCardPrint } from '../../components/print/WorkshopJobCardPrint';
import type { JobStatus, JobPriority } from '../../types';
import { workshopApi, orderApi, vendorsApi } from '../../services/api';
import { settingsApi } from '../../services/api/settings';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';
// Thermal label system (scan-to-advance + labels). Imported DIRECTLY from
// their modules (not via the services/api barrel — new-service barrel
// re-exports fail to resolve, TS2614).
import { ScanToAdvance } from '../../components/labels/ScanToAdvance';
import { StageMonitorBoard } from '../../components/labels/StageMonitorBoard';
import { LabelPreviewModal } from '../../components/labels/LabelPreviewModal';
import type { LabelModalSpec } from '../../components/labels/LabelPreviewModal';
import { printJobLabel } from '../../components/labels/printLabel';

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
  // Lens-order lifecycle (snake_case — passes through job_to_frontend as-is).
  lens_status?: LensStatus;
  lens_ordered_at?: string;
  lens_received_at?: string;
  lens_mounted_at?: string;
  ready_notified_at?: string;
}

// Lens-order lifecycle: forward-only NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED.
type LensStatus = 'NOT_ORDERED' | 'ORDERED' | 'RECEIVED' | 'MOUNTED';
const LENS_STATUS_CONFIG: Record<LensStatus, { label: string; class: string; next?: LensStatus; nextLabel?: string }> = {
  NOT_ORDERED: { label: 'Lens: Not ordered', class: 'bg-gray-100 text-gray-600', next: 'ORDERED', nextLabel: 'Mark lens ordered' },
  ORDERED: { label: 'Lens: Ordered', class: 'bg-blue-50 text-blue-700', next: 'RECEIVED', nextLabel: 'Mark lens received' },
  RECEIVED: { label: 'Lens: Received', class: 'bg-indigo-50 text-indigo-700', next: 'MOUNTED', nextLabel: 'Mark lens mounted' },
  MOUNTED: { label: 'Lens: Mounted', class: 'bg-green-50 text-green-700' },
};
function resolveLensConfig(status: unknown) {
  const key = (typeof status === 'string' ? status : 'NOT_ORDERED') as LensStatus;
  return LENS_STATUS_CONFIG[key] ?? LENS_STATUS_CONFIG.NOT_ORDERED;
}

// Audit Run #2 fix: the workshop page was crashing via error boundary when
// a job doc came back with a status value not in this map (e.g. null, "",
// or a legacy status). The `resolveStatusConfig` / `resolvePriorityConfig`
// helpers below guarantee a sane fallback object instead of undefined.
const UNKNOWN_STATUS = { label: 'Unknown', class: 'bg-gray-100 text-gray-700', step: 0 };
const STATUS_CONFIG: Record<JobStatus, { label: string; class: string; step: number }> = {
  PENDING: { label: 'Pending', class: 'bg-gray-100 text-gray-700', step: 1 },
  PROCESSING: { label: 'Fitting', class: 'bg-yellow-50 text-yellow-700', step: 2 },
  COMPLETED: { label: 'Completed', class: 'bg-blue-50 text-blue-700', step: 3 },
  QC_FAILED: { label: 'QC Failed', class: 'bg-red-50 text-red-700', step: 2 },
  READY: { label: 'Ready for Pickup', class: 'bg-green-50 text-green-700', step: 4 },
  DELIVERED: { label: 'Delivered', class: 'bg-emerald-50 text-emerald-700', step: 5 },
  // Fallback for legacy statuses
  CREATED: { label: 'Created', class: 'bg-gray-100 text-gray-500', step: 1 },
  LENS_ORDERED: { label: 'Lens Ordered', class: 'bg-blue-50 text-blue-700', step: 2 },
  LENS_RECEIVED: { label: 'Lens Received', class: 'bg-indigo-50 text-indigo-700', step: 3 },
  QC_PENDING: { label: 'QC Pending', class: 'bg-orange-50 text-orange-700', step: 3 },
  QC_PASSED: { label: 'QC Passed', class: 'bg-teal-50 text-teal-700', step: 4 },
  CANCELLED: { label: 'Cancelled', class: 'bg-red-50 text-red-700', step: 0 },
};

const UNKNOWN_PRIORITY = { label: '—', class: 'text-gray-500', icon: Clock };
const PRIORITY_CONFIG: Record<JobPriority, { label: string; class: string; icon: React.ComponentType<{ className?: string }> }> = {
  NORMAL: { label: 'Normal', class: 'text-gray-500', icon: Clock },
  EXPRESS: { label: 'Express', class: 'text-orange-500', icon: Timer },
  URGENT: { label: 'Urgent', class: 'text-red-500', icon: Zap },
};

// Guarded lookups — audit Run #2 found unguarded accesses throwing to the
// error boundary when the backend returned a status/priority not in the
// maps above. Always return a valid object.
function resolveStatusConfig(status: unknown) {
  const key = (typeof status === 'string' ? status : '') as JobStatus;
  return STATUS_CONFIG[key] ?? UNKNOWN_STATUS;
}
function resolvePriorityConfig(priority: unknown) {
  const key = (typeof priority === 'string' ? priority : 'NORMAL') as JobPriority;
  return PRIORITY_CONFIG[key] ?? UNKNOWN_PRIORITY;
}

export function WorkshopPage() {
  const { user } = useAuth();
  const toast = useToast();

  // Data state
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  // Phase 6.4 — server-side KPIs. Falls back to client-side counts if
  // the endpoint is unreachable or the backend doesn't expose it yet.
  const [kpis, setKpis] = useState<{
    pending: number;
    qc_failed: number;
    ready_for_pickup: number;
    overdue: number;
    completed_today: number;
    avg_turnaround_days: number | null;
  } | null>(null);

  // UI state
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<JobStatus | 'ALL' | 'ACTIVE'>('ACTIVE');
  const [priorityFilter, setPriorityFilter] = useState<JobPriority | 'ALL'>('ALL');

  // QC checklist modal — opened from a COMPLETED / QC_FAILED job's detail panel.
  const [qcModalJob, setQcModalJob] = useState<Job | null>(null);
  // Who may run QC. Workshop floor + store/area management + optometrist
  // (power verification is optometry-adjacent). Plain cashiers/sales don't.
  const QC_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'OPTOMETRIST', 'WORKSHOP_STAFF'];
  const canRunQc = QC_ROLES.includes(user?.activeRole || '');

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
      // Fetch jobs + server-side KPIs in parallel. KPIs use allSettled
      // because a missing endpoint on older backends shouldn't prevent
      // the page from rendering the jobs list.
      const [jobsResp, kpisResp] = await Promise.allSettled([
        workshopApi.getJobs(user.activeStoreId),
        workshopApi.getDashboardKpis(user.activeStoreId),
      ]);
      if (jobsResp.status === 'fulfilled') {
        const jobsData = jobsResp.value?.jobs || jobsResp.value || [];
        setJobs(Array.isArray(jobsData) ? jobsData : []);
      } else {
        // Audit Run #2: don't throw — set an inline error instead so the
        // page still renders empty shells + user sees why. Previous
        // behaviour threw, which set the error state but only AFTER the
        // first render cycle, and a status-config crash could catch the
        // throw mid-render.
        // eslint-disable-next-line no-console
        console.warn('[Workshop] getJobs failed:', jobsResp.reason);
        setJobs([]);
        setError('Workshop jobs unavailable right now. Other functionality still works.');
      }
      if (kpisResp.status === 'fulfilled') {
        setKpis(kpisResp.value);
      } else {
        // eslint-disable-next-line no-console
        console.warn('[Workshop] getDashboardKpis failed (non-fatal):', kpisResp.reason);
      }

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
  const [printJob, setPrintJob] = useState<Job | null>(null);
  // Thermal label modal (traveler / stage / ready / product).
  const [labelSpec, setLabelSpec] = useState<LabelModalSpec | null>(null);
  const [storeInfo, setStoreInfo] = useState<any>(null);
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
      const created = await workshopApi.createJob({
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
      // Offer the work-order traveler label for the freshly created job so it
      // can be attached to the physical job + scanned through the workflow.
      const newJobId = created?.job_id || created?.id;
      if (newJobId) {
        setLabelSpec({ kind: 'job', jobId: newJobId, type: 'traveler' });
      }
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
      // Auto-print the appropriate label on a forward transition (fail-soft;
      // printJobLabel falls back to an HTML print window when QZ is absent and
      // is a no-op silent failure on error). READY -> pickup label, else the
      // stage sticker. Honours the auto_print_stage_sticker printer setting.
      if (!['QC_FAILED', 'CANCELLED'].includes(newStatus)) {
        try {
          const s = await settingsApi.getPrinterSettings();
          if ((s as any)?.auto_print_stage_sticker !== false) {
            const labelType = newStatus === 'READY' ? 'ready' : 'stage';
            printJobLabel(jobId, labelType).catch(() => { /* fail-soft */ });
          }
        } catch {
          /* settings unavailable -> skip auto-print, never block */
        }
      }
    } catch {
      toast.error('Failed to update job status');
    }
  };

  // Submit a QC checklist result via the dedicated /qc endpoint (persists
  // qc_passed / qc_notes / qc_by / qc_at). Pass -> READY, fail -> QC_FAILED.
  const [qcBusy, setQcBusy] = useState(false);
  const handleQcSubmit = async (jobId: string, passed: boolean, notes: string) => {
    setQcBusy(true);
    try {
      const res = await workshopApi.qcJob(jobId, passed, notes);
      toast.success(passed ? 'QC passed — job ready for pickup' : 'QC failed — job flagged for rework');
      setQcModalJob(null);
      setSelectedJob(null);
      await loadJobs();
      // On a pass the job is now READY — auto-print the pickup label, honouring
      // the auto_print_stage_sticker setting (fail-soft, mirrors handleStatusChange).
      if (res?.status === 'READY') {
        try {
          const s = await settingsApi.getPrinterSettings();
          if ((s as any)?.auto_print_stage_sticker !== false) {
            printJobLabel(jobId, 'ready').catch(() => { /* fail-soft */ });
          }
        } catch {
          /* settings unavailable -> skip auto-print, never block */
        }
      }
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to record QC';
      toast.error(msg);
    } finally {
      setQcBusy(false);
    }
  };

  // Send a QC-failed job back to the bench via the dedicated /rework endpoint
  // (increments rework_count; QC_FAILED -> IN_PROGRESS).
  const handleRework = async (jobId: string) => {
    setQcBusy(true);
    try {
      const res = await workshopApi.reworkJob(jobId);
      toast.success(res?.rework_count ? `Sent for rework (attempt #${res.rework_count})` : 'Job sent for rework');
      setSelectedJob(null);
      await loadJobs();
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to send job for rework';
      toast.error(msg);
    } finally {
      setQcBusy(false);
    }
  };

  // Advance the lens lifecycle one step (forward-only; backend enforces).
  const [lensBusy, setLensBusy] = useState(false);
  const handleLensAdvance = async (jobId: string, nextStatus: LensStatus) => {
    setLensBusy(true);
    try {
      await workshopApi.updateLensStatus(jobId, nextStatus);
      toast.success(`Lens ${nextStatus.toLowerCase().replace('_', ' ')}`);
      setSelectedJob(null);
      await loadJobs();
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to update lens status';
      toast.error(msg);
    } finally {
      setLensBusy(false);
    }
  };

  // Notify the customer their job is ready for pickup.
  const [notifyBusy, setNotifyBusy] = useState(false);
  const handleNotifyReady = async (jobId: string) => {
    setNotifyBusy(true);
    try {
      const res = await workshopApi.notifyReady(jobId);
      const wa = res?.whatsapp_status;
      if (wa === 'SENT') {
        toast.success('Customer notified via WhatsApp');
      } else if (wa === 'SIMULATED') {
        toast.success('Pickup notification logged (dispatch off — not sent live)');
      } else if (wa === 'no_phone') {
        toast.warning('No customer phone on file — logged only');
      } else {
        toast.warning('Notification logged but WhatsApp send failed');
      }
      await loadJobs();
    } catch {
      toast.error('Failed to send pickup notification');
    } finally {
      setNotifyBusy(false);
    }
  };

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Workshop</div>
          <h1>From Rx to finished job.</h1>
          <div className="hint">Lens ordered → received → mounted → QC. Assign by technician, auto-notify when ready, customer pickup with OTP verify.</div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button onClick={() => setShowCreateJob(true)} className="btn sm primary">
            <Wrench className="w-4 h-4" /> New job from order
          </button>
          <button onClick={loadJobs} disabled={isLoading} className="btn sm">
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="card bg-red-50 border-red-700">
          <div className="flex items-center gap-3 text-red-200">
            <AlertTriangle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadJobs} className="ml-auto text-sm underline hover:text-red-100">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Stats Cards — Phase 6.4: server-side KPIs with client fallback. */}
      <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
              <Wrench className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Active Jobs</p>
              <p className="text-2xl font-bold text-gray-900">{kpis?.pending ?? activeJobs.length}</p>
              {kpis?.completed_today !== undefined && kpis?.completed_today !== null && (
                <p className="text-xs text-gray-500">{kpis.completed_today} completed today</p>
              )}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-red-50 rounded-lg flex items-center justify-center">
              <Zap className="w-5 h-5 text-red-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Urgent</p>
              <p className="text-2xl font-bold text-red-600">{urgentJobs.length}</p>
              {kpis?.qc_failed ? (
                <p className="text-xs text-red-500">{kpis.qc_failed} in QC rework</p>
              ) : null}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Ready for Pickup</p>
              <p className="text-2xl font-bold text-green-600">{kpis?.ready_for_pickup ?? readyJobs.length}</p>
              {kpis?.avg_turnaround_days != null && (
                <p className="text-xs text-gray-500">Avg {kpis.avg_turnaround_days}d turnaround</p>
              )}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-orange-50 rounded-lg flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-orange-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Overdue</p>
              <p className="text-2xl font-bold text-orange-600">{kpis?.overdue ?? overdueJobs.length}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Scan-to-advance box (keyboard-wedge). Resolves a scanned code to a
          job in THIS store, advances its stage (gated, no skip), auto-prints
          the next stage sticker, and refreshes on success. */}
      <ScanToAdvance
        resolveJobId={(code) => {
          const c = code.trim().toUpperCase();
          const match = jobs.find(
            (j) =>
              (j.jobNumber || '').toUpperCase() === c ||
              (j.id || '').toUpperCase() === c ||
              (!!j.jobNumber && c.includes(j.jobNumber.toUpperCase())) ||
              (!!j.id && c.includes(j.id.toUpperCase())),
          );
          return match ? match.id : null;
        }}
        onAdvanced={(res) => {
          toast.success(res.message);
          loadJobs();
        }}
      />

      {/* Jobs-by-stage monitor board (store-scoped live visibility). */}
      <StageMonitorBoard
        jobs={jobs.map((j) => ({
          id: j.id,
          jobNumber: j.jobNumber,
          customerName: j.customerName,
          status: j.status,
          priority: j.priority,
          promisedDate: j.promisedDate,
        }))}
        onPrintStage={(jobId) => setLabelSpec({ kind: 'job', jobId, type: 'stage' })}
        onSelectJob={(jobId) => {
          const j = jobs.find((x) => x.id === jobId);
          if (j) setSelectedJob(j);
        }}
      />

      {/* Filters */}
      <div className="card">
        <div className="flex flex-col tablet:flex-row gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
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
            const statusConfig = resolveStatusConfig(job.status);
            const priorityConfig = resolvePriorityConfig(job.priority);
            const PriorityIcon = priorityConfig.icon;
            const overdue = isOverdue(job.promisedDate) && !['READY', 'DELIVERED', 'CANCELLED'].includes(job.status);

            return (
              <div
                key={job.id}
                className={clsx(
                  'card',
                  job.priority === 'URGENT' && 'border-red-700 bg-red-50/20',
                  overdue && job.priority !== 'URGENT' && 'border-orange-700 bg-orange-50/20'
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
                      <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', resolveLensConfig(job.lens_status).class)}>
                        {resolveLensConfig(job.lens_status).label}
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
                      <p className="mt-2 text-sm text-yellow-200 bg-yellow-50/30 px-2 py-1 rounded">
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
                  <span className={clsx('px-3 py-1 rounded-full text-sm font-medium', resolveStatusConfig(selectedJob.status).class)}>
                    {resolveStatusConfig(selectedJob.status).label}
                  </span>
                  <span className={clsx('text-sm font-medium', resolvePriorityConfig(selectedJob.priority).class)}>
                    {selectedJob.priority}
                  </span>
                  {isOverdue(selectedJob.promisedDate) && !['READY', 'DELIVERED', 'CANCELLED'].includes(selectedJob.status) && (
                    <span className="px-2 py-1 bg-red-50 text-red-700 text-xs rounded-full font-medium">Overdue</span>
                  )}
                </div>

                {/* Customer */}
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-2">
                  <h3 className="text-sm font-medium text-gray-500">Customer</h3>
                  <p className="font-medium text-gray-900 flex items-center gap-2">
                    <User className="w-4 h-4" /> {selectedJob.customerName}
                  </p>
                  <p className="text-sm text-gray-500 flex items-center gap-2">
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
                <div className="grid grid-cols-2 gap-4 bg-gray-50 border border-gray-200 rounded-lg p-4">
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
                  <div className="bg-yellow-50/20 border border-yellow-700 rounded-lg p-3">
                    <p className="text-sm font-medium text-yellow-700">Notes</p>
                    <p className="text-sm text-yellow-200 mt-1">{selectedJob.notes}</p>
                  </div>
                )}

                {/* Vendor / lens lab — vendor portal hooks (May 2026).
                    Stamps the lab's order ID + tracking URL so the lab can
                    open the portal page; status updates from the lab show
                    up under "Vendor history". */}
                <VendorCaptureBlock job={selectedJob} onSaved={loadJobs} />

                {/* Progress */}
                <div>
                  <p className="text-sm text-gray-500 mb-2">Progress: {resolveStatusConfig(selectedJob.status).label}</p>
                  <div className="h-3 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className={clsx(
                        'h-full transition-all',
                        selectedJob.status === 'QC_FAILED' ? 'bg-red-500' : 'bg-bv-red-600'
                      )}
                      style={{ width: `${(resolveStatusConfig(selectedJob.status).step / 8) * 100}%` }}
                    />
                  </div>
                </div>

                {/* Lens-order lifecycle — forward-only NOT_ORDERED -> ORDERED
                    -> RECEIVED -> MOUNTED. Independent of the job workflow status. */}
                <div className="rounded-lg border border-gray-200 bg-gray-50/60 p-3">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-sm font-medium text-gray-700">Lens order</p>
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', resolveLensConfig(selectedJob.lens_status).class)}>
                      {resolveLensConfig(selectedJob.lens_status).label}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-xs text-gray-500 mb-2">
                    <div>
                      <p className="text-gray-400">Ordered</p>
                      <p className="font-medium text-gray-700">{selectedJob.lens_ordered_at ? formatDate(selectedJob.lens_ordered_at) : '—'}</p>
                    </div>
                    <div>
                      <p className="text-gray-400">Received</p>
                      <p className="font-medium text-gray-700">{selectedJob.lens_received_at ? formatDate(selectedJob.lens_received_at) : '—'}</p>
                    </div>
                    <div>
                      <p className="text-gray-400">Mounted</p>
                      <p className="font-medium text-gray-700">{selectedJob.lens_mounted_at ? formatDate(selectedJob.lens_mounted_at) : '—'}</p>
                    </div>
                  </div>
                  {resolveLensConfig(selectedJob.lens_status).next && (
                    <button
                      onClick={() => handleLensAdvance(selectedJob.id, resolveLensConfig(selectedJob.lens_status).next as LensStatus)}
                      disabled={lensBusy}
                      className="btn-outline text-sm disabled:opacity-50"
                    >
                      {resolveLensConfig(selectedJob.lens_status).nextLabel}
                    </button>
                  )}
                </div>

                {/* Status Transition Buttons */}
                <div className="flex gap-2 flex-wrap">
                  {selectedJob.status === 'PENDING' && (
                    <button onClick={() => handleStatusChange(selectedJob.id, 'PROCESSING')} className="btn-primary text-sm">Start Processing</button>
                  )}
                  {selectedJob.status === 'PROCESSING' && (
                    <button onClick={() => handleStatusChange(selectedJob.id, 'COMPLETED')} className="btn-primary text-sm">Mark Completed</button>
                  )}
                  {selectedJob.status === 'COMPLETED' && canRunQc && (
                    <button
                      onClick={() => setQcModalJob(selectedJob)}
                      className="btn-primary text-sm flex items-center gap-1"
                    >
                      <ClipboardCheck className="w-4 h-4" /> Run QC checklist
                    </button>
                  )}
                  {selectedJob.status === 'QC_FAILED' && canRunQc && (
                    <>
                      <button
                        onClick={() => setQcModalJob(selectedJob)}
                        className="btn-primary text-sm flex items-center gap-1"
                      >
                        <ClipboardCheck className="w-4 h-4" /> Re-run QC
                      </button>
                      <button
                        onClick={() => handleRework(selectedJob.id)}
                        disabled={qcBusy}
                        className="btn-outline text-sm disabled:opacity-50"
                      >
                        Send for rework
                      </button>
                    </>
                  )}
                  {selectedJob.status === 'READY' && (
                    <button onClick={() => handleStatusChange(selectedJob.id, 'DELIVERED')} className="btn-success text-sm">Mark Delivered</button>
                  )}
                  {['COMPLETED', 'READY'].includes(selectedJob.status) && (
                    <button
                      onClick={() => handleNotifyReady(selectedJob.id)}
                      disabled={notifyBusy}
                      className="btn-outline text-sm flex items-center gap-1 disabled:opacity-50"
                    >
                      <Phone className="w-4 h-4" />
                      {selectedJob.ready_notified_at ? 'Notify ready again' : 'Notify ready'}
                    </button>
                  )}
                </div>

                {/* Thermal label actions: traveler/work-order always; stage
                    sticker any time; ready/pickup label at READY. */}
                <div className="flex gap-2 flex-wrap">
                  <button
                    onClick={() => setLabelSpec({ kind: 'job', jobId: selectedJob.id, type: 'traveler' })}
                    className="btn-outline text-sm flex items-center gap-1"
                  >
                    <Tag className="w-4 h-4" /> Traveler label
                  </button>
                  <button
                    onClick={() => setLabelSpec({ kind: 'job', jobId: selectedJob.id, type: 'stage' })}
                    className="btn-outline text-sm flex items-center gap-1"
                  >
                    <Printer className="w-4 h-4" /> Stage sticker
                  </button>
                  {selectedJob.status === 'READY' && (
                    <button
                      onClick={() => setLabelSpec({ kind: 'job', jobId: selectedJob.id, type: 'ready' })}
                      className="btn-outline text-sm flex items-center gap-1 text-green-700 border-green-600"
                    >
                      <Tag className="w-4 h-4" /> Pickup label
                    </button>
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

      {/* Thermal Label Preview + Print modal (QZ silent or HTML fallback) */}
      {labelSpec && (
        <LabelPreviewModal spec={labelSpec} onClose={() => setLabelSpec(null)} />
      )}

      {/* QC checklist modal — posts to /qc (pass -> READY, fail -> QC_FAILED) */}
      {qcModalJob && (
        <QcChecklistModal
          job={qcModalJob}
          busy={qcBusy}
          onCancel={() => setQcModalJob(null)}
          onSubmit={(passed, notes) => handleQcSubmit(qcModalJob.id, passed, notes)}
        />
      )}

      {/* CREATE JOB MODAL */}
      {showCreateJob && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[85vh] overflow-y-auto">
            <div className="p-5 border-b border-gray-200 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">Create Workshop Job from Order</h3>
              <button onClick={() => { setShowCreateJob(false); setCreateSelectedOrder(null); setCreateOrders([]); }} className="p-1 hover:bg-gray-100 rounded text-gray-500 hover:text-gray-700">
                ×
              </button>
            </div>
            <div className="p-5 space-y-4">
              {!createSelectedOrder ? (
                <>
                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                      <input value={createOrderSearch} onChange={e => setCreateOrderSearch(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && searchOrdersForJob()}
                        placeholder="Search order number or customer..."
                        className="w-full pl-9 pr-4 py-2.5 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm placeholder-gray-500" />
                    </div>
                    <button onClick={searchOrdersForJob} className="px-4 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700">Search</button>
                  </div>
                  {createOrders.length > 0 && (
                    <div className="space-y-1.5 max-h-60 overflow-y-auto">
                      {createOrders.map((o: any) => (
                        <button key={o.id} onClick={() => setCreateSelectedOrder(o)}
                          className="w-full flex items-center justify-between p-3 rounded-lg border border-gray-300 hover:border-bv-gold-400 hover:bg-gray-100 text-left text-gray-900 transition-colors">
                          <div>
                            <p className="text-sm font-medium">{o.orderNumber}</p>
                            <p className="text-xs text-gray-500">{o.customerName} · {(o.items || []).length} items</p>
                          </div>
                          <span className="text-sm font-bold text-bv-gold-300">₹{Math.round(o.grandTotal || 0).toLocaleString('en-IN')}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium text-sm">{createSelectedOrder.orderNumber}</p>
                        <p className="text-xs text-gray-500">{createSelectedOrder.customerName}</p>
                      </div>
                      <button onClick={() => setCreateSelectedOrder(null)} className="text-xs text-bv-red-600 hover:underline">Change</button>
                    </div>
                    <div className="mt-2 space-y-1">
                      {(createSelectedOrder.items || []).map((item: any, i: number) => (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <span className="text-gray-900">{item.productName || item.product_name || item.name}</span>
                          <span className="text-gray-500 text-xs">{item.category}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Priority</label>
                    <div className="flex gap-2">
                      {(['NORMAL', 'EXPRESS', 'URGENT'] as const).map(p => (
                        <button key={p} onClick={() => setCreatePriority(p)}
                          className={clsx('flex-1 py-2 rounded-lg text-xs font-medium border-2 transition-all',
                            createPriority === p
                              ? p === 'URGENT' ? 'border-red-500 bg-red-50 text-red-700'
                                : p === 'EXPRESS' ? 'border-amber-500 bg-amber-50 text-amber-700'
                                  : 'border-bv-red-600 bg-bv-gold-900 text-bv-gold-300'
                              : 'border-gray-300 text-gray-600 bg-white')}>
                          {p}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Expected Delivery Date</label>
                    <input type="date" value={createExpectedDate} onChange={e => setCreateExpectedDate(e.target.value)}
                      min={new Date().toISOString().split('T')[0]}
                      className="w-full px-3 py-2 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm" />
                  </div>

                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Fitting Instructions</label>
                    <textarea value={createFitting} onChange={e => setCreateFitting(e.target.value)}
                      placeholder="PD, segment height, tilt, wrap angle, frame adjustments..."
                      className="w-full px-3 py-2 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm h-16 resize-none placeholder-gray-500" />
                  </div>

                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Special Notes for Workshop</label>
                    <textarea value={createNotes} onChange={e => setCreateNotes(e.target.value)}
                      placeholder="Tint, drill mount, special coating, customer preferences..."
                      className="w-full px-3 py-2 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm h-16 resize-none placeholder-gray-500" />
                  </div>
                </>
              )}
            </div>
            {createSelectedOrder && (
              <div className="p-5 border-t border-gray-200 flex gap-2">
                <button onClick={() => { setShowCreateJob(false); setCreateSelectedOrder(null); }}
                  className="flex-1 px-4 py-2.5 border border-gray-300 text-gray-600 rounded-lg text-sm hover:bg-gray-100">Cancel</button>
                <button onClick={handleCreateJob} disabled={createLoading}
                  className="flex-1 px-4 py-2.5 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700 disabled:opacity-50">
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


// ============================================================================
// QC checklist modal — power verification / fitting / cosmetic check + notes.
// The backend /qc endpoint only stores `passed` + a free-text `notes`, so the
// checklist outcome is folded into the notes string. "Pass" requires every
// item ticked; "Fail" needs a reason.
// ============================================================================

const QC_CHECKLIST_ITEMS: Array<{ key: string; label: string; hint: string }> = [
  { key: 'power', label: 'Power verification', hint: 'Lensmeter reading matches the prescription' },
  { key: 'fitting', label: 'Fitting check', hint: 'Lenses seated, frame aligned, screws tight' },
  { key: 'cosmetic', label: 'Cosmetic check', hint: 'No scratches, chips, coating defects or marks' },
];

function QcChecklistModal({
  job,
  busy,
  onCancel,
  onSubmit,
}: {
  job: Job;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (passed: boolean, notes: string) => void;
}) {
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [notes, setNotes] = useState('');

  const allChecked = QC_CHECKLIST_ITEMS.every((item) => checks[item.key]);

  // Compose a human-readable QC summary that gets persisted as qc_notes.
  const buildNotes = (passed: boolean) => {
    const lines = QC_CHECKLIST_ITEMS.map(
      (item) => `${checks[item.key] ? '[x]' : '[ ]'} ${item.label}`,
    );
    lines.unshift(passed ? 'QC PASS' : 'QC FAIL');
    if (notes.trim()) lines.push(`Notes: ${notes.trim()}`);
    return lines.join('\n');
  };

  const handlePass = () => {
    if (!allChecked || busy) return;
    onSubmit(true, buildNotes(true));
  };

  const handleFail = () => {
    if (busy) return;
    if (!notes.trim()) return; // a failure must say why
    onSubmit(false, buildNotes(false));
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md max-h-[85vh] overflow-y-auto">
        <div className="p-5 border-b border-gray-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ClipboardCheck className="w-5 h-5 text-bv-red-600" />
            <div>
              <h3 className="font-semibold text-gray-900">Quality check</h3>
              <p className="text-xs text-gray-500">Job {job.jobNumber || job.id}</p>
            </div>
          </div>
          <button
            onClick={onCancel}
            className="p-1 hover:bg-gray-100 rounded text-gray-500 hover:text-gray-700"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div className="space-y-2">
            {QC_CHECKLIST_ITEMS.map((item) => (
              <label
                key={item.key}
                className="flex items-start gap-3 p-3 rounded-lg border border-gray-200 hover:bg-gray-50 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={!!checks[item.key]}
                  onChange={(e) => setChecks((prev) => ({ ...prev, [item.key]: e.target.checked }))}
                  className="mt-0.5 h-4 w-4"
                />
                <span>
                  <span className="block text-sm font-medium text-gray-900">{item.label}</span>
                  <span className="block text-xs text-gray-500">{item.hint}</span>
                </span>
              </label>
            ))}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Notes {!allChecked && <span className="text-gray-400">(required to fail QC)</span>}
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="Anything the technician should know — defects, rework instructions, etc."
              className="input-field text-sm w-full"
            />
          </div>

          {!allChecked && (
            <p className="text-xs text-gray-500">
              Tick every item to mark QC passed. To fail QC, add a note explaining what's wrong.
            </p>
          )}
        </div>

        <div className="p-5 border-t border-gray-200 flex gap-2">
          <button
            onClick={handlePass}
            disabled={!allChecked || busy}
            className="btn-success text-sm flex-1 disabled:opacity-50"
          >
            {busy ? 'Saving…' : 'Pass — mark ready'}
          </button>
          <button
            onClick={handleFail}
            disabled={busy || !notes.trim()}
            className="btn-outline text-sm flex-1 text-red-600 border-red-600 disabled:opacity-50"
          >
            Fail QC
          </button>
        </div>
      </div>
    </div>
  );
}


// ============================================================================
// Vendor capture block — admin stamps the lens lab + their order ID +
// tracking URL on a workshop job. Setting vendor_id for the first time is
// what makes the public vendor portal aware of the job.
// ============================================================================

function VendorCaptureBlock({ job, onSaved }: { job: Job; onSaved: () => void }) {
  const toast = useToast();
  const j = job as Job & {
    vendor_id?: string;
    vendor_name?: string;
    vendor_order_id?: string;
    vendor_tracking_url?: string;
    vendor_status?: string;
    vendor_history?: Array<{
      status: string;
      note?: string;
      source: string;
      at: string;
    }>;
  };

  const [vendors, setVendors] = useState<Array<{ vendor_id: string; legal_name?: string; trade_name?: string; name?: string }>>([]);
  const [loadingVendors, setLoadingVendors] = useState(false);
  const [vendorId, setVendorId] = useState(j.vendor_id || '');
  const [vendorOrderId, setVendorOrderId] = useState(j.vendor_order_id || '');
  const [trackingUrl, setTrackingUrl] = useState(j.vendor_tracking_url || '');
  const [saving, setSaving] = useState(false);
  const [showStatusForm, setShowStatusForm] = useState(false);
  const [statusValue, setStatusValue] = useState('');
  const [statusNote, setStatusNote] = useState('');

  // Reset local form state when the user picks a different job
  useEffect(() => {
    setVendorId(j.vendor_id || '');
    setVendorOrderId(j.vendor_order_id || '');
    setTrackingUrl(j.vendor_tracking_url || '');
    setShowStatusForm(false);
    setStatusValue('');
    setStatusNote('');
  }, [j.id, j.vendor_id, j.vendor_order_id, j.vendor_tracking_url]);

  // Lazy-load the vendor directory once
  useEffect(() => {
    let cancelled = false;
    setLoadingVendors(true);
    vendorsApi
      .getVendors({ is_active: true })
      .then((r) => {
        if (cancelled) return;
        const list = (r as { vendors?: unknown[] })?.vendors ?? r ?? [];
        setVendors(Array.isArray(list) ? (list as typeof vendors) : []);
      })
      .catch(() => {
        if (!cancelled) setVendors([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingVendors(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const dirty =
    vendorId !== (j.vendor_id || '') ||
    vendorOrderId !== (j.vendor_order_id || '') ||
    trackingUrl !== (j.vendor_tracking_url || '');

  const handleSave = async () => {
    setSaving(true);
    try {
      await workshopApi.patchJobVendor(job.id, {
        vendor_id: vendorId || null,
        vendor_order_id: vendorOrderId || null,
        vendor_tracking_url: trackingUrl || null,
      });
      toast.success('Vendor details saved');
      onSaved();
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to save vendor details';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handlePostStatus = async () => {
    if (!statusValue.trim()) {
      toast.error('Pick a status');
      return;
    }
    setSaving(true);
    try {
      await workshopApi.postJobVendorStatus(job.id, {
        status: statusValue.trim(),
        note: statusNote.trim() || undefined,
      });
      toast.success('Vendor status logged');
      setShowStatusForm(false);
      setStatusValue('');
      setStatusNote('');
      onSaved();
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to log vendor status';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const VENDOR_STATUS_OPTIONS = ['ACKNOWLEDGED', 'IN_PROGRESS', 'DISPATCHED', 'DELIVERED', 'DELAYED', 'CANCELLED'];

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/30 p-3">
      <div className="flex items-center justify-between mb-2">
        <p className="text-sm font-medium text-indigo-900">Vendor / lens lab</p>
        {j.vendor_status && (
          <span className="px-2 py-0.5 rounded text-xs font-semibold bg-white border border-indigo-300 text-indigo-700">
            {j.vendor_status}
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-2">
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-0.5">Lens lab</label>
          <select
            value={vendorId}
            onChange={(e) => setVendorId(e.target.value)}
            disabled={loadingVendors}
            className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded bg-white"
          >
            <option value="">— Select vendor —</option>
            {vendors.map((v) => (
              <option key={v.vendor_id} value={v.vendor_id}>
                {v.trade_name || v.legal_name || v.name || v.vendor_id}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-0.5">Vendor order ID</label>
          <input
            type="text"
            value={vendorOrderId}
            onChange={(e) => setVendorOrderId(e.target.value)}
            placeholder="e.g. ZL-2026-44871"
            className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-0.5">Tracking URL</label>
          <input
            type="url"
            value={trackingUrl}
            onChange={(e) => setTrackingUrl(e.target.value)}
            placeholder="https://lab.example/track/..."
            className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
          />
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || saving}
          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save vendor details'}
        </button>
        {j.vendor_id && (
          <button
            type="button"
            onClick={() => setShowStatusForm((s) => !s)}
            className="px-3 py-1.5 bg-white hover:bg-gray-50 text-indigo-700 text-xs font-semibold rounded border border-indigo-300"
          >
            {showStatusForm ? 'Cancel status update' : 'Log vendor status'}
          </button>
        )}
      </div>

      {showStatusForm && (
        <div className="mt-2 p-2 bg-white border border-indigo-200 rounded space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <select
              value={statusValue}
              onChange={(e) => setStatusValue(e.target.value)}
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
            >
              <option value="">— Status —</option>
              {VENDOR_STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={statusNote}
              onChange={(e) => setStatusNote(e.target.value)}
              placeholder="Note (optional)"
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
            />
          </div>
          <button
            type="button"
            onClick={handlePostStatus}
            disabled={!statusValue.trim() || saving}
            className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded disabled:opacity-50"
          >
            {saving ? 'Posting…' : 'Post status'}
          </button>
        </div>
      )}

      {Array.isArray(j.vendor_history) && j.vendor_history.length > 0 && (
        <details className="mt-2">
          <summary className="text-xs font-medium text-gray-600 cursor-pointer">
            Vendor history ({j.vendor_history.length})
          </summary>
          <ul className="mt-1 space-y-1">
            {j.vendor_history
              .slice()
              .reverse()
              .map((h, i) => (
                <li key={i} className="text-xs text-gray-700 flex gap-2">
                  <span className="font-mono text-gray-400">
                    {h.at ? new Date(h.at).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'}
                  </span>
                  <span className="font-semibold text-indigo-700">{h.status}</span>
                  {h.note && <span className="text-gray-600">· {h.note}</span>}
                  <span className="text-gray-400 ml-auto">[{h.source}]</span>
                </li>
              ))}
          </ul>
        </details>
      )}
    </div>
  );
}
