/**
 * Workshop QC-handover helpers (pure, no React) — extracted so they can be unit
 * tested. See backend/api/routers/workshop.py for the authoritative rules; these
 * only steer the UI toward the right next action, they never authorise anything.
 */

/** Minimal shape these helpers need. The real Job type is a superset. */
export interface QcJobLike {
  status: string;
  qc_passed?: boolean;
  qc_waived?: boolean;
  current_station?: string | null;
  fitting_details?: { confirmed_by_sales?: boolean } | null;
}

/**
 * PATIENT SAFETY: a job is cleared for handover only when lens QC PASSED or was
 * explicitly (audited) waived — identical to the backend predicate
 * workshop._qc_cleared, including its strict `=== true` comparison so a truthy
 * string like 'false' does NOT read as a pass. Both fields are absent on a job
 * that was never QC'd, so absent reads as "not recorded".
 */
export const hasQcOnFile = (job: QcJobLike) =>
  job.qc_passed === true || job.qc_waived === true;

/**
 * Statuses on which the backend accepts a QC submission (workshop.py
 * _QC_INPUT_STATUSES). COMPLETED and QC_FAILED are omitted here only because the
 * detail panel already renders dedicated QC buttons for them; PENDING is omitted
 * because the backend refuses it (a QC pass would route an unstarted job to
 * READY, skipping the sales-confirm and Delivery-Challan gates on the
 * -> IN_PROGRESS leg).
 *
 * IN_PROGRESS is the one that matters most: no station in the scan flow ever
 * sets COMPLETED, so a job whose DISPATCH -> READY leg is HELD for missing QC
 * keeps the IN_PROGRESS it got at INTAKE and walks on to the PICKUP station.
 * That held job is the state the handover gate actually produces.
 */
export const QC_ACTIONABLE_STATUSES: string[] = ['IN_PROGRESS', 'PROCESSING', 'READY'];

/** Stations at which the job is physically at/next to the handover point. */
export const HANDOVER_STATIONS = ['DISPATCH', 'PICKUP'];

/**
 * True when a missing QC record is an ACTIONABLE counter problem right now: the
 * job is on the pickup shelf, or it has walked to the dispatch/pickup end of the
 * bench with its status held. An early-stage job legitimately has no QC yet, and
 * warning on those would train staff to ignore the message that matters.
 */
export const awaitingHandoverQc = (job: QcJobLike) =>
  !hasQcOnFile(job) &&
  job.status !== 'DELIVERED' &&
  job.status !== 'CANCELLED' &&
  (job.status === 'READY' ||
    HANDOVER_STATIONS.includes((job.current_station || '').toUpperCase()) ||
    // A PENDING job auto-created by the POS safety net has NO current_station at
    // all, so the station test above never fires for it. It is stuck for a
    // different reason -- sales have not confirmed the fitting, which is what
    // blocks PENDING -> IN_PROGRESS -- and it would otherwise show neither a
    // warning nor an action. Narrow on purpose: only an unconfirmed PENDING job,
    // not every early-stage bench job (warning fatigue is the failure mode on
    // the other side).
    isAwaitingSalesConfirmation(job));

/**
 * A PENDING job that sales have not yet confirmed the fitting for. This -- not
 * QC -- is what is actually blocking it, so the UI must say so rather than
 * telling staff to run a QC the API would refuse on a PENDING job.
 */
export const isAwaitingSalesConfirmation = (job: QcJobLike) =>
  job.status === 'PENDING' && !job.fitting_details?.confirmed_by_sales;

/**
 * The sentence to show for a job that needs attention before handover. Branches
 * on WHY it is stuck, so the note always names a step that actually exists.
 */
export const handoverBlockerMessage = (job: QcJobLike): string =>
  isAwaitingSalesConfirmation(job)
    ? 'Sales have not confirmed the fitting for this job yet — confirm the fitting details before it can go to the bench.'
    : 'No QC recorded for this job — run QC before handing it over.';

/**
 * Read an order line's prescription id regardless of casing.
 *
 * orders.item_to_frontend serialises the field as `prescriptionId`, so reading
 * only the snake_case name sent a BLANK prescription_id on every job created
 * from the Workshop page — which both dropped the Rx link the bench grinds
 * against AND made the backend's Rx verification a silent no-op (it returns
 * early on a blank id). That is the fallback route POS tells staff to use when
 * auto-create fails, so the gate has to fire there.
 */
export const resolveItemPrescriptionId = (item: unknown): string => {
  const row = (item ?? {}) as { prescriptionId?: unknown; prescription_id?: unknown };
  return String(row.prescriptionId || row.prescription_id || '');
};

/**
 * Pull the backend's own sentence out of an Axios-shaped error. Workshop gates
 * return either a plain string detail (the QC / Rx gates) or an object with a
 * `message` (the DC hardlock), so both are handled. The QC handover gate
 * deliberately leaves destructive buttons enabled on the reasoning that the
 * server returns a plain-English refusal naming the remedy — that reasoning only
 * holds if the UI actually renders it.
 */
export const backendMessage = (err: unknown, fallback: string): string => {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data
    ?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  const message = (detail as { message?: unknown } | undefined)?.message;
  if (typeof message === 'string' && message.trim()) return message;
  return fallback;
};
