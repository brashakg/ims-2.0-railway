// ============================================================================
// IMS 2.0 - Workshop Page - shared types, status/priority/lens config
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet). No rule changed.

import { Clock, Timer, Zap } from 'lucide-react';
import type { JobStatus, JobPriority } from '../../types';

// Job type
export interface Job {
  id: string;
  jobNumber: string;
  orderNumber: string;
  customerId: string;
  customerName: string;
  customerPhone: string;
  frameName: string;
  frameBarcode?: string;
  lensType: string;
  status: JobStatus;
  priority: JobPriority;
  assignedTo?: string;
  /** Display name resolved server-side; absent when the id names nobody
      (deleted account) — the screen and job card then print the raw id. */
  assignedToName?: string;
  expectedDate: string;
  promisedDate: string;
  createdAt: string;
  completedAt?: string;
  notes?: string;
  // Lens-order lifecycle (snake_case — passes through job_to_frontend as-is).
  lens_status?: LensStatus;
  lens_ordered_at?: string;
  lens_received_at?: string;
  lens_mounted_at?: string;
  ready_notified_at?: string;
  // F2 -- in-house lab station the job is currently at (snake_case, passes
  // through job_to_frontend as-is). Null until the first lab scan.
  current_station?: string | null;
  // QC record (snake_case, passes through job_to_frontend as-is). Absent on a
  // job that has never been QC'd -- see hasQcOnFile below.
  qc_passed?: boolean;
  qc_waived?: boolean;
  // Sales confirmation of the fitting. A PENDING job without it cannot start
  // work at all -- see isAwaitingSalesConfirmation in ./qcHandover.
  fitting_details?: { confirmed_by_sales?: boolean } | null;
}

// PATIENT SAFETY: a job is cleared for handover only when lens QC PASSED or was
// explicitly (audited) waived -- the SAME rule the backend gate enforces
// (workshop._qc_cleared / _QC_REQUIRED_TARGETS), which now blocks -> DELIVERED,
// not just -> READY. Both fields are absent on a job that was never QC'd, so an
// absent field reads as "not recorded". Used only to steer the UI toward the
// right next action; the backend remains the authority.
// Pure QC-handover helpers live in ./qcHandover so they can be unit tested
// without mounting this page. See that module for the reasoning behind each.

// Lens-order lifecycle: forward-only NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED.
export type LensStatus = 'NOT_ORDERED' | 'ORDERED' | 'RECEIVED' | 'MOUNTED';
const LENS_STATUS_CONFIG: Record<LensStatus, { label: string; class: string; next?: LensStatus; nextLabel?: string }> = {
  NOT_ORDERED: { label: 'Lens: Not ordered', class: 'bg-gray-100 text-gray-600', next: 'ORDERED', nextLabel: 'Mark lens ordered' },
  ORDERED: { label: 'Lens: Ordered', class: 'bg-blue-50 text-blue-700', next: 'RECEIVED', nextLabel: 'Mark lens received' },
  RECEIVED: { label: 'Lens: Received', class: 'bg-blue-50 text-blue-700', next: 'MOUNTED', nextLabel: 'Mark lens mounted' },
  MOUNTED: { label: 'Lens: Mounted', class: 'bg-green-50 text-green-700' },
};
export function resolveLensConfig(status: unknown) {
  const key = (typeof status === 'string' ? status : 'NOT_ORDERED') as LensStatus;
  return LENS_STATUS_CONFIG[key] ?? LENS_STATUS_CONFIG.NOT_ORDERED;
}

// Audit Run #2 fix: the workshop page was crashing via error boundary when
// a job doc came back with a status value not in this map (e.g. null, "",
// or a legacy status). The `resolveStatusConfig` / `resolvePriorityConfig`
// helpers below guarantee a sane fallback object instead of undefined.
const UNKNOWN_STATUS = { label: 'Unknown', class: 'bg-gray-100 text-gray-700', step: 0 };
export const STATUS_CONFIG: Record<JobStatus, { label: string; class: string; step: number }> = {
  PENDING: { label: 'Pending', class: 'bg-gray-100 text-gray-700', step: 1 },
  IN_PROGRESS: { label: 'In Progress', class: 'bg-amber-50 text-amber-700', step: 2 },
  PROCESSING: { label: 'Fitting', class: 'bg-amber-50 text-amber-700', step: 2 },
  COMPLETED: { label: 'Completed', class: 'bg-blue-50 text-blue-700', step: 3 },
  QC_FAILED: { label: 'QC Failed', class: 'bg-red-50 text-red-700', step: 2 },
  READY: { label: 'Ready for Pickup', class: 'bg-green-50 text-green-700', step: 4 },
  DELIVERED: { label: 'Delivered', class: 'bg-green-50 text-green-700', step: 5 },
  // Fallback for legacy statuses
  CREATED: { label: 'Created', class: 'bg-gray-100 text-gray-500', step: 1 },
  LENS_ORDERED: { label: 'Lens Ordered', class: 'bg-blue-50 text-blue-700', step: 2 },
  LENS_RECEIVED: { label: 'Lens Received', class: 'bg-blue-50 text-blue-700', step: 3 },
  QC_PENDING: { label: 'QC Pending', class: 'bg-amber-50 text-amber-700', step: 3 },
  QC_PASSED: { label: 'QC Passed', class: 'bg-green-50 text-green-700', step: 4 },
  CANCELLED: { label: 'Cancelled', class: 'bg-red-50 text-red-700', step: 0 },
};

const UNKNOWN_PRIORITY = { label: '—', class: 'text-gray-500', icon: Clock };
const PRIORITY_CONFIG: Record<JobPriority, { label: string; class: string; icon: React.ComponentType<{ className?: string }> }> = {
  NORMAL: { label: 'Normal', class: 'text-gray-500', icon: Clock },
  EXPRESS: { label: 'Express', class: 'text-amber-600', icon: Timer },
  URGENT: { label: 'Urgent', class: 'text-red-500', icon: Zap },
};

// Guarded lookups — audit Run #2 found unguarded accesses throwing to the
// error boundary when the backend returned a status/priority not in the
// maps above. Always return a valid object.
export function resolveStatusConfig(status: unknown) {
  const key = (typeof status === 'string' ? status : '') as JobStatus;
  return STATUS_CONFIG[key] ?? UNKNOWN_STATUS;
}
export function resolvePriorityConfig(priority: unknown) {
  const key = (typeof priority === 'string' ? priority : 'NORMAL') as JobPriority;
  return PRIORITY_CONFIG[key] ?? UNKNOWN_PRIORITY;
}
