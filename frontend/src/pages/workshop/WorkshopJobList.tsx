// ============================================================================
// IMS 2.0 - Workshop: the job list (loading / empty / one card per job)
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet).

import { Wrench, AlertTriangle, Eye, Phone, User, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import { resolveLensConfig, resolveStatusConfig, resolvePriorityConfig, NOT_ON_FILE } from './shared';
import type { WorkshopPageState } from './useWorkshopPage';

export function WorkshopJobList({ page }: { page: WorkshopPageState }) {
  const { isLoading, filteredJobs, searchQuery, statusFilter, priorityFilter, isOverdue, formatDate, setSelectedJob } = page;
  return (
    <>
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
                  job.priority === 'URGENT' && 'border-red-300 bg-red-50',
                  overdue && job.priority !== 'URGENT' && 'border-amber-300 bg-amber-50'
                )}
              >
                {/* L2-UI-F01: on a phone the date/actions stack under the job
                    info and the badges wrap, so nothing overlaps; from sm up
                    it is the side-by-side card it always was. */}
                <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 sm:gap-4">
                  {/* Job Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 mb-2">
                      <span className="font-bold text-gray-900 break-all">{job.jobNumber}</span>
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
                      <div className="min-w-0 break-words">
                        <p className="text-gray-500">Customer</p>
                        <p className="font-medium flex items-center gap-1">
                          <User className="w-3 h-3" />
                          {job.customerName || NOT_ON_FILE}
                        </p>
                        <p className="text-gray-500 flex items-center gap-1">
                          <Phone className="w-3 h-3" />
                          {job.customerPhone || NOT_ON_FILE}
                        </p>
                      </div>
                      <div className="min-w-0 break-words">
                        <p className="text-gray-500">Frame & Lens</p>
                        <p className="font-medium">{job.frameName || NOT_ON_FILE}</p>
                        <p className="text-gray-500">{job.lensType || NOT_ON_FILE}</p>
                      </div>
                    </div>

                    {job.notes && (
                      <p className="mt-2 text-sm text-amber-800 bg-amber-50 px-2 py-1 rounded">
                        Note: {job.notes}
                      </p>
                    )}
                  </div>

                  {/* Dates & Actions */}
                  <div className="flex flex-wrap items-center justify-between gap-2 sm:block sm:text-right sm:shrink-0">
                    <div className="sm:mb-3">
                      <p className="text-xs text-gray-500">Promise Date</p>
                      <p className={clsx(
                        'font-medium',
                        overdue ? 'text-red-600' : 'text-gray-900'
                      )}>
                        {formatDate(job.promisedDate)}
                      </p>
                    </div>
                    {job.assignedTo && (
                      <p className="text-xs text-gray-500 sm:mb-3">
                        Assigned: {job.assignedToName || job.assignedTo}
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

    </>
  );
}
