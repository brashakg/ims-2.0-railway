// ============================================================================
// IMS 2.0 - Workshop: the four KPI cards (server-side KPIs, client fallback)
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet).

import { Wrench, Zap, CheckCircle, AlertTriangle } from 'lucide-react';
import type { WorkshopPageState } from './useWorkshopPage';

export function WorkshopKpiStrip({ page }: { page: WorkshopPageState }) {
  const { kpis, activeJobs, urgentJobs, readyJobs, overdueJobs } = page;
  return (
    <>
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
            <div className="w-10 h-10 bg-amber-50 rounded-lg flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-amber-600" />
            </div>
            <div>
              <p className="text-sm text-gray-500">Overdue</p>
              <p className="text-2xl font-bold text-amber-600">{kpis?.overdue ?? overdueJobs.length}</p>
            </div>
          </div>
        </div>
      </div>

    </>
  );
}
