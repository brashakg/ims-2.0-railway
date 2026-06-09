// ============================================================================
// IMS 2.0 - Jobs-by-stage monitor board
// ============================================================================
// A read-at-a-glance Kanban board: one column per workshop stage, a card per
// job. Store-scoped (the parent passes already-store-filtered jobs). Each card
// has a quick "print stage sticker" action. Purely presentational + a couple
// of print buttons; the canonical state lives on the job's status field.

import { Printer, Clock } from 'lucide-react';
import clsx from 'clsx';

export interface BoardJob {
  id: string;
  jobNumber?: string;
  customerName?: string;
  status?: string;
  priority?: string;
  promisedDate?: string;
  /** F2 -- the in-house lab station the job is currently at (null until intake). */
  currentStation?: string | null;
}

const STATION_LABEL: Record<string, string> = {
  INTAKE: 'Intake',
  EDGING: 'Edging',
  COATING: 'Coating',
  QC_LAB: 'Lab QC',
  DISPATCH: 'Dispatch',
  PICKUP: 'Pickup',
};

interface StageMonitorBoardProps {
  jobs: BoardJob[];
  onPrintStage?: (jobId: string) => void;
  onSelectJob?: (jobId: string) => void;
}

// Columns mirror the linear scan-advance spine. QC_FAILED is shown as an
// attention lane so rework is visible (it is a branch, not a forward stage).
const COLUMNS: Array<{ key: string; label: string; accent: string }> = [
  { key: 'PENDING', label: 'Received', accent: 'border-t-gray-400' },
  { key: 'IN_PROGRESS', label: 'In Progress', accent: 'border-t-yellow-400' },
  { key: 'COMPLETED', label: 'Work Done (QC)', accent: 'border-t-blue-400' },
  { key: 'READY', label: 'Ready', accent: 'border-t-green-500' },
  { key: 'DELIVERED', label: 'Delivered', accent: 'border-t-emerald-500' },
  { key: 'QC_FAILED', label: 'QC Failed', accent: 'border-t-red-500' },
];

// Legacy / alternate status values map onto a column so no job is dropped.
const STATUS_TO_COLUMN: Record<string, string> = {
  PENDING: 'PENDING',
  CREATED: 'PENDING',
  PROCESSING: 'IN_PROGRESS',
  IN_PROGRESS: 'IN_PROGRESS',
  LENS_ORDERED: 'IN_PROGRESS',
  LENS_RECEIVED: 'IN_PROGRESS',
  COMPLETED: 'COMPLETED',
  QC_PENDING: 'COMPLETED',
  QC_PASSED: 'READY',
  READY: 'READY',
  DELIVERED: 'DELIVERED',
  QC_FAILED: 'QC_FAILED',
};

const PRIORITY_DOT: Record<string, string> = {
  URGENT: 'bg-red-500',
  EXPRESS: 'bg-orange-400',
  NORMAL: 'bg-gray-300',
};

function isOverdue(promisedDate?: string): boolean {
  if (!promisedDate) return false;
  const d = new Date(promisedDate);
  return !isNaN(d.getTime()) && d < new Date();
}

export function StageMonitorBoard({ jobs, onPrintStage, onSelectJob }: StageMonitorBoardProps) {
  const byColumn: Record<string, BoardJob[]> = {};
  for (const col of COLUMNS) byColumn[col.key] = [];
  for (const job of jobs) {
    const col = STATUS_TO_COLUMN[(job.status || '').toUpperCase()] || 'PENDING';
    (byColumn[col] || byColumn.PENDING).push(job);
  }

  return (
    <div className="card">
      <h3 className="text-base font-semibold text-gray-900 mb-3">Jobs by stage</h3>
      <div className="grid gap-3 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        {COLUMNS.map((col) => {
          const colJobs = byColumn[col.key] || [];
          return (
            <div
              key={col.key}
              className={clsx('bg-gray-50 rounded-lg border-t-4 p-2 min-h-[120px]', col.accent)}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
                  {col.label}
                </span>
                <span className="text-xs font-bold text-gray-500 bg-white rounded-full px-2 py-0.5">
                  {colJobs.length}
                </span>
              </div>
              <div className="space-y-2">
                {colJobs.length === 0 ? (
                  <p className="text-[11px] text-gray-400 text-center py-2">--</p>
                ) : (
                  colJobs.map((job) => (
                    <div
                      key={job.id}
                      className={clsx(
                        'bg-white rounded-md border p-2 text-xs',
                        isOverdue(job.promisedDate) ? 'border-red-300' : 'border-gray-200',
                      )}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <button
                          onClick={() => onSelectJob?.(job.id)}
                          className="font-mono font-semibold text-gray-900 hover:text-bv-red-600 truncate text-left"
                          title={job.jobNumber}
                        >
                          {job.jobNumber || job.id}
                        </button>
                        <span
                          className={clsx(
                            'w-2 h-2 rounded-full shrink-0',
                            PRIORITY_DOT[(job.priority || 'NORMAL').toUpperCase()] || PRIORITY_DOT.NORMAL,
                          )}
                          title={job.priority}
                        />
                      </div>
                      {job.customerName && (
                        <div className="text-gray-600 truncate">{job.customerName}</div>
                      )}
                      {job.currentStation && (
                        <div className="mt-1">
                          <span className="inline-block rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-600">
                            At {STATION_LABEL[job.currentStation] || job.currentStation}
                          </span>
                        </div>
                      )}
                      <div className="flex items-center justify-between mt-1">
                        {isOverdue(job.promisedDate) ? (
                          <span className="inline-flex items-center gap-1 text-red-600 font-medium">
                            <Clock className="w-3 h-3" /> Overdue
                          </span>
                        ) : (
                          <span />
                        )}
                        {onPrintStage && (
                          <button
                            onClick={() => onPrintStage(job.id)}
                            className="p-1 text-gray-400 hover:text-bv-red-600 rounded"
                            title="Print stage sticker"
                          >
                            <Printer className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default StageMonitorBoard;
