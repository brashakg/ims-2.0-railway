// ============================================================================
// IMS 2.0 - Workshop: scan-to-advance + station queues + stage monitor
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet). Imported DIRECTLY
// from their modules (not via the services/api barrel) exactly as before.

import { ScanToAdvance } from '../../components/labels/ScanToAdvance';
import { StageMonitorBoard } from '../../components/labels/StageMonitorBoard';
import { StationQueueBoard } from '../../components/labels/StationQueueBoard';
import type { WorkshopPageState } from './useWorkshopPage';

export function WorkshopBoards({ page }: { page: WorkshopPageState }) {
  const { jobs, toast, loadJobs, user, setLabelSpec, setSelectedJob } = page;
  return (
    <>
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

      {/* F2 -- lab floor map: per-station queues with SLA-aged dwell chips +
          links to each bench scan terminal. Managers can edit SLA inline. */}
      <StationQueueBoard
        storeId={user?.activeStoreId}
        canConfigure={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'].includes(
          user?.activeRole || '',
        )}
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
          currentStation: j.current_station,
        }))}
        onPrintStage={(jobId) => setLabelSpec({ kind: 'job', jobId, type: 'stage' })}
        onSelectJob={(jobId) => {
          const j = jobs.find((x) => x.id === jobId);
          if (j) setSelectedJob(j);
        }}
      />

    </>
  );
}
