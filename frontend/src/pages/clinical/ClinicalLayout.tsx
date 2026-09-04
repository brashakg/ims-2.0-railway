// ============================================================================
// IMS 2.0 - Clinical module layout
// ============================================================================
// Wave 2 split. ClinicalPage was one 910-line mega-page holding FIVE sections
// in useState - queue / completed / prescriptions / abuse-alerts / conversion -
// with not one bookmarkable address between them. Each section is now a real
// page with its own URL:
//
//   /clinical/queue         the optometrist queue, by token (intake lives here)
//   /clinical/completed     eye tests completed today
//   /clinical/prescriptions the ONE prescriptions door (see clinicalRoutes)
//   /clinical/abuse-alerts  managers only (clinicalRoles.CLINICAL_MANAGER_ROLES)
//   /clinical/conversion    optometrist -> retail conversion analytics
//
// This layout keeps what was always on screen on every tab - the editorial
// header, Refresh, the two intake buttons (+ their modals), the online-store
// note, the 3-card stat strip and the section nav. Section content renders in
// the Outlet. The stat strip reads the same React Query cache the queue and
// completed pages use, so it costs no extra fetches.

import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle,
  Clock,
  FileText,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  TrendingUp,
} from 'lucide-react';
import clsx from 'clsx';
import { useIsFetching, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { useIsOnlineStore } from '../../hooks/useIsOnlineStore';
import { clinicalApi } from '../../services/api';
import { PatientIntakeModal } from '../../components/clinical/PatientIntakeModal';
import { QueueExistingCustomerModal } from '../../components/customers/QueueExistingCustomerModal';
import { CLINICAL_MODULE_ROLES, canSeeClinicalAbuseAlerts } from './clinicalRoles';
import { CLINICAL_QK, useClinicalQueue, useTodayTests } from './clinicalQueries';

interface Section {
  path: string;
  label: string;
  icon: typeof Clock;
  /** Managers only, per the single list in clinicalRoles.ts. */
  managerOnly?: boolean;
}

const SECTIONS: Section[] = [
  { path: '/clinical/queue', label: 'Queue', icon: Clock },
  { path: '/clinical/completed', label: 'Completed today', icon: CheckCircle },
  { path: '/clinical/prescriptions', label: 'Prescriptions', icon: FileText },
  { path: '/clinical/abuse-alerts', label: 'Abuse alerts', icon: AlertTriangle, managerOnly: true },
  { path: '/clinical/conversion', label: 'Conversion', icon: TrendingUp },
];

// Helper to calculate age from date of birth (intake flow).
function calculateAge(dob: string): number {
  const birthDate = new Date(dob);
  const today = new Date();
  let age = today.getFullYear() - birthDate.getFullYear();
  const monthDiff = today.getMonth() - birthDate.getMonth();
  if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) {
    age--;
  }
  return age;
}

export function ClinicalLayout() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const queryClient = useQueryClient();

  // OS-065: an ONLINE store has no exam room or walk-ins — queue intake is
  // disabled there so nonsense records can't be attributed to a virtual store.
  const isOnlineStoreActive = useIsOnlineStore();
  const canAddPatient = hasRole(CLINICAL_MODULE_ROLES);
  const canSeeAbuse = canSeeClinicalAbuseAlerts(user?.roles);
  const sections = SECTIONS.filter((s) => !s.managerOnly || canSeeAbuse);

  // Intake modal state (Phase 6.13 — search-existing first, new-patient second).
  const [showAddCustomerModal, setShowAddCustomerModal] = useState(false);
  const [showQueueExistingModal, setShowQueueExistingModal] = useState(false);
  const [addCustomerInitialName, setAddCustomerInitialName] = useState('');

  // Shared cache: the queue + completed pages read these same keys.
  const { data: queue = [] } = useClinicalQueue(user?.activeStoreId);
  const { data: completedTests = [] } = useTodayTests(user?.activeStoreId);
  const isRefreshing = useIsFetching({ queryKey: CLINICAL_QK }) > 0;

  const waitingCount = queue.filter((q) => q.status === 'WAITING').length;
  const inProgressCount = queue.filter((q) => q.status === 'IN_PROGRESS').length;
  const completedCount = completedTests.length;

  const refresh = () => queryClient.invalidateQueries({ queryKey: CLINICAL_QK });

  // Warm the sibling section chunks once the browser is idle, so the FIRST
  // click on any section renders without the lazy-chunk spinner (Wave 1
  // template). Vite dedupes these against the route-level lazy() imports.
  useEffect(() => {
    const idle: (cb: () => void) => void =
      'requestIdleCallback' in window
        ? (cb) => (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(cb)
        : (cb) => { setTimeout(cb, 1500); };
    idle(() => {
      void import('./ClinicalQueuePage');
      void import('./EyeExamPage');
      void import('./ClinicalCompletedPage');
      void import('./ClinicalPrescriptionsPage');
      void import('./ConversionTab');
      if (canSeeAbuse) void import('./ClinicalAbusePage');
    });
  }, [canSeeAbuse]);

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
          <button onClick={refresh} disabled={isRefreshing} className="btn sm">
            {isRefreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          {/* Phase 6.13 — search-existing first, new-patient second. Most
              walk-ins are repeat customers; this flow saves re-keying their
              details every visit. #949-4: on an ONLINE store (no exam room, no
              walk-ins) the intake buttons are replaced by a visible note below
              rather than shown disabled with a tooltip touch users never see. */}
          {canAddPatient && !isOnlineStoreActive && (
            <>
              <button onClick={() => setShowQueueExistingModal(true)} className="btn sm">
                <Search className="w-4 h-4" /> Queue existing
              </button>
              <button onClick={() => setShowAddCustomerModal(true)} className="btn sm primary">
                <Plus className="w-4 h-4" /> New patient
              </button>
            </>
          )}
        </div>
      </div>

      {/* #949-4: ONLINE-store intake note (visible, not a tooltip) — mirrors the
          Attendance-page pattern so touch users get a real explanation. */}
      {isOnlineStoreActive && (
        <div
          className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600"
          style={{ marginBottom: 14 }}
        >
          This is the online store — it has no exam room or walk-in queue. Switch to a
          physical store from the top bar to queue a patient.
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

      {/* Section nav — each entry is a real URL now, so every section is
          linkable and bookmarkable.
          ponytail: still <button>, not <NavLink>, because index.css styles
          `.inv-tabs button` by element — an <a> would render unstyled and
          index.css is outside this PR's blast radius. */}
      <div className="inv-tabs">
        {sections.map(({ path, label, icon: TabIcon }) => (
          <button
            key={path}
            onClick={() => navigate(path)}
            className={pathname === path ? 'on' : ''}
          >
            <TabIcon className="w-4 h-4" />
            {label}
            {path === '/clinical/queue' && (
              <span className="count">· {waitingCount + inProgressCount}</span>
            )}
            {path === '/clinical/completed' && (
              <span className="count">· {completedCount}</span>
            )}
          </button>
        ))}
      </div>

      <Outlet />

      {/* Clinical patient-intake modal: token-first patient identity + inline
          Rx grid (OD/OS x SPH/CYL/AXIS/ADD/PD/VA). Reuses the customer +
          prescription + queue APIs. Distinct from the POS "Add Customer" form. */}
      <PatientIntakeModal
        isOpen={showAddCustomerModal}
        onClose={() => {
          setShowAddCustomerModal(false);
          setAddCustomerInitialName('');
        }}
        storeId={user?.activeStoreId}
        initialName={addCustomerInitialName}
        onComplete={async () => {
          setShowAddCustomerModal(false);
          setAddCustomerInitialName('');
          await refresh();
        }}
      />

      {/* Phase 6.13 — Queue existing customer. Opens first; falls
          through to the clinical PatientIntakeModal if no match. */}
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
            await refresh();
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

export default ClinicalLayout;
