// ============================================================================
// IMS 2.0 - Workshop: the job detail modal
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet). The QC handover
// gate is server-enforced; every button, label and blocker sentence here is
// exactly what it was - see ./qcHandover for the reasoning behind each.

import { Eye, Phone, User, Printer, Tag, ClipboardCheck } from 'lucide-react';
import clsx from 'clsx';
import {
  hasQcOnFile,
  awaitingHandoverQc,
  handoverBlockerMessage,
  QC_ACTIONABLE_STATUSES,
} from './qcHandover';
import { resolveLensConfig, resolveStatusConfig, resolvePriorityConfig } from './shared';
import type { LensStatus } from './shared';
import { VendorCaptureBlock } from './VendorCaptureBlock';
import type { WorkshopPageState } from './useWorkshopPage';

export function WorkshopJobDetail({ page }: { page: WorkshopPageState }) {
  const { selectedJob, setSelectedJob, isOverdue, formatDate, loadJobs, lensBusy, handleLensAdvance, canConfirmFitting, setFittingJob, handleStatusChange, canRunQc, setQcModalJob, openReworkModal, qcBusy, pickupName, setPickupName, notifyBusy, handleNotifyReady, setLabelSpec, setPrintJob } = page;
  return (
    <>
      {/* Job Detail Modal */}
      {selectedJob && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full max-h-[90dvh] overflow-y-auto">
            <div className="p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-bold text-gray-900">
                  Job {selectedJob.jobNumber}
                </h2>
                <button
                  onClick={() => setSelectedJob(null)}
                  className="p-2 hover:bg-gray-100 rounded-lg text-gray-500"
                >
                  ×
                </button>
              </div>

              <div className="space-y-4">
                {/* Status & Priority */}
                <div className="flex items-center gap-2">
                  <span className={clsx('px-3 py-1 rounded-full text-sm font-medium', resolveStatusConfig(selectedJob.status).class)}>
                    {resolveStatusConfig(selectedJob.status).label}
                  </span>
                  <span className={clsx('text-sm font-medium', resolvePriorityConfig(selectedJob.priority).class)}>
                    {selectedJob.priority}
                  </span>
                  {isOverdue(selectedJob.promisedDate) && !['READY', 'DELIVERED', 'CANCELLED'].includes(selectedJob.status) && (
                    <span className="px-2 py-1 bg-red-50 text-red-700 text-xs rounded-full font-medium">Overdue</span>
                  )}
                </div>

                {/* Customer */}
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-2">
                  <h3 className="text-sm font-medium text-gray-500">Customer</h3>
                  <p className="font-medium text-gray-900 flex items-center gap-2">
                    <User className="w-4 h-4" /> {selectedJob.customerName}
                  </p>
                  <p className="text-sm text-gray-500 flex items-center gap-2">
                    <Phone className="w-4 h-4" /> {selectedJob.customerPhone}
                  </p>
                </div>

                {/* Job Details */}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-sm text-gray-500">Order Number</p>
                    <p className="font-medium">{selectedJob.orderNumber}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Frame</p>
                    <p className="font-medium">{selectedJob.frameName}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Lens Type</p>
                    <p className="font-medium">{selectedJob.lensType}</p>
                  </div>
                  {selectedJob.frameBarcode && (
                    <div>
                      <p className="text-sm text-gray-500">Frame Barcode</p>
                      <p className="font-medium font-mono text-sm">{selectedJob.frameBarcode}</p>
                    </div>
                  )}
                </div>

                {/* Dates */}
                <div className="grid grid-cols-2 gap-4 bg-gray-50 border border-gray-200 rounded-lg p-4">
                  <div>
                    <p className="text-sm text-gray-500">Created</p>
                    <p className="font-medium">{formatDate(selectedJob.createdAt)}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-500">Promised Date</p>
                    <p className={clsx('font-medium', isOverdue(selectedJob.promisedDate) && 'text-red-600')}>
                      {formatDate(selectedJob.promisedDate)}
                    </p>
                  </div>
                  {selectedJob.assignedTo && (
                    <div>
                      <p className="text-sm text-gray-500">Assigned To</p>
                      <p className="font-medium">{selectedJob.assignedToName || selectedJob.assignedTo}</p>
                    </div>
                  )}
                  {selectedJob.completedAt && (
                    <div>
                      <p className="text-sm text-gray-500">Completed</p>
                      <p className="font-medium">{formatDate(selectedJob.completedAt)}</p>
                    </div>
                  )}
                </div>

                {/* Notes */}
                {selectedJob.notes && (
                  <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                    <p className="text-sm font-medium text-amber-800">Notes</p>
                    <p className="text-sm text-amber-800 mt-1">{selectedJob.notes}</p>
                  </div>
                )}

                {/* Vendor / lens lab — vendor portal hooks (May 2026).
                    Stamps the lab's order ID + tracking URL so the lab can
                    open the portal page; status updates from the lab show
                    up under "Vendor history". */}
                <VendorCaptureBlock job={selectedJob} onSaved={loadJobs} />

                {/* Progress */}
                <div>
                  <p className="text-sm text-gray-500 mb-2">Progress: {resolveStatusConfig(selectedJob.status).label}</p>
                  <div className="h-3 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className={clsx(
                        'h-full transition-all',
                        selectedJob.status === 'QC_FAILED' ? 'bg-red-500' : 'bg-bv-red-600'
                      )}
                      style={{ width: `${(resolveStatusConfig(selectedJob.status).step / 8) * 100}%` }}
                    />
                  </div>
                </div>

                {/* Lens-order lifecycle — forward-only NOT_ORDERED -> ORDERED
                    -> RECEIVED -> MOUNTED. Independent of the job workflow status. */}
                <div className="rounded-lg border border-gray-200 bg-gray-50/60 p-3">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-sm font-medium text-gray-700">Lens order</p>
                    <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', resolveLensConfig(selectedJob.lens_status).class)}>
                      {resolveLensConfig(selectedJob.lens_status).label}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-xs text-gray-500 mb-2">
                    <div>
                      <p className="text-gray-400">Ordered</p>
                      <p className="font-medium text-gray-700">{selectedJob.lens_ordered_at ? formatDate(selectedJob.lens_ordered_at) : '—'}</p>
                    </div>
                    <div>
                      <p className="text-gray-400">Received</p>
                      <p className="font-medium text-gray-700">{selectedJob.lens_received_at ? formatDate(selectedJob.lens_received_at) : '—'}</p>
                    </div>
                    <div>
                      <p className="text-gray-400">Mounted</p>
                      <p className="font-medium text-gray-700">{selectedJob.lens_mounted_at ? formatDate(selectedJob.lens_mounted_at) : '—'}</p>
                    </div>
                  </div>
                  {resolveLensConfig(selectedJob.lens_status).next && (
                    <button
                      onClick={() => handleLensAdvance(selectedJob.id, resolveLensConfig(selectedJob.lens_status).next as LensStatus)}
                      disabled={lensBusy}
                      className="btn-outline text-sm disabled:opacity-50"
                    >
                      {resolveLensConfig(selectedJob.lens_status).nextLabel}
                    </button>
                  )}
                </div>

                {/* Status Transition Buttons */}
                <div className="flex gap-2 flex-wrap">
                  {/* A PENDING job whose fitting sales-confirmation is missing
                      cannot be started, completed or QC'd -- every one of those
                      doors 400s -- and the handover gate then blocks the order.
                      Until now the ONLY UI that could write fitting details was
                      the post-sale POS modal, so dismissing that modal (or
                      creating the job from this page, which sends none) left the
                      job permanently stuck with no in-app way out. Same modal,
                      same PATCH; the backend roles already allow it. */}
                  {selectedJob.status === 'PENDING' && canConfirmFitting
                    && !selectedJob.fitting_details?.confirmed_by_sales && (
                    <button
                      onClick={() => setFittingJob(selectedJob)}
                      className="btn-primary text-sm flex items-center gap-1"
                    >
                      <ClipboardCheck className="w-4 h-4" /> Confirm fitting details
                    </button>
                  )}
                  {selectedJob.status === 'PENDING' && (
                    // Bug fix: was sending 'PROCESSING' which the backend state machine
                    // doesn't recognise (it uses 'IN_PROGRESS'). Backend now also
                    // aliases PROCESSING -> IN_PROGRESS for backward compat, but the
                    // frontend should send the canonical value.
                    <button onClick={() => handleStatusChange(selectedJob.id, 'IN_PROGRESS')} className="btn-primary text-sm">Start Processing</button>
                  )}
                  {(selectedJob.status === 'IN_PROGRESS' || selectedJob.status === 'PROCESSING') && (
                    <button onClick={() => handleStatusChange(selectedJob.id, 'COMPLETED')} className="btn-primary text-sm">Mark Completed</button>
                  )}
                  {selectedJob.status === 'COMPLETED' && canRunQc && (
                    <button
                      onClick={() => setQcModalJob(selectedJob)}
                      className="btn-primary text-sm flex items-center gap-1"
                    >
                      <ClipboardCheck className="w-4 h-4" /> Run QC checklist
                    </button>
                  )}
                  {selectedJob.status === 'QC_FAILED' && canRunQc && (
                    <>
                      <button
                        onClick={() => setQcModalJob(selectedJob)}
                        className="btn-primary text-sm flex items-center gap-1"
                      >
                        <ClipboardCheck className="w-4 h-4" /> Re-run QC
                      </button>
                      <button
                        onClick={() => openReworkModal(selectedJob.id)}
                        disabled={qcBusy}
                        className="btn-outline text-sm disabled:opacity-50"
                      >
                        Send for rework
                      </button>
                    </>
                  )}
                  {/* PATIENT SAFETY: the backend blocks -> DELIVERED without a
                      QC pass/waiver, so QC late in the lifecycle is ongoing
                      workflow, not a legacy shim. Crucially this is NOT limited
                      to READY: no station in the scan flow ever sets COMPLETED,
                      so a job whose DISPATCH -> READY leg is HELD for missing QC
                      keeps the IN_PROGRESS it got at INTAKE and walks on to the
                      PICKUP station. That held job is the state this gate
                      actually produces, and offering the remedy only at READY
                      left the counter with no visible action for it.
                      Same canRunQc role gate, same modal + submit path as the
                      COMPLETED / QC_FAILED buttons above (which keep their own
                      labels). When QC is already on file this is a secondary
                      re-run, so it steps back to btn-outline and Mark Delivered
                      stays the primary green action. */}
                  {QC_ACTIONABLE_STATUSES.includes(selectedJob.status) && canRunQc && (
                    <button
                      onClick={() => setQcModalJob(selectedJob)}
                      className={`${hasQcOnFile(selectedJob) ? 'btn-outline' : 'btn-primary'} text-sm flex items-center gap-1`}
                    >
                      <ClipboardCheck className="w-4 h-4" /> Run QC before handover
                    </button>
                  )}
                  {selectedJob.status === 'READY' && (
                    <div className="flex items-center gap-2 flex-wrap">
                      <input
                        type="text"
                        value={pickupName}
                        onChange={(e) => setPickupName(e.target.value)}
                        placeholder="Collected by (name, optional)"
                        className="input-field text-sm w-56"
                        maxLength={80}
                      />
                      {/* Deliberately NOT disabled when QC is missing: the
                          backend gate is the authority and returns a plain
                          English 400, and a client-side block driven by a field
                          that may simply be absent on an older job would be a
                          fake refusal. The note below states the real state. */}
                      <button onClick={() => handleStatusChange(selectedJob.id, 'DELIVERED')} className="btn-success text-sm">Mark Delivered</button>
                    </div>
                  )}
                  {awaitingHandoverQc(selectedJob) && (
                    <p className="text-xs text-amber-700 w-full">
                      {handoverBlockerMessage(selectedJob)}
                    </p>
                  )}
                  {['COMPLETED', 'READY'].includes(selectedJob.status) && (
                    <button
                      onClick={() => handleNotifyReady(selectedJob.id)}
                      disabled={notifyBusy}
                      className="btn-outline text-sm flex items-center gap-1 disabled:opacity-50"
                    >
                      <Phone className="w-4 h-4" />
                      {selectedJob.ready_notified_at ? 'Notify ready again' : 'Notify ready'}
                    </button>
                  )}
                </div>

                {/* Thermal label actions: traveler/work-order always; stage
                    sticker any time; ready/pickup label at READY. */}
                <div className="flex gap-2 flex-wrap">
                  <button
                    onClick={() => setLabelSpec({ kind: 'job', jobId: selectedJob.id, type: 'traveler' })}
                    className="btn-outline text-sm flex items-center gap-1"
                  >
                    <Tag className="w-4 h-4" /> Traveler label
                  </button>
                  <button
                    onClick={() => setLabelSpec({ kind: 'job', jobId: selectedJob.id, type: 'stage' })}
                    className="btn-outline text-sm flex items-center gap-1"
                  >
                    <Printer className="w-4 h-4" /> Stage sticker
                  </button>
                  {selectedJob.status === 'READY' && (
                    <button
                      onClick={() => setLabelSpec({ kind: 'job', jobId: selectedJob.id, type: 'ready' })}
                      className="btn-outline text-sm flex items-center gap-1 text-green-700 border-green-600"
                    >
                      <Tag className="w-4 h-4" /> Pickup label
                    </button>
                  )}
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      setPrintJob(selectedJob);
                      setSelectedJob(null);
                    }}
                    className="btn-primary flex-1 flex items-center justify-center gap-2"
                  >
                    <Eye className="w-4 h-4" />
                    Print Card
                  </button>
                  <button
                    onClick={() => setSelectedJob(null)}
                    className="btn-outline flex-1"
                  >
                    Close
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

    </>
  );
}
