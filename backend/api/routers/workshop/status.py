"""Job status moves: the status PATCH with every gate, assign, start, complete.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query, Body
from typing import Optional
from ..auth import get_current_user, require_roles
from ...dependencies import get_db, get_workshop_repository, get_audit_repository
from ._shared import (
    MAX_REWORK,
    VALID_JOB_TRANSITIONS,
    WORKSHOP_ROLES,
    _REWORK_OVERRIDE_ROLES,
    _check_dc_hardlock,
    router,
)
from .gates import _is_patient_facing, _qc_cleared, gate_job_handover_payment
from .models import StatusBody
from .helpers import _assert_job_store_access


@router.patch("/jobs/{job_id}/status")
async def update_job_status(
    job_id: str,
    body: Optional[StatusBody] = Body(None),
    status_q: Optional[str] = Query(None, alias="status"),
    notes_q: Optional[str] = Query(None, alias="notes"),
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Update job status (generic endpoint) with state machine validation.

    Accepts the transition target and optional notes either as a JSON body
    (preferred by the frontend via Axios PATCH) or as query parameters
    (backward-compatible with existing callers). The body takes precedence.

    Bug fixed: previous signature used ``Query(...)`` for ``status``, but the
    frontend sends ``api.patch(url, { status, notes })`` which delivers the data
    as a JSON body — not a query string. That mismatch caused every generic
    status transition from the UI to return 422 Unprocessable Entity.
    """
    # Resolve status + notes from body (preferred) or query params (fallback)
    status = (body.status if body else None) or status_q
    notes = (body.notes if body else None) or notes_q

    if not status:
        raise HTTPException(
            status_code=422,
            detail="status is required (provide as JSON body field or ?status= query param)",
        )

    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        current_status = job.get("status", "PENDING")

        # Map legacy frontend status names to canonical backend values.
        # The frontend historically used "PROCESSING" for what the backend
        # calls "IN_PROGRESS" — normalise here so old clients still work.
        STATUS_ALIASES = {"PROCESSING": "IN_PROGRESS"}
        status = STATUS_ALIASES.get(status, status)

        allowed = VALID_JOB_TRANSITIONS.get(current_status, set())
        if status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot transition from {current_status} to {status}. "
                    f"Allowed: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}."
                ),
            )

        # BUG-116c: the workshop may only ACCEPT a job (-> IN_PROGRESS) once sales
        # has confirmed the fitting details (power + product correct). Mirror the
        # /start gate on the generic status PATCH so it cannot be bypassed.
        if status == "IN_PROGRESS" and not (
            (job.get("fitting_details") or {}).get("confirmed_by_sales")
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot start this job: sales must confirm the fitting details "
                    "(confirmed_by_sales) first."
                ),
            )

        # F9 DC HARDLOCK: an external-lab lens (top-level lens_status=ORDERED) may
        # not ADVANCE TO IN_PROGRESS until an accepted Delivery Challan covering its
        # SKU exists at this store -- this is the REAL gate (create-time lens_status
        # is unset). Raises 422 (DC_HARDLOCK) when blocked; an ADMIN+ override_reason
        # bypasses (audited). In-house lenses + a disabled flag pass through.
        if status == "IN_PROGRESS":
            dc_lock = _check_dc_hardlock(
                get_db(),
                job.get("lens_status"),
                (job.get("lens_details") or {}).get("product_id"),
                job.get("store_id"),
                job.get("created_at"),
                current_user,
                (body.override_reason if body else None),
            )
            if dc_lock.get("override_applied"):
                try:
                    audit = get_audit_repository()
                    if audit is not None:
                        audit.create(
                            {
                                "action": "dc_hardlock_override",
                                "entity_type": "workshop_job",
                                "entity_id": job_id,
                                "user_id": current_user.get("user_id"),
                                "detail": {
                                    "transition": "IN_PROGRESS",
                                    "store_id": job.get("store_id"),
                                    "product_id": (job.get("lens_details") or {}).get("product_id"),
                                    "reason": dc_lock.get("reason"),
                                },
                            }
                        )
                except Exception:  # noqa: BLE001
                    pass

        # BUG-116a (patient-safety): a lens job must NOT reach the PATIENT without
        # a QC record. The dedicated QC endpoints (/jobs/{id}/qc, /qc-checklist)
        # set qc_passed/qc_waived before flipping the job to READY; this GENERIC
        # transition previously bypassed that (the gate here was a no-op `pass`),
        # so a job could be PATCHed COMPLETED -> READY with zero QC.
        #
        # DELIVERED is gated by the SAME rule, via the same _qc_cleared() source
        # of truth. The old code reasoned that "DELIVERED from READY is always
        # fine because READY is QC-gated above" -- that only holds for jobs whose
        # READY leg passed through THIS gate. A job that reached READY before the
        # gate existed (live rows) carries no QC record at all, and would still be
        # handed to the patient on a plain READY -> DELIVERED PATCH. Remedy for
        # such a job: run QC on it (the QC endpoints accept a READY job precisely
        # for this) -- a pass/audited waiver clears it, a fail pulls it back off
        # the pickup shelf into QC_FAILED for rework.
        #
        # NOTE: the barcode-scan paths mirror THIS gate (and the IN_PROGRESS
        # sales-confirm + DC gates above) via evaluate_scan_transition_gate --
        # both now read _QC_REQUIRED_TARGETS / _qc_cleared, so they cannot drift.
        if _is_patient_facing(status) and not _qc_cleared(job):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot mark this job {status}: lens QC must pass (record it "
                    f"via the QC endpoint) or be explicitly waived (qc_waived) "
                    f"first."
                ),
            )

        # MONEY GATE (owner ruling): marking a job DELIVERED is the physical
        # handover of the order's goods, so it must clear the SAME money rule
        # as the Orders-screen deliver door - at least partial payment, and a
        # balance still due needs a manager or a manager-approved
        # CREDIT_DELIVERY token. WORKSHOP_ROLES includes WORKSHOP_STAFF, a role
        # the owner's credit-delivery ruling deliberately excludes from taking
        # that decision - the shared gate (services.delivery_gate via
        # gate_job_handover_payment) is what enforces the exclusion. 400/403.
        if status == "DELIVERED":
            gate_job_handover_payment(
                job, current_user, (body.approval_token if body else None)
            )

        # BUG-116d: a QC_FAILED -> IN_PROGRESS move is a rework. Cap it: once the
        # job has already been reworked MAX_REWORK times, only a manager may send
        # it back again (override), otherwise the rework loop is blocked.
        is_rework = current_status == "QC_FAILED" and status == "IN_PROGRESS"
        rework_count = int(job.get("rework_count") or 0)
        if is_rework and rework_count >= MAX_REWORK:
            roles = set(current_user.get("roles") or [])
            if not (roles & _REWORK_OVERRIDE_ROLES):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This job has already been reworked {rework_count} time(s) "
                        f"(max {MAX_REWORK}). A Store Manager must override to rework it again."
                    ),
                )

        # Pickup record: only meaningful when the transition lands on DELIVERED.
        # Passed as kwargs ONLY when supplied so older repo doubles/mocks with
        # the 4-arg signature keep working untouched.
        pickup_kwargs = {}
        if status == "DELIVERED" and body is not None:
            if body.picked_up_by_name and body.picked_up_by_name.strip():
                pickup_kwargs["picked_up_by_name"] = body.picked_up_by_name.strip()
            if body.picked_up_by_phone and body.picked_up_by_phone.strip():
                pickup_kwargs["picked_up_by_phone"] = body.picked_up_by_phone.strip()

        if repo.update_status(
            job_id, status, current_user.get("user_id"), notes, **pickup_kwargs
        ):
            if is_rework:
                # Count this rework so the cap is enforced on the next attempt.
                try:
                    repo.update(job_id, {"rework_count": rework_count + 1})
                except Exception:  # noqa: BLE001 -- counting is best-effort
                    pass
            return {
                "job_id": job_id,
                "status": status,
                "message": f"Job status updated to {status}",
            }

        raise HTTPException(status_code=500, detail="Failed to update job status")

    return {
        "job_id": job_id,
        "status": status or "",
        "message": f"Job status updated to {status}",
    }


@router.post("/jobs/{job_id}/assign")
async def assign_job(
    job_id: str,
    technician_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Assign job to a technician"""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") not in ["PENDING", "IN_PROGRESS"]:
            raise HTTPException(
                status_code=400, detail="Job cannot be assigned in current state"
            )

        # Validate technician exists and has WORKSHOP_STAFF role
        from ...dependencies import get_user_repository

        user_repo = get_user_repository()
        if user_repo:
            tech_user = user_repo.find_by_id(technician_id)
            if tech_user is None:
                raise HTTPException(
                    status_code=404, detail=f"Technician {technician_id} not found"
                )
            tech_roles = tech_user.get("roles", [])
            if not any(
                r in tech_roles
                for r in ["WORKSHOP_STAFF", "STORE_MANAGER", "ADMIN", "SUPERADMIN"]
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"User {tech_user.get('full_name', technician_id)} is not a workshop technician",
                )

        if repo.assign_technician(job_id, technician_id):
            return {
                "job_id": job_id,
                "technician_id": technician_id,
                "message": "Job assigned",
            }

        raise HTTPException(status_code=500, detail="Failed to assign job")

    return {"message": "Job assigned"}


@router.post("/jobs/{job_id}/start")
async def start_job(job_id: str, current_user: dict = Depends(require_roles(*WORKSHOP_ROLES))):
    """Start working on a job. Requires sales confirmation (fitting_details.confirmed_by_sales=True)."""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") != "PENDING":
            raise HTTPException(status_code=400, detail="Job must be PENDING to start")

        # BUG-116c: gate job acceptance on sales confirmation
        fitting_details = job.get("fitting_details") or {}
        if not fitting_details.get("confirmed_by_sales"):
            raise HTTPException(
                status_code=400,
                detail="Cannot start job: sales must confirm fitting details (confirmed_by_sales=True) first",
            )

        if repo.update_status(job_id, "IN_PROGRESS", current_user.get("user_id")):
            return {"job_id": job_id, "status": "IN_PROGRESS", "message": "Job started"}

        raise HTTPException(status_code=500, detail="Failed to start job")

    return {"message": "Job started"}


@router.post("/jobs/{job_id}/complete")
async def complete_job(job_id: str, current_user: dict = Depends(require_roles(*WORKSHOP_ROLES))):
    """Mark job as completed (pending QC). Requires sales confirmation (fitting_details.confirmed_by_sales=True)."""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") != "IN_PROGRESS":
            raise HTTPException(
                status_code=400, detail="Job must be IN_PROGRESS to complete"
            )

        # BUG-116c: defensive check (should have been gated at /start, but verify here too)
        fitting_details = job.get("fitting_details") or {}
        if not fitting_details.get("confirmed_by_sales"):
            raise HTTPException(
                status_code=400,
                detail="Cannot complete job: sales must confirm fitting details (confirmed_by_sales=True) first",
            )

        if repo.update_status(job_id, "COMPLETED", current_user.get("user_id")):
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "message": "Job completed, pending QC",
            }

        raise HTTPException(status_code=500, detail="Failed to complete job")

    return {"message": "Job completed, pending QC"}
