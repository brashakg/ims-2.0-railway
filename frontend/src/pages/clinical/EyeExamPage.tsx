// ============================================================================
// IMS 2.0 - /clinical/test/:entryId  (and /clinical/test/amend/:testId)
// ============================================================================
// The eye examination is its OWN PAGE with its own URL, opened from a queue
// row. It used to open as a modal over the queue (owner, 2026-09-04: "why is
// this screen still a pop up"); the modal is deleted.
//
//   /clinical/test/:entryId        a queue entry: Start (WAITING) or Continue
//                                  (IN_PROGRESS, possibly paused mid-way).
//   /clinical/test/amend/:testId   an already-completed exam, reopened from Rx
//                                  history to be corrected. Same page, same
//                                  brain; only the save door differs.
//
// Data: the queue entry from the shared clinical cache (token, age, patient),
// the test document from GET /clinical/tests/{id} (the steps recorded so
// far), and the family Rx from GET /prescriptions/family/{customer_id} for
// the previous Rx the drift guardrail compares against.
//
// Saves: Complete test -> POST /clinical/tests/{id}/complete (mints the Rx,
// closes the queue entry). Save & pause and Save changes -> PUT
// /clinical/tests/{id}/exam (the backend books an amendment only once the
// test is COMPLETED; before that it is a pause and the entry stays in the
// queue). ONE write body for all three: eyeTestWriteBody.

import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Play } from 'lucide-react';
import { clinicalApi, prescriptionApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { apiDetailMessage } from '../../utils/errorHandler';
import { EyeExamWorkbench } from '../../components/clinical/EyeExamWorkbench';
import { eyeTestWriteBody } from '../../components/clinical/eyeTestPayload';
import { previousRxFromFamily } from '../../components/clinical/rxDrift';
import type { EyeTestData, PatientInfo } from '../../components/clinical/eyeTestTypes';
import type { StoredEyeTest } from '../../components/clinical/eyeTestHydrate';
import { CLINICAL_QK, useClinicalQueue } from './clinicalQueries';

/** GET /clinical/tests/{id}: the camelCased test document. */
interface TestDoc extends StoredEyeTest {
  status?: string;
  patientName?: string;
  customerPhone?: string;
  customerId?: string | null;
  patientId?: string | null;
  queueId?: string | null;
}

export function EyeExamPage() {
  const { entryId, testId: amendTestId } = useParams<{ entryId?: string; testId?: string }>();
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const queueQuery = useClinicalQueue(user?.activeStoreId);
  const entry = entryId ? queueQuery.data?.find((q) => q.id === entryId) : undefined;
  const testId = amendTestId ?? entry?.testId ?? null;

  const testQuery = useQuery({
    queryKey: [...CLINICAL_QK, 'test', testId ?? 'none'] as const,
    enabled: !!testId,
    queryFn: () => clinicalApi.getTest(testId as string) as Promise<TestDoc>,
  });
  const test = testQuery.data;

  const customerId = entry?.customerId ?? test?.customerId ?? null;
  const patientId = entry?.patientId ?? test?.patientId ?? null;

  const familyQuery = useQuery({
    queryKey: [...CLINICAL_QK, 'family-rx', customerId ?? 'none'] as const,
    enabled: !!customerId,
    // Fail-soft: no family Rx just means no guardrail and no "Against" card.
    queryFn: () => prescriptionApi.getFamilyRx(customerId as string).catch(() => ({ members: [] })),
  });
  const { previous: previousRx, earliest: wearingSince } = useMemo(
    () =>
      previousRxFromFamily(
        (familyQuery.data as { members?: unknown[] } | undefined)?.members as Parameters<typeof previousRxFromFamily>[0],
        { target: patientId ?? customerId, excludeTestId: testId },
      ),
    [familyQuery.data, patientId, customerId, testId],
  );

  const invalidate = () => queryClient.invalidateQueries({ queryKey: CLINICAL_QK });

  // ---- Resolving the subject -------------------------------------------------
  if (!amendTestId && (queueQuery.isLoading || (!entry && queueQuery.isFetching))) {
    return <Spinner />;
  }
  if (!amendTestId && !entry) {
    return (
      <div className="card text-center py-12 text-ink-3">
        <p className="font-medium text-ink">This queue entry is not in today&apos;s queue.</p>
        <p className="text-sm mt-1">It may have been removed, or it belongs to another store.</p>
        <button type="button" className="btn lg mt-4" onClick={() => navigate('/clinical/queue')}>Back to queue</button>
      </div>
    );
  }
  if (entry && !testId) {
    // A WAITING entry reached by URL (the queue's Start button starts the test
    // before navigating). No side effect on load: the optometrist presses Start.
    const start = async () => {
      try {
        await clinicalApi.startTest(entry.id);
        await invalidate();
      } catch {
        toast.error('Failed to start test.');
      }
    };
    return (
      <div className="card text-center py-12">
        <p className="font-medium text-ink">{entry.patientName} is waiting (token {entry.tokenNumber}).</p>
        <p className="text-sm text-ink-3 mt-1">Start the test to open the examination.</p>
        <button type="button" className="btn lg primary mt-4" onClick={start}>
          <Play className="w-4 h-4" /> Start test
        </button>
      </div>
    );
  }
  if (testQuery.isLoading) return <Spinner />;
  if (testQuery.isError || !test) {
    return (
      <div className="card text-center py-12 text-ink-3">
        <p className="font-medium text-ink">Could not load this eye test.</p>
        <button type="button" className="btn lg mt-4" onClick={() => testQuery.refetch()}>Retry</button>
      </div>
    );
  }

  // ---- The exam ----------------------------------------------------------------
  const mode = test.status === 'COMPLETED' ? 'amend' : 'exam';
  const patient: PatientInfo = {
    id: patientId ?? customerId ?? entry?.id ?? '',
    name: entry?.patientName ?? test.patientName ?? 'Patient',
    phone: entry?.customerPhone ?? test.customerPhone ?? '',
    age: entry?.age,
    customerId: customerId ?? '',
  };
  const optometristName = user?.name ?? '';
  const id = testId as string;

  const finish = async (data: EyeTestData) => {
    try {
      if (mode === 'exam') {
        await clinicalApi.completeTest(id, eyeTestWriteBody(data));
        toast.success('Eye test saved successfully');
        await invalidate();
        navigate('/clinical/queue');
      } else {
        await clinicalApi.amendTest(id, eyeTestWriteBody(data));
        toast.success('Eye test updated');
        await invalidate();
        navigate('/clinical/prescriptions');
      }
    } catch (err: unknown) {
      // The backend's specific message (e.g. "Right eye CYL value -50 is
      // outside the valid range") so the optometrist knows which field to fix.
      toast.error(apiDetailMessage(err, mode === 'exam' ? 'Failed to save eye test' : 'Failed to update eye test'));
    }
  };

  const pause = async (data: EyeTestData) => {
    try {
      // The SAME door as an amendment; the backend sees the test is still
      // IN_PROGRESS and books a pause, not a correction.
      await clinicalApi.amendTest(id, eyeTestWriteBody(data));
      toast.success(`Saved. ${patient.name} stays in the queue.`);
      await invalidate();
      navigate('/clinical/queue');
    } catch (err: unknown) {
      toast.error(apiDetailMessage(err, 'Could not save the exam'));
    }
  };

  return (
    <EyeExamWorkbench
      key={id}
      patient={patient}
      token={entry?.tokenNumber}
      optometristName={optometristName}
      initialTest={test}
      previousRx={previousRx}
      wearingSince={wearingSince}
      mode={mode}
      onFinish={finish}
      onPause={mode === 'exam' ? pause : undefined}
      onBack={() => navigate(mode === 'exam' ? '/clinical/queue' : '/clinical/prescriptions')}
    />
  );
}

function Spinner() {
  return (
    <div className="card flex items-center justify-center py-12">
      <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
    </div>
  );
}

export default EyeExamPage;
