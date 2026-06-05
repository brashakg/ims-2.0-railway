// ============================================================================
// IMS 2.0 - Clinical / Eye Tests Page
// ============================================================================
// NO MOCK DATA - All data from API

import { useState, useEffect } from 'react';
import {
  Eye,
  User,
  Clock,
  CheckCircle,
  Play,
  Plus,
  Search,
  FileText,
  Phone,
  Loader2,
  RefreshCw,
  AlertTriangle,
} from 'lucide-react';
import { clinicalApi, customerApi, storeApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { EyeTestForm, type EyeTestData } from '../../components/clinical/EyeTestForm';
import { AddCustomerModal, type CustomerFormData } from '../../components/customers/AddCustomerModal';
import { QueueExistingCustomerModal } from '../../components/customers/QueueExistingCustomerModal';
import { EyeTestTokenPrint } from '../../components/print/EyeTestTokenPrint';
import { AbuseDetection } from '../../components/clinical/AbuseDetection';
import { PrescriptionCard } from '../../components/clinical/PrescriptionCard';
import { ClinicPrescriptionHistory } from '../../components/clinical/ClinicPrescriptionHistory';
import clsx from 'clsx';
import { readEyePower } from '../../utils/rxEye';

// Types
interface QueueItem {
  id: string;
  tokenNumber: string;
  patientName: string;
  customerPhone: string;
  age?: number;
  reason?: string;
  status: QueueStatus;
  waitTime: number;
  createdAt: string;
  testId?: string;
  /** Linked customer id when the patient is a dependent (e.g. a child)
   *  whose bills are paid against a different customer record. Empty
   *  when the patient and the customer are the same person. */
  customerId?: string;
  /** Linked customer name — only set when patientName !== customerName,
   *  to make the patient-vs-customer relationship explicit on the card. */
  customerName?: string;
}

interface CompletedTest {
  id: string;
  patientName: string;
  customerPhone: string;
  customerId?: string;
  completedAt: string;
  rightEye: { sphere: number | null; cylinder: number | null; axis: number | null };
  leftEye: { sphere: number | null; cylinder: number | null; axis: number | null };
}

type QueueStatus = 'WAITING' | 'IN_PROGRESS' | 'COMPLETED';

const STATUS_CONFIG: Record<QueueStatus, { label: string; class: string }> = {
  WAITING: { label: 'Waiting', class: 'bg-yellow-100 text-yellow-600' },
  IN_PROGRESS: { label: 'In Progress', class: 'bg-blue-100 text-blue-600' },
  COMPLETED: { label: 'Completed', class: 'bg-green-100 text-green-600' },
};

export function ClinicalPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();

  // Data state
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [completedTests, setCompletedTests] = useState<CompletedTest[]>([]);

  // UI state
  const [activeTab, setActiveTab] = useState<'queue' | 'completed' | 'abuse-alerts'>('queue');
  const [showEyeTestForm, setShowEyeTestForm] = useState(false);
  const [selectedPatient, setSelectedPatient] = useState<{
    id: string;
    name: string;
    phone: string;
    age?: number;
    customerId: string;
  } | null>(null);
  const [currentTestId, setCurrentTestId] = useState<string | null>(null);

  // Prescription history / edit modal (bugs #1-#3): per-customer Rx view with
  // Edit + New + Print, grouped by family member. Opened from a queue row.
  const [rxHistoryFor, setRxHistoryFor] = useState<{
    customerId: string;
    customerName?: string;
    patientId?: string;
  } | null>(null);

  // Add customer modal state (replaces simple patient modal)
  const [showAddCustomerModal, setShowAddCustomerModal] = useState(false);
  // Phase 6.13 — search-existing-customer flow. Opens a lookup modal
  // first; the create-new modal stays as a fallback from inside the
  // search modal when no match is found.
  const [showQueueExistingModal, setShowQueueExistingModal] = useState(false);
  const [addCustomerInitialName, setAddCustomerInitialName] = useState('');
  
  // Print and store state
  const [printToken, setPrintToken] = useState<any>(null);
  const [storeInfo, setStoreInfo] = useState<any>(null);
  const [printRxCard, setPrintRxCard] = useState<any>(null);

  // Loading state
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Role-based permissions
  const canStartTest = hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']);
  const canAddPatient = hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'SALES_CASHIER', 'SALES_STAFF']);
  const canViewAbuseAlerts = hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']);

  // Load data on mount
  useEffect(() => {
    loadData();
  }, [user?.activeStoreId]);

  const loadData = async () => {
    if (!user?.activeStoreId) {
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const [queueData, testsData, storeData] = await Promise.all([
        clinicalApi.getQueue(user.activeStoreId).catch(() => ({ queue: [] })),
        clinicalApi.getTodayTests(user.activeStoreId).catch(() => ({ tests: [] })),
        !storeInfo ? storeApi.getStore(user.activeStoreId).catch(() => null) : Promise.resolve(null),
      ]);

      const queueItems = queueData?.queue || queueData || [];
      setQueue(Array.isArray(queueItems) ? queueItems : []);

      const tests = testsData?.tests || testsData || [];
      setCompletedTests(Array.isArray(tests) ? tests : []);
      
      // Load store info for printing tokens
      if (storeData && !storeInfo) {
        setStoreInfo({
          storeName: storeData.storeName || storeData.name || 'Better Vision Optics',
          address: storeData.address || '',
          city: storeData.city || '',
          state: storeData.state || '',
          pincode: storeData.pincode || '',
          phone: (storeData as any).phone || '',
        });
      }
    } catch {
      setError('Failed to load data. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleStartTest = async (queueId: string): Promise<string | null> => {
    setActionLoading(queueId);
    try {
      const result = await clinicalApi.startTest(queueId);
      await loadData();
      return result?.testId || null;
    } catch {
      setError('Failed to start test.');
      return null;
    } finally {
      setActionLoading(null);
    }
  };

  const handleOpenEyeTest = (item: QueueItem) => {
    setSelectedPatient({
      id: item.id,
      name: item.patientName,
      phone: item.customerPhone,
      age: item.age,
      customerId: (item as any).customerId || item.id,
    });
    setShowEyeTestForm(true);
  };

  const handleSaveEyeTest = async (data: EyeTestData) => {
    try {
      if (!currentTestId) {
        toast.error('No active test found');
        return;
      }

      // Extract prescription data from finalRx for the API. Forward the FULL
      // Final-Rx field set (VA / prism / base per eye + IPD + lens type + next
      // checkup) so the auto-created prescription carries the SAME data a
      // POS-created Rx does (field + DB parity).
      const fr = data.finalRx;
      const re = fr?.rightEye;
      const le = fr?.leftEye;

      // Convert string values to numbers, handling empty strings
      const parseValue = (val: string): number | null => {
        const num = parseFloat(val);
        return isNaN(num) ? null : num;
      };

      await clinicalApi.completeTest(currentTestId, {
        rightEye: {
          sphere: parseValue(re?.sphere || ''),
          cylinder: parseValue(re?.cylinder || ''),
          axis: parseValue(re?.axis || ''),
          // Near-vision ADD is captured in finalRx.rightAdd, not rightEye.add.
          add: parseValue(fr?.rightAdd || re?.add || ''),
          pd: parseValue(re?.pd || ''),
          prism: re?.prism || null,
          base: re?.base || null,
          va: re?.va || null,
        },
        leftEye: {
          sphere: parseValue(le?.sphere || ''),
          cylinder: parseValue(le?.cylinder || ''),
          axis: parseValue(le?.axis || ''),
          add: parseValue(fr?.leftAdd || le?.add || ''),
          pd: parseValue(le?.pd || ''),
          prism: le?.prism || null,
          base: le?.base || null,
          va: le?.va || null,
        },
        pd: parseValue(re?.pd || le?.pd || '') ?? undefined,
        ipd: fr?.ipd || undefined,
        lensRecommendation: fr?.lensType || undefined,
        nextCheckup: fr?.nextCheckup || undefined,
        notes: data.chiefComplaint || '',
        // C6-B: also persist the structured exam findings the form already
        // collects (chief complaint + per-eye aided VA from the Final Rx) into
        // the test record's `clinical_findings` block, so they're queryable
        // (VA trend across visits, search by complaint) rather than only buried
        // in `notes`/per-eye. Only sent when non-empty -> a refraction-only test
        // is unchanged. Net-new inputs (IOP/diagnosis/...) land here once the
        // EyeTestForm grows those fields (follow-up).
        clinicalFindings: (() => {
          const cf: Record<string, string | number> = {};
          if (data.chiefComplaint) cf.chiefComplaint = data.chiefComplaint;
          if (re?.va) cf.vaRightAided = re.va;
          if (le?.va) cf.vaLeftAided = le.va;
          // The new internal Clinical Findings card (IOP / diagnosis / colour
          // vision / cover test / dominant eye). Only forward filled fields;
          // IOP -> number so the backend's 0-80 mmHg bound applies.
          const f = data.clinicalFindings;
          if (f) {
            if (f.iopRight) cf.iopRight = parseFloat(f.iopRight);
            if (f.iopLeft) cf.iopLeft = parseFloat(f.iopLeft);
            if (f.diagnosis) cf.diagnosis = f.diagnosis;
            if (f.colourVision) cf.colourVision = f.colourVision;
            if (f.coverTest) cf.coverTest = f.coverTest;
            if (f.dominantEye) cf.dominantEye = f.dominantEye;
          }
          return Object.keys(cf).length > 0 ? cf : undefined;
        })(),
        // CLI-11: forward the structured SOAP note when the optometrist filled
        // at least one field. An entirely empty SOAP note is omitted so the
        // backend keeps the test as a refraction-only record (unchanged).
        soapNote: (() => {
          const sn = data.soapNote;
          if (!sn) return undefined;
          const hasText =
            sn.chiefComplaint || sn.historyPresentIllness || sn.ocularHistory ||
            sn.systemicHistory || sn.familyHistory || sn.medications || sn.allergies ||
            sn.vduUsage || sn.vaRightUnaided || sn.vaLeftUnaided || sn.vaRightAided ||
            sn.vaLeftAided || sn.vaBinocular || sn.iopRight || sn.iopLeft ||
            sn.colourVision || sn.coverTest || sn.dominantEye || sn.pupils ||
            sn.ocularMotility || sn.slitLampSummary || sn.fundusSummary ||
            sn.assessment || (sn.dxCodes && sn.dxCodes.length > 0) ||
            sn.plan || sn.planReferral || sn.planFollowUp || sn.patientInstructions;
          if (!hasText) return undefined;
          // Shape matches the backend SoapNote camelCase aliases.
          return {
            chiefComplaint: sn.chiefComplaint || undefined,
            historyPresentIllness: sn.historyPresentIllness || undefined,
            ocularHistory: sn.ocularHistory || undefined,
            systemicHistory: sn.systemicHistory || undefined,
            familyHistory: sn.familyHistory || undefined,
            medications: sn.medications || undefined,
            allergies: sn.allergies || undefined,
            vduUsage: sn.vduUsage || undefined,
            vaRightUnaided: sn.vaRightUnaided || undefined,
            vaLeftUnaided: sn.vaLeftUnaided || undefined,
            vaRightAided: sn.vaRightAided || undefined,
            vaLeftAided: sn.vaLeftAided || undefined,
            vaBinocular: sn.vaBinocular || undefined,
            iopRight: sn.iopRight ? parseFloat(sn.iopRight) : undefined,
            iopLeft: sn.iopLeft ? parseFloat(sn.iopLeft) : undefined,
            colourVision: sn.colourVision || undefined,
            coverTest: sn.coverTest || undefined,
            dominantEye: sn.dominantEye || undefined,
            pupils: sn.pupils || undefined,
            ocularMotility: sn.ocularMotility || undefined,
            slitLampSummary: sn.slitLampSummary || undefined,
            fundusSummary: sn.fundusSummary || undefined,
            assessment: sn.assessment || undefined,
            dxCodes:
              sn.dxCodes && sn.dxCodes.length > 0
                ? sn.dxCodes.map(d => ({ code: d.code, description: d.description, system: d.system }))
                : undefined,
            plan: sn.plan || undefined,
            planReferral: sn.planReferral || undefined,
            planReferralTo: sn.planReferralTo || undefined,
            planFollowUp: sn.planFollowUp || undefined,
            planFollowUpWeeks: sn.planFollowUpWeeks || undefined,
            patientInstructions: sn.patientInstructions || undefined,
          };
        })(),
      });

      toast.success('Eye test saved successfully');
      setShowEyeTestForm(false);
      setSelectedPatient(null);
      setCurrentTestId(null);
      await loadData();
    } catch (err: any) {
      // Surface the backend's specific validation message (e.g. "Right eye CYL
      // value -50 is outside the valid range (-6 to 6)") instead of a generic
      // failure, so the optometrist knows which field to fix.
      const detail = err?.response?.data?.detail;
      let msg = 'Failed to save eye test';
      if (typeof detail === 'string') {
        msg = detail;
      } else if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0];
        const field = Array.isArray(first?.loc) ? first.loc[first.loc.length - 1] : '';
        msg = field ? `${field}: ${first?.msg || 'invalid value'}` : first?.msg || msg;
      }
      toast.error(msg);
    }
  };

  // Save customer and add to queue.
  // The flow tolerates duplicates by design: if a record with this mobile
  // already exists we silently use it instead of 400'ing — repeat walk-ins
  // shouldn't get blocked at the front desk. Order of operations:
  //   1. sanitize the mobile to 10 digits (matches backend regex)
  //   2. lookup by mobile — if found, use that record
  //   3. otherwise create
  //   4. if create races against a parallel create and 400s with "already
  //      exists", retry the lookup and use the winner
  //   5. queue the patient against whichever customer id we ended up with
  const handleSaveCustomer = async (customerData: CustomerFormData) => {
    try {
      const sanitizedMobile = (customerData.mobileNumber || '').replace(/\D/g, '').slice(-10);
      if (sanitizedMobile.length !== 10) {
        toast.error('Mobile number must contain 10 digits');
        throw new Error('Invalid mobile');
      }

      const lookupByMobile = async (): Promise<any | null> => {
        try {
          const r = await customerApi.searchByPhone(sanitizedMobile);
          if (!r) return null;
          if (Array.isArray(r)) return r[0] || null;
          if ((r as any).customer) return (r as any).customer;
          if (Array.isArray((r as any).customers)) return (r as any).customers[0] || null;
          // Bare object response (legacy shape)
          if ((r as any).customer_id || (r as any)._id || (r as any).id) return r;
          return null;
        } catch {
          return null;
        }
      };

      let existing = await lookupByMobile();
      let isExisting = !!existing;

      let customerId: string | undefined;
      let customerName: string;

      // Build a billing_address object only when at least one address
      // field has content; otherwise omit so we don't blow away any
      // existing address on update.
      const billingAddress =
        customerData.address || customerData.city || customerData.pincode || customerData.state
          ? {
              address: customerData.address || undefined,
              city: customerData.city || undefined,
              state: customerData.state || undefined,
              pincode: customerData.pincode || undefined,
            }
          : undefined;

      const patientsPayload = (customerData.patients || [])
        .filter(p => p.name && p.name.trim())
        .map(p => ({
          name: p.name,
          mobile: p.mobile ? p.mobile.replace(/\D/g, '').slice(-10) : undefined,
          dob: p.dateOfBirth || undefined,
          relation: p.relation || 'Self',
        }));

      if (existing) {
        customerId = existing.customer_id || existing._id || existing.id;
        customerName = existing.name || customerData.fullName;

        // Amend the existing record — backend ignores empty diffs and
        // appends patients additively (de-dups on name+mobile). This lets
        // the operator add a DOB / marketing-opt-out / new dependent on
        // a return visit without ever hitting the duplicate-mobile wall.
        const updatePayload: Record<string, unknown> = {
          marketing_consent: customerData.marketingConsent,
        };
        if (customerData.dateOfBirth) updatePayload.dob = customerData.dateOfBirth;
        if (customerData.anniversary) updatePayload.anniversary = customerData.anniversary;
        if (customerData.email && customerData.email !== existing.email) {
          updatePayload.email = customerData.email;
        }
        if (
          customerData.customerType === 'B2B' &&
          customerData.gstNumber &&
          customerData.gstNumber !== existing.gstin
        ) {
          updatePayload.gstin = customerData.gstNumber;
          updatePayload.customer_type = 'B2B';
        }
        if (billingAddress) updatePayload.billing_address = billingAddress;
        if (patientsPayload.length > 0) updatePayload.patients = patientsPayload;

        try {
          await customerApi.updateCustomer(customerId as string, updatePayload);
        } catch {
          // Don't block the queue add if the amend fails — log + continue.
          // eslint-disable-next-line no-console
          console.warn('[Clinical] updateCustomer failed; proceeding with queue add anyway');
        }
      } else {
        const customerPayload = {
          name: customerData.fullName,
          mobile: sanitizedMobile,
          email: customerData.email || undefined,
          dob: customerData.dateOfBirth || undefined,
          anniversary: customerData.anniversary || undefined,
          customer_type: customerData.customerType,
          gstin: customerData.customerType === 'B2B' ? customerData.gstNumber : undefined,
          billing_address: billingAddress,
          marketing_consent: customerData.marketingConsent,
          patients: patientsPayload,
        };

        try {
          const created = await customerApi.createCustomer(customerPayload as any);
          customerId = created?.customer_id || created?.id;
          customerName = created?.name || customerData.fullName;
        } catch (err: any) {
          const detail: string =
            err?.response?.data?.detail ?? err?.message ?? '';
          if (/already exists/i.test(detail)) {
            // Race: someone created the record between our lookup and POST.
            // Fall back to lookup-and-use rather than blocking the operator.
            existing = await lookupByMobile();
            if (existing) {
              customerId = existing.customer_id || existing._id || existing.id;
              customerName = existing.name || customerData.fullName;
              isExisting = true;
            } else {
              throw err;
            }
          } else {
            throw err;
          }
        }
      }

      // Decide which patient to queue. Prefer the modal's first patient (the
      // operator's explicit choice for this visit); fall back to the
      // existing customer's first registered patient; finally fall back to
      // the customer themselves.
      const modalPatient = customerData.patients?.[0];
      const existingPatient =
        Array.isArray(existing?.patients) && existing.patients.length > 0
          ? existing.patients[0]
          : null;
      const queuePatientName =
        (modalPatient?.name && modalPatient.name.trim()) ||
        (existingPatient?.name) ||
        customerName;
      const queueAge = modalPatient?.dateOfBirth
        ? calculateAge(modalPatient.dateOfBirth)
        : undefined;

      const queuePatientId =
        (modalPatient as any)?.id ||
        (modalPatient as any)?.patient_id ||
        (existingPatient as any)?.patient_id ||
        (existingPatient as any)?.id ||
        undefined;
      await clinicalApi.addToQueue({
        storeId: user?.activeStoreId || '',
        patientName: queuePatientName,
        customerPhone: sanitizedMobile,
        customerId,
        patientId: queuePatientId,
        age: queueAge,
        reason: 'Eye examination',
      });

      if (isExisting) {
        toast.info(
          `Existing customer record amended — ${queuePatientName} added to queue (${customerName})`,
        );
      } else {
        toast.success(
          `Customer created and ${queuePatientName} added to queue`,
        );
      }

      setShowAddCustomerModal(false);
      setAddCustomerInitialName('');
      await loadData();
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message || 'Failed to add customer';
      toast.error(detail);
      throw error;
    }
  };

  // Helper function to calculate age from date of birth
  const calculateAge = (dob: string): number => {
    const birthDate = new Date(dob);
    const today = new Date();
    let age = today.getFullYear() - birthDate.getFullYear();
    const monthDiff = today.getMonth() - birthDate.getMonth();
    if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) {
      age--;
    }
    return age;
  };

  const waitingCount = queue.filter(q => q.status === 'WAITING').length;
  const inProgressCount = queue.filter(q => q.status === 'IN_PROGRESS').length;
  const completedCount = completedTests.length;

  const formatTime = (dateStr: string) => {
    return new Date(dateStr).toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const formatPower = (value: number | null) => {
    if (value === null) return '-';
    return value >= 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  };

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Clinical</div>
          <h1>The queue, by token.</h1>
          <div className="hint">Optometrist queue · refraction form · A5 Rx card handoff to POS.</div>
        </div>
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <button
            onClick={loadData}
            disabled={isLoading}
            className="btn sm"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          {canAddPatient && (
            <>
              {/* Phase 6.13 — search-existing first, new-patient second.
                  Most walk-ins are repeat customers; this flow saves
                  re-keying their details every visit. */}
              <button
                onClick={() => setShowQueueExistingModal(true)}
                className="btn sm"
              >
                <Search className="w-4 h-4" /> Queue existing
              </button>
              <button
                onClick={() => setShowAddCustomerModal(true)}
                className="btn sm primary"
              >
                <Plus className="w-4 h-4" /> New patient
              </button>
            </>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="s-section" style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)', display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>{error}</span>
          <button onClick={loadData} className="btn sm" style={{ marginLeft: 'auto' }}>Retry</button>
        </div>
      )}

      {/* 3-card stat strip */}
      <div className="stat-strip stat-strip-3">
        <div>
          <div className="l">Waiting</div>
          <div className="v" style={{ color: waitingCount > 0 ? 'var(--warn)' : 'var(--ink)' }}>{waitingCount}</div>
          <div className="d">in queue</div>
        </div>
        <div>
          <div className="l">In exam</div>
          <div className="v" style={{ color: inProgressCount > 0 ? 'var(--info)' : 'var(--ink)' }}>{inProgressCount}</div>
          <div className="d">currently active</div>
        </div>
        <div>
          <div className="l">Completed today</div>
          <div className="v" style={{ color: completedCount > 0 ? 'var(--ok)' : 'var(--ink)' }}>{completedCount}</div>
          <div className={clsx('d', completedCount > 0 && 'good')}>{completedCount === 1 ? 'eye test' : 'eye tests'}</div>
        </div>
      </div>

      {/* Tabs — underline style (shared .inv-tabs) */}
      <div className="inv-tabs">
        <button
          onClick={() => setActiveTab('queue')}
          className={activeTab === 'queue' ? 'on' : ''}
        >
          <Clock className="w-4 h-4" />
          Queue <span className="count">· {waitingCount + inProgressCount}</span>
        </button>
        <button
          onClick={() => setActiveTab('completed')}
          className={activeTab === 'completed' ? 'on' : ''}
        >
          <CheckCircle className="w-4 h-4" />
          Completed today <span className="count">· {completedCount}</span>
        </button>
        {canViewAbuseAlerts && (
          <button
            onClick={() => setActiveTab('abuse-alerts')}
            className={activeTab === 'abuse-alerts' ? 'on' : ''}
          >
            <AlertTriangle className="w-4 h-4" />
            Abuse alerts
          </button>
        )}
      </div>

      {/* Queue Tab — token-first intake (.q-item layout per design) */}
      {activeTab === 'queue' && (
        <div>
          {isLoading ? (
            <div className="card flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : queue.length === 0 ? (
            <div className="card text-center py-12 text-gray-500">
              <Eye className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No patients in queue</p>
            </div>
          ) : (
            queue.map((item) => {
              const statusConfig = STATUS_CONFIG[item.status] || {
                label: item.status,
                class: 'bg-gray-100 text-gray-500',
              };
              const isActionLoading = actionLoading === item.id;
              const linkedCustomerId = item.customerId;
              const linkedCustomerName = item.customerName;
              const isPatientCustomerSplit =
                !!linkedCustomerId &&
                !!linkedCustomerName &&
                linkedCustomerName !== item.patientName;
              const isLate = item.status === 'WAITING' && item.waitTime > 10;
              const isCurrent = item.status === 'IN_PROGRESS';

              return (
                <div
                  key={item.id}
                  className={clsx('q-item', isCurrent && 'cur')}
                >
                  {/* Token (mono, brand red) + tiny print action */}
                  <div className="tok-stack">
                    <div className="tok">{item.tokenNumber}</div>
                    <button
                      type="button"
                      className="print-btn"
                      onClick={() => {
                        if (storeInfo) {
                          setPrintToken({
                            tokenNumber: item.tokenNumber,
                            patientName: item.patientName,
                            dateTime: item.createdAt,
                            optometristAssigned: undefined,
                            queuePosition: [...queue].indexOf(item) + 1,
                          });
                        }
                      }}
                      title="Print token"
                    >
                      Print
                    </button>
                  </div>

                  {/* Patient (subject) — and the linked customer (account-holder)
                      are surfaced separately when they differ. Most walk-ins
                      have patient == customer; dependents (e.g. a child) won't. */}
                  <div className="who">
                    <div className="n">{item.patientName}</div>
                    {isPatientCustomerSplit && (
                      <div className="for-cust" title="Patient is a dependent of this customer">
                        <span style={{ opacity: 0.6 }}>For</span>
                        <span>{linkedCustomerName}</span>
                        <span style={{ opacity: 0.6 }}>·</span>
                        <span>{linkedCustomerId}</span>
                      </div>
                    )}
                    <div className="p">
                      <span className="flex items-center gap-1">
                        <Phone className="w-3 h-3" />
                        {item.customerPhone || '—'}
                      </span>
                      {item.age != null && (
                        <>
                          <span className="sep">·</span>
                          <span>Age {item.age}</span>
                        </>
                      )}
                      {item.reason && (
                        <>
                          <span className="sep">·</span>
                          <span>{item.reason}</span>
                        </>
                      )}
                    </div>
                    <div className="chips">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium',
                          statusConfig.class,
                        )}
                      >
                        {statusConfig.label}
                      </span>
                    </div>
                  </div>

                  {/* Wait time (mono pill, red when late) */}
                  <div className={clsx('waited', isLate && 'late')}>
                    {item.status === 'COMPLETED' ? (
                      <span>—</span>
                    ) : (
                      <>
                        <span className="v">{item.waitTime}</span>m {isLate ? 'late' : 'wait'}
                      </>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2">
                    {/* Prescriptions / history — view past Rx for this customer,
                        edit them, or add a fresh one (bugs #1-#3).
                        CLI-1 fix: guard on a real customerId — falling back to
                        item.id (queue row id) causes a 404 on the Rx lookup. */}
                    <button
                      onClick={() => {
                        if (!linkedCustomerId) {
                          toast.info('No customer account linked to this queue entry. Rx history is unavailable.');
                          return;
                        }
                        setRxHistoryFor({
                          customerId: linkedCustomerId,
                          customerName: item.patientName,
                          patientId: linkedCustomerId,
                        });
                      }}
                      className="btn sm"
                      title={linkedCustomerId ? 'View / edit prescriptions' : 'No customer linked'}
                    >
                      <FileText className="w-4 h-4" />
                      Rx history
                    </button>
                    {item.status === 'WAITING' && canStartTest && (
                      <button
                        onClick={async () => {
                          const testId = await handleStartTest(item.id);
                          if (testId) {
                            setCurrentTestId(testId);
                            handleOpenEyeTest(item);
                          }
                        }}
                        disabled={isActionLoading}
                        className="btn sm primary disabled:opacity-50"
                      >
                        {isActionLoading ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Play className="w-4 h-4" />
                        )}
                        Start
                      </button>
                    )}
                    {item.status === 'IN_PROGRESS' && canStartTest && (
                      <button
                        onClick={() => {
                          const testId = item.testId || item.id;
                          setCurrentTestId(testId);
                          handleOpenEyeTest(item);
                        }}
                        className="btn sm primary"
                      >
                        <Eye className="w-4 h-4" />
                        Continue
                      </button>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* Completed Tab */}
      {activeTab === 'completed' && (
        <div className="card overflow-hidden">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
            </div>
          ) : completedTests.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <CheckCircle className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>No tests completed today</p>
            </div>
          ) : (
            <div className="divide-y divide-gray-200">
              {completedTests.map(test => (
                <div key={test.id} className="p-4 hover:bg-gray-100 transition-colors">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center">
                        <User className="w-5 h-5 text-green-600" />
                      </div>
                      <div>
                        <p className="font-medium text-gray-900">{test.patientName}</p>
                        <p className="text-sm text-gray-500">
                          Completed at {formatTime(test.completedAt)}
                        </p>
                      </div>
                    </div>

                    {/* Quick Rx Preview */}
                    <div className="flex items-center gap-6">
                      <div className="text-sm">
                        <p className="text-gray-500">R: {formatPower(readEyePower(test, 'right', 'sphere'))} / {formatPower(readEyePower(test, 'right', 'cylinder'))}</p>
                        <p className="text-gray-500">L: {formatPower(readEyePower(test, 'left', 'sphere'))} / {formatPower(readEyePower(test, 'left', 'cylinder'))}</p>
                      </div>
                      {/* CLI-1 fix: guard on a real customerId — test.id is the
                          eye-test record id, not the customer id. Without a
                          real customer id the Rx lookup returns 404. */}
                      <button
                        type="button"
                        onClick={() => {
                          if (!test.customerId) {
                            toast.info('No customer account linked to this test. Rx history is unavailable.');
                            return;
                          }
                          setRxHistoryFor({
                            customerId: test.customerId,
                            customerName: test.patientName,
                            patientId: test.customerId,
                          });
                        }}
                        className="p-2 text-gray-500 hover:text-teal-600 transition-colors"
                        title={test.customerId ? 'View / edit prescriptions' : 'No customer linked'}
                      >
                        <Eye className="w-5 h-5" />
                      </button>
                      <button
                        onClick={() => setPrintRxCard({
                          id: test.id,
                          patientName: test.patientName,
                          date: test.completedAt,
                          optometristName: user?.name || 'Optometrist',
                          rightEye: { sphere: readEyePower(test, 'right', 'sphere') || 0, cylinder: readEyePower(test, 'right', 'cylinder') || 0, axis: readEyePower(test, 'right', 'axis') || 0, add: 0 },
                          leftEye: { sphere: readEyePower(test, 'left', 'sphere') || 0, cylinder: readEyePower(test, 'left', 'cylinder') || 0, axis: readEyePower(test, 'left', 'axis') || 0, add: 0 },
                          pd: 0,
                          visualAcuity: '',
                          notes: '',
                          storeName: storeInfo?.storeName || 'Better Vision Optics',
                          storePhone: storeInfo?.phone || '',
                        })}
                        className="p-2 text-gray-500 hover:text-green-500 transition-colors"
                        title="Print Rx Card"
                      >
                        <FileText className="w-5 h-5" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Abuse Alerts Tab — self-fetching, scoped to the active store. */}
      {activeTab === 'abuse-alerts' && canViewAbuseAlerts && (
        <AbuseDetection storeId={user?.activeStoreId} />
      )}

      {/* Prescription Card Print Modal */}
      {printRxCard && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg max-w-2xl w-full max-h-[90vh] overflow-y-auto">
            <PrescriptionCard prescription={printRxCard} />
            <div className="flex justify-end p-4 border-t">
              <button
                onClick={() => setPrintRxCard(null)}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Print Token Modal */}
      {printToken && storeInfo && (
        <EyeTestTokenPrint
          token={printToken}
          store={storeInfo}
          onClose={() => setPrintToken(null)}
        />
      )}

      {/* Eye Test Form Modal */}
      <EyeTestForm
        isOpen={showEyeTestForm}
        onClose={() => {
          setShowEyeTestForm(false);
          setSelectedPatient(null);
          setCurrentTestId(null);
        }}
        onSave={handleSaveEyeTest}
        patient={selectedPatient}
        optometristName={user?.name}
      />

      {/* Prescription history / edit / new — per customer, grouped by family */}
      {rxHistoryFor && (
        <ClinicPrescriptionHistory
          isOpen={!!rxHistoryFor}
          onClose={() => setRxHistoryFor(null)}
          customerId={rxHistoryFor.customerId}
          customerName={rxHistoryFor.customerName}
          defaultPatientId={rxHistoryFor.patientId}
        />
      )}

      {/* Add Customer Modal (replaces simple patient modal) */}
      <AddCustomerModal
        isOpen={showAddCustomerModal}
        onClose={() => {
          setShowAddCustomerModal(false);
          setAddCustomerInitialName('');
        }}
        onSave={handleSaveCustomer}
        initialName={addCustomerInitialName}
      />

      {/* Phase 6.13 — Queue existing customer. Opens first; falls
          through to AddCustomerModal if no match. */}
      <QueueExistingCustomerModal
        isOpen={showQueueExistingModal}
        onClose={() => setShowQueueExistingModal(false)}
        storeId={user?.activeStoreId}
        onQueue={async (customer, patient) => {
          try {
            // Search hits return the raw Mongo doc which carries `mobile`,
            // not the camelCase `phone` from the TS type. Read both so the
            // queue request never lands at the backend with phone undefined
            // (which 422'd silently before).
            const phone = (customer as any).phone || (customer as any).mobile || '';
            await clinicalApi.addToQueue({
              storeId: user?.activeStoreId || '',
              patientName: patient?.name || customer.name,
              customerPhone: phone,
              age: patient?.dateOfBirth ? calculateAge(patient.dateOfBirth) : undefined,
              reason: 'Eye examination',
              customerId: (customer as any).customer_id || customer.id,
              patientId: (patient as any)?.id || (patient as any)?.patient_id,
            });
            toast.success(`${patient?.name || customer.name} added to queue`);
            setShowQueueExistingModal(false);
            await loadData();
          } catch (e) {
            // eslint-disable-next-line no-console
            console.error('[Clinical] addToQueue failed:', e);
            toast.error('Could not add to queue. Try again.');
          }
        }}
        onCreateNew={(initialQuery) => {
          // User couldn't find the customer — swap to the create flow,
          // pre-filling whatever they typed (name OR phone; the create
          // modal decides based on whether it's numeric).
          setShowQueueExistingModal(false);
          setAddCustomerInitialName(initialQuery);
          setShowAddCustomerModal(true);
        }}
      />
    </div>
  );
}

export default ClinicalPage;
