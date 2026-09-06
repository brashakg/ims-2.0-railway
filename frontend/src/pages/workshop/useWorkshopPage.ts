// ============================================================================
// IMS 2.0 - Workshop Page - state, loaders and handlers
// ============================================================================
// Every useState/useEffect and every handler of WorkshopPage, moved verbatim
// out of the page file (Wave 3 file diet). Hook order, API calls, toast copy
// and the QC gate wiring are unchanged - the backend remains the authority.
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import { canonicalCategory } from '../../utils/categoryNormalize';
import type { JobStatus, JobPriority } from '../../types';
import { workshopApi, orderApi } from '../../services/api';
import { settingsApi } from '../../services/api/settings';
import { useAuth } from '../../context/AuthContext';
import { useIsOnlineStore } from '../../hooks/useIsOnlineStore';
import { useToast } from '../../context/ToastContext';
import type { LabelModalSpec } from '../../components/labels/LabelPreviewModal';
import { printJobLabel } from '../../components/labels/printLabel';
import { resolveStoreIdentity } from '../../components/print/storeIdentity';
import type { LensFittingFormValue } from '../../components/pos/LensFittingFormModal';
import { resolveItemPrescriptionId, backendMessage } from './qcHandover';
import type { EntityLike } from '../../components/print/legalPrimitives';
import type { Job, LensStatus } from './shared';

export function useWorkshopPage() {
  const { user } = useAuth();
  const toast = useToast();
  // OS-065: an ONLINE store has no workshop bench — job intake is disabled
  // there (online orders are fulfilled from the physical stores' benches).
  const isOnlineStoreActive = useIsOnlineStore();

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

  // Pickup record for READY -> DELIVERED: who collected the job. Optional —
  // a record, not a gate (delivery is never blocked on it). Reset per job.
  const [pickupName, setPickupName] = useState('');
  useEffect(() => {
    setPickupName('');
  }, [selectedJob?.id]);

  // QC checklist modal — opened from a COMPLETED / QC_FAILED job's detail panel.
  const [qcModalJob, setQcModalJob] = useState<Job | null>(null);
  // Who may run QC. Workshop floor + store/area management + optometrist
  // (power verification is optometry-adjacent). Plain cashiers/sales don't.
  const QC_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'OPTOMETRIST', 'WORKSHOP_STAFF'];
  const canRunQc = QC_ROLES.includes(user?.activeRole || '');

  // Fitting-details modal — opened from a PENDING job that sales never confirmed.
  // Mirrors the backend's _FITTING_ROLES (workshop.py): confirming the fitting is
  // a SALES act, so workshop staff are deliberately absent from this list.
  const [fittingJob, setFittingJob] = useState<Job | null>(null);
  const [fittingSaving, setFittingSaving] = useState(false);
  const FITTING_ROLES = [
    'SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER',
    'SALES_STAFF', 'SALES_CASHIER', 'CASHIER',
  ];
  const canConfirmFitting = FITTING_ROLES.includes(user?.activeRole || '');

  const handleFittingSave = async (jobId: string, value: LensFittingFormValue) => {
    setFittingSaving(true);
    try {
      await workshopApi.updateFittingDetails(jobId, value);
      toast.success('Fitting details confirmed — the job can now be started');
      setFittingJob(null);
      setSelectedJob(null);
      await loadJobs();
    } catch (err) {
      toast.error(backendMessage(err, 'Failed to save fitting details'));
    } finally {
      setFittingSaving(false);
    }
  };

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

      // Load the issuing-store identity (store + legal entity) for printing the
      // job card + thermal labels. NEVER defaulted to a fixed brand name (a
      // WizOpt store must print WizOpt).
      if (!storeInfo && user?.activeStoreId) {
        try {
          const id = await resolveStoreIdentity(user.activeStoreId);
          const sv = id.store;
          setStoreInfo({
            storeName: sv.storeName || sv.storeCode || '',
            storeCode: sv.storeCode || '',
            brand: sv.brand || '',
            address: sv.address || '',
            city: sv.city || '',
            state: sv.state || '',
            stateCode: sv.stateCode || '',
            pincode: sv.pincode || '',
            phone: (sv as any).phone || '',
            gstin: sv.gstin || '',
          });
          setStoreEntity(id.entity);
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
  const [storeEntity, setStoreEntity] = useState<EntityLike | null>(null);
  const [createOrderSearch, setCreateOrderSearch] = useState('');
  const [createOrders, setCreateOrders] = useState<any[]>([]);
  const [createSelectedOrder, setCreateSelectedOrder] = useState<any>(null);
  const [createFitting, setCreateFitting] = useState('');
  const [createNotes, setCreateNotes] = useState('');
  const [createPriority, setCreatePriority] = useState<'NORMAL' | 'EXPRESS' | 'URGENT'>('NORMAL');
  const [createExpectedDate, setCreateExpectedDate] = useState(new Date(Date.now() + 3 * 86400000).toISOString().split('T')[0]);
  const [createLoading, setCreateLoading] = useState(false);
  // F9 — DC hardlock: when the backend returns 422 DC_HARDLOCK, show a banner
  // in the create-job modal. ADMIN+ may enter an override reason and resubmit.
  const [dcHardlock, setDcHardlock] = useState<string | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  const canOverrideHardlock = ['ADMIN', 'SUPERADMIN'].includes(user?.activeRole || '');

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
    setDcHardlock(null);
    try {
      const rxItem = (createSelectedOrder.items || []).find((i: any) => canonicalCategory(i.category) === 'OPTICAL_LENS' || i.is_optical);
      const created = await workshopApi.createJob({
        order_id: createSelectedOrder.id,
        frame_details: { items: (createSelectedOrder.items || []).filter((i: any) => ['FRAME', 'SUNGLASS'].includes(canonicalCategory(i.category))) },
        lens_details: rxItem?.lens_details || { type: 'STANDARD' },
        prescription_id: resolveItemPrescriptionId(rxItem),
        fitting_instructions: createFitting || undefined,
        special_notes: createNotes || undefined,
        expected_date: createExpectedDate,
        // F9 — include the override reason only when an ADMIN+ supplied one.
        override_reason: overrideReason.trim() || undefined,
      });
      setShowCreateJob(false);
      setCreateSelectedOrder(null);
      setCreateFitting('');
      setCreateNotes('');
      setOverrideReason('');
      if (created?.dc_hardlock_override) {
        toast.success('Override logged. Job created.');
      }
      await loadJobs();
      // Offer the work-order traveler label for the freshly created job so it
      // can be attached to the physical job + scanned through the workflow.
      const newJobId = created?.job_id || created?.id;
      if (newJobId) {
        setLabelSpec({ kind: 'job', jobId: newJobId, type: 'traveler' });
      }
    } catch (err: any) {
      // F9 — surface the DC hardlock as an actionable banner inside the modal.
      const detail = err?.response?.data?.detail;
      const code = typeof detail === 'object' ? detail?.code : undefined;
      if (code === 'DC_HARDLOCK' || code === 'DC_HARDLOCK_OVERRIDE_FORBIDDEN') {
        setDcHardlock(
          (typeof detail === 'object' && detail?.message) ||
            'No Delivery Challan logged for this lens.',
        );
      } else {
        // The create-time Rx 422s (unknown / WRONG-PATIENT / expired) return a
        // plain-string detail. The wrong-patient sentence and the "a Store
        // Manager must approve" instruction are the most important strings this
        // screen can show — a generic toast hid both and left staff retrying.
        toast.error(typeof detail === 'string' ? detail : 'Failed to create workshop job');
      }
    } finally {
      setCreateLoading(false);
    }
  };

  const handleStatusChange = async (jobId: string, newStatus: string) => {
    try {
      // On DELIVERED, send the optional "collected by" record along with the
      // PATCH. Empty name -> omitted entirely (never blocks the delivery).
      const pickup =
        newStatus === 'DELIVERED' && pickupName.trim()
          ? { picked_up_by_name: pickupName.trim() }
          : undefined;
      await workshopApi.updateJobStatus(jobId, newStatus, undefined, pickup);
      toast.success(`Job status updated to ${newStatus}`);
      setPickupName('');
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
    } catch (err) {
      // Surface the BACKEND's sentence. The QC handover gate deliberately leaves
      // "Mark Delivered" enabled on the reasoning that the server returns a
      // plain-English 400 naming the remedy ("...run QC on it (or record an
      // audited waiver) before handing it to the customer") — that reasoning is
      // only true if we actually render it. A bare catch turned every refusal
      // into "Failed to update job status", which reads like a server fault and
      // sends staff hunting for a manager instead of running QC.
      toast.error(backendMessage(err, 'Failed to update job status'));
    }
  };

  // Submit a structured QC checklist via the /qc-checklist endpoint (Phase 6.9).
  // Each checklist item (key, label, passed, note) is stored server-side with
  // reviewer identity + timestamp. Pass -> READY, fail -> QC_FAILED.
  //
  // `previousStatus` is the job's status BEFORE this submission. QC is now also
  // run on a job that is ALREADY READY (ongoing workflow: the handover gate
  // needs a QC record, and a job routinely reaches the pickup shelf without
  // one), and that case differs in two ways -- the toast copy and the pickup
  // label auto-print. Both are handled below.
  const [qcBusy, setQcBusy] = useState(false);
  const handleQcSubmit = async (
    jobId: string,
    passed: boolean,
    notes: string,
    checklistItems?: Array<{ key: string; label: string; passed: boolean; note?: string }>,
    previousStatus?: JobStatus,
  ) => {
    const wasAlreadyReady = previousStatus === 'READY';
    setQcBusy(true);
    try {
      let res;
      if (checklistItems && checklistItems.length > 0) {
        // Use the structured /qc-checklist endpoint when items are provided.
        res = await workshopApi.qcChecklist(
          jobId,
          checklistItems,
          notes || undefined,
        );
      } else {
        // Fallback to the simple /qc endpoint (no structured items).
        res = await workshopApi.qcJob(jobId, passed, notes);
      }
      // Copy has to be true for a job that was ALREADY on the pickup shelf:
      // "now ready for pickup" would be wrong there (it never moved), and a
      // fail is not a generic "flagged for rework" — it pulls the job back OFF
      // the shelf, which the counter needs to understand immediately.
      if (passed) {
        toast.success(
          wasAlreadyReady
            ? 'QC recorded — job cleared for handover'
            : 'QC passed — job ready for pickup',
        );
      } else {
        // A FAILURE is not a success: at a busy counter colour is read before
        // text, and a green flash after a QC fail reads as "done, all good" —
        // exactly backwards for a job that just left the pickup shelf.
        toast.error(
          wasAlreadyReady
            ? 'QC failed — job pulled off the pickup shelf for rework'
            : 'QC failed — job flagged for rework',
        );
      }
      setQcModalJob(null);
      setSelectedJob(null);
      await loadJobs();
      // On a pass from COMPLETED / QC_FAILED the job has JUST reached the pickup
      // shelf — auto-print the pickup label, honouring the
      // auto_print_stage_sticker setting (fail-soft, mirrors handleStatusChange).
      // A job that was ALREADY READY is deliberately excluded: its pickup label
      // was printed when it first became READY, and silently spitting out a
      // duplicate every time QC is recorded at the shelf would confuse the
      // counter. Staff can still reprint on demand via the Pickup label button.
      if (res?.status === 'READY' && !wasAlreadyReady) {
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

  // F13 — a rework is a REMAKE: the backend requires a remake_reason_code
  // (422 without one), costs the spoiled lens, and logs the justification.
  // The button opens a small reason dialog; confirm fires the API call.
  const REWORK_FALLBACK_CODES = [
    { code: 'AXIS_ERROR', label: 'Axis error', category: 'LAB_FAULT' },
    { code: 'POWER_ERROR', label: 'Power error', category: 'LAB_FAULT' },
    { code: 'FITTING_ERROR', label: 'Fitting error', category: 'LAB_FAULT' },
    { code: 'SURFACE_DEFECT', label: 'Surface defect', category: 'VENDOR_FAULT' },
    { code: 'COATING_DEFECT', label: 'Coating defect', category: 'VENDOR_FAULT' },
    { code: 'BREAKAGE_IN_LAB', label: 'Breakage in lab', category: 'LAB_FAULT' },
    { code: 'WRONG_LENS_PICKED', label: 'Wrong lens picked', category: 'STORE_FAULT' },
    { code: 'CUSTOMER_CHANGED_RX', label: 'Customer changed Rx', category: 'CUSTOMER' },
    { code: 'OTHER', label: 'Other', category: 'LAB_FAULT' },
  ];
  const [reworkModalJobId, setReworkModalJobId] = useState<string | null>(null);
  const [reworkCodes, setReworkCodes] = useState<Array<{ code: string; label: string; category: string }>>(REWORK_FALLBACK_CODES);
  const [reworkCode, setReworkCode] = useState('');
  const [reworkNotes, setReworkNotes] = useState('');

  const openReworkModal = async (jobId: string) => {
    setReworkCode('');
    setReworkNotes('');
    setReworkModalJobId(jobId);
    try {
      const res = await workshopApi.getRemakeReasonCodes();
      if (res?.codes?.length) setReworkCodes(res.codes);
    } catch {
      /* taxonomy fetch fail-soft -> seeded fallback list */
    }
  };

  const handleRework = async (jobId: string, reasonCode: string, notes?: string) => {
    if (!reasonCode) {
      toast.error('Select a remake reason first');
      return;
    }
    setQcBusy(true);
    try {
      const res = await workshopApi.reworkJob(jobId, reasonCode, notes ? { notes } : undefined);
      toast.success(res?.rework_count ? `Sent for rework (attempt #${res.rework_count})` : 'Job sent for rework');
      setReworkModalJobId(null);
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

  return {
    user, toast, isOnlineStoreActive,
    jobs, selectedJob, setSelectedJob, kpis,
    searchQuery, setSearchQuery, statusFilter, setStatusFilter, priorityFilter, setPriorityFilter,
    pickupName, setPickupName,
    qcModalJob, setQcModalJob, canRunQc,
    fittingJob, setFittingJob, fittingSaving, canConfirmFitting, handleFittingSave,
    isLoading, error, loadJobs,
    filteredJobs, activeJobs, urgentJobs, readyJobs, overdueJobs,
    formatDate, isOverdue,
    showCreateJob, setShowCreateJob,
    printJob, setPrintJob, labelSpec, setLabelSpec, storeInfo, storeEntity,
    createOrderSearch, setCreateOrderSearch, createOrders, setCreateOrders,
    createSelectedOrder, setCreateSelectedOrder,
    createFitting, setCreateFitting, createNotes, setCreateNotes,
    createPriority, setCreatePriority, createExpectedDate, setCreateExpectedDate,
    createLoading, dcHardlock, overrideReason, setOverrideReason, canOverrideHardlock,
    searchOrdersForJob, handleCreateJob, handleStatusChange,
    qcBusy, handleQcSubmit,
    reworkModalJobId, setReworkModalJobId, reworkCodes, reworkCode, setReworkCode,
    reworkNotes, setReworkNotes, openReworkModal, handleRework,
    lensBusy, handleLensAdvance,
    notifyBusy, handleNotifyReady,
  };
}

export type WorkshopPageState = ReturnType<typeof useWorkshopPage>;
