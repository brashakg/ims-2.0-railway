// ============================================================================
// IMS 2.0 - /clinical/queue
// ============================================================================
// The optometrist queue, by token. Was the "queue" tab of the deleted
// ClinicalPage mega-page. Data comes from the shared clinicalQueries cache
// (the layout's stat strip reads the same keys).
//
// Start / Continue NAVIGATE to the examination page, /clinical/test/:entryId.
// The exam used to open here as a modal (owner, 2026-09-04: "why is this
// screen still a pop up"); that modal is deleted, and the exam's brain --
// state, the one range validator, the save path -- lives with the page.
// NO MOCK DATA - All data from API.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Eye,
  Play,
  FileText,
  Phone,
  Loader2,
  X,
} from 'lucide-react';
import clsx from 'clsx';
import { useQueryClient } from '@tanstack/react-query';
import { clinicalApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { EyeTestTokenPrint } from '../../components/print/EyeTestTokenPrint';
import { ClinicPrescriptionHistory } from '../../components/clinical/ClinicPrescriptionHistory';
import { CLINICAL_MODULE_ROLES } from './clinicalRoles';
import {
  CLINICAL_QK,
  useClinicalQueue,
  useClinicalStoreIdentity,
  type QueueItem,
  type QueueStatus,
} from './clinicalQueries';

const STATUS_CONFIG: Record<QueueStatus, { label: string; class: string }> = {
  WAITING: { label: 'Waiting', class: 'bg-yellow-100 text-yellow-600' },
  IN_PROGRESS: { label: 'In Progress', class: 'bg-blue-100 text-blue-600' },
  COMPLETED: { label: 'Completed', class: 'bg-green-100 text-green-600' },
};

interface TokenPrintData {
  tokenNumber: string;
  patientName: string;
  dateTime: string;
  optometristAssigned?: string;
  queuePosition: number;
}

export function ClinicalQueuePage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: queue = [], isLoading } = useClinicalQueue(user?.activeStoreId);
  const { storeInfo, storeEntity } = useClinicalStoreIdentity(user?.activeStoreId);

  const [printToken, setPrintToken] = useState<TokenPrintData | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Prescription history / edit modal (bugs #1-#3): per-customer Rx view with
  // Edit + New + Print, grouped by family member. Opened from a queue row.
  const [rxHistoryFor, setRxHistoryFor] = useState<{
    customerId: string;
    customerName?: string;
    patientId?: string;
  } | null>(null);

  const canStartTest = hasRole(CLINICAL_MODULE_ROLES);

  const reload = () => queryClient.invalidateQueries({ queryKey: CLINICAL_QK });

  const handleStartTest = async (queueId: string): Promise<string | null> => {
    setActionLoading(queueId);
    try {
      const result = await clinicalApi.startTest(queueId);
      await reload();
      return result?.testId || null;
    } catch {
      toast.error('Failed to start test.');
      return null;
    } finally {
      setActionLoading(null);
    }
  };

  // CLI-1: remove (cancel) a patient from the queue. Backend
  // DELETE /clinical/queue/{id} is store-scoped + audited. Confirm first, then
  // reload so the row disappears. Gated to the same clinical roles as Start.
  const handleRemoveFromQueue = async (item: QueueItem) => {
    const ok = window.confirm(
      `Remove ${item.patientName} (token ${item.tokenNumber}) from the queue?`,
    );
    if (!ok) return;
    setActionLoading(item.id);
    try {
      await clinicalApi.removeFromQueue(item.id);
      toast.success(`${item.patientName} removed from queue`);
      await reload();
    } catch {
      toast.error('Could not remove from queue. Try again.');
    } finally {
      setActionLoading(null);
    }
  };

  /** The examination page for this queue entry. Its own URL, not a modal. */
  const openExam = (item: QueueItem) => navigate(`/clinical/test/${item.id}`);

  return (
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
                      if (testId) openExam(item);
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
                    onClick={() => openExam(item)}
                    className="btn sm primary"
                  >
                    <Eye className="w-4 h-4" />
                    Continue
                  </button>
                )}
                {/* CLI-1: remove / cancel a patient from the queue. Hidden
                    once a test is COMPLETED (nothing to cancel). */}
                {item.status !== 'COMPLETED' && canStartTest && (
                  <button
                    onClick={() => handleRemoveFromQueue(item)}
                    disabled={isActionLoading}
                    className="btn sm disabled:opacity-50"
                    title="Remove from queue"
                  >
                    {isActionLoading ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <X className="w-4 h-4" />
                    )}
                    Remove
                  </button>
                )}
              </div>
            </div>
          );
        })
      )}

      {/* Print Token Modal */}
      {printToken && storeInfo && (
        <EyeTestTokenPrint
          token={printToken}
          store={storeInfo}
          entity={storeEntity}
          onClose={() => setPrintToken(null)}
        />
      )}

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
    </div>
  );
}

export default ClinicalQueuePage;
