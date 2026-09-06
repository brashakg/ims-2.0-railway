// ============================================================================
// IMS 2.0 - Workshop: editorial header, ONLINE-store intake note, error banner
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet). The "New job from
// order" button text is the layout gate's popup trigger - do not reword it.

import { Wrench, AlertTriangle, Loader2, RefreshCw } from 'lucide-react';
import type { WorkshopPageState } from './useWorkshopPage';

export function WorkshopHeader({ page }: { page: WorkshopPageState }) {
  const { isOnlineStoreActive, setShowCreateJob, isLoading, loadJobs, error } = page;
  return (
    <>
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Workshop</div>
          <h1>From Rx to finished job.</h1>
          <div className="hint">Lens ordered → received → mounted → QC. Assign by technician, auto-notify when ready, customer pickup with OTP verify.</div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {/* #949-4: on an ONLINE store (no bench) the intake button is replaced
              by a visible note below rather than shown disabled with a tooltip. */}
          {!isOnlineStoreActive && (
            <button
              onClick={() => setShowCreateJob(true)}
              className="btn sm primary"
            >
              <Wrench className="w-4 h-4" /> New job from order
            </button>
          )}
          <button onClick={loadJobs} disabled={isLoading} className="btn sm">
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
        </div>
      </div>

      {/* #949-4: ONLINE-store intake note (visible, not a tooltip) — mirrors the
          Attendance-page pattern so touch users get a real explanation. */}
      {isOnlineStoreActive && (
        <div
          className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600"
          style={{ marginBottom: 14 }}
        >
          This is the online store — it has no workshop bench. Jobs run on the physical
          stores' benches; switch to a physical store from the top bar to create a job.
        </div>
      )}

      {/* Error State */}
      {error && (
        <div className="card bg-red-50 border-red-200">
          <div className="flex items-center gap-3 text-red-700">
            <AlertTriangle className="w-5 h-5" />
            <p>{error}</p>
            <button onClick={loadJobs} className="ml-auto text-sm underline hover:text-red-900">
              Retry
            </button>
          </div>
        </div>
      )}

    </>
  );
}
