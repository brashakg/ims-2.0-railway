"""
IMS 2.0 - Workshop Router
==========================
Workshop job management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime
import uuid
import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_db,
    get_workshop_repository,
    get_order_repository,
    get_audit_repository,
    get_vendor_repository,
)

# Roles allowed to drive the lens lifecycle + ready-notify. SUPERADMIN passes
# automatically via require_roles, so it is intentionally not listed.
WORKSHOP_ROLES = (
    "WORKSHOP_STAFF",
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
)

# Forward-only lens-order lifecycle for a workshop job. The lens is ordered
# from the lab, received into the store, then mounted into the frame. Each
# transition stamps a timestamp field (see LENS_STATUS_TIMESTAMP_FIELD).
LENS_STATUS_ORDER = ["NOT_ORDERED", "ORDERED", "RECEIVED", "MOUNTED"]
LENS_STATUS_TIMESTAMP_FIELD = {
    "ORDERED": "lens_ordered_at",
    "RECEIVED": "lens_received_at",
    "MOUNTED": "lens_mounted_at",
}


def _next_lens_status_ok(current, target) -> bool:
    """Pure transition guard for the lens lifecycle. No DB access.

    Returns True only when `target` is the IMMEDIATE next step after
    `current` along NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED. Skips
    (e.g. NOT_ORDERED -> RECEIVED), backwards moves, no-ops, and any value
    not in LENS_STATUS_ORDER all return False.

    A missing / empty / unknown current status is treated as NOT_ORDERED so a
    legacy job with no lens_status set can still be advanced to ORDERED.
    """
    cur = current if current in LENS_STATUS_ORDER else "NOT_ORDERED"
    if target not in LENS_STATUS_ORDER:
        return False
    try:
        return LENS_STATUS_ORDER.index(target) == LENS_STATUS_ORDER.index(cur) + 1
    except ValueError:
        return False


# Vendor-side status taxonomy used by the admin endpoints below. Mirrors
# vendor_portal.PORTAL_STATUSES — kept duplicated to avoid a cross-router
# import cycle (vendor_portal imports from workshop's dependencies; if
# workshop imported back we'd have a circle at module-load time).
ADMIN_VENDOR_STATUSES = {
    "RECEIVED",
    "IN_PRODUCTION",
    "DISPATCHED",
    "DELIVERED",
    "ON_HOLD",
    "CANCELLED",
}

# Valid workshop job state transitions
VALID_JOB_TRANSITIONS = {
    "PENDING": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
    "COMPLETED": {"READY", "QC_FAILED"},  # QC pass → READY, QC fail → QC_FAILED
    "QC_FAILED": {"IN_PROGRESS", "CANCELLED"},  # rework sends back to IN_PROGRESS
    "READY": {"DELIVERED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class FittingDetails(BaseModel):
    """Phase 6.8 — physical measurements the sales staff hands over to
    the workshop technician for lens cutting / fitting. All fields are
    optional individually, but `confirmed_by_sales` must be True for
    the workshop to accept the job (sales explicitly confirming that
    power + product details are correct).
    """

    dia: Optional[str] = None  # Lens diameter (e.g. "65", "70")
    fh: Optional[str] = None  # Fitting height (for progressive/bifocal)
    b_size: Optional[str] = None  # Lens vertical measurement
    dbl: Optional[str] = None  # Distance between lenses (bridge width)
    tint: Optional[str] = None  # Tint colour / percentage
    base_curve: Optional[str] = None  # Base curve (e.g. "6", "8")
    coating: Optional[str] = (
        None  # Coating name (redundant with lens_details.coating but captured here for sales confirmation)
    )
    other: Optional[str] = None  # Free-text notes
    order_date: Optional[str] = None  # ISO date (auto-filled on save)
    order_time: Optional[str] = None  # HH:MM (auto-filled on save)
    ordered_by: Optional[str] = None  # User id of sales staff
    ordered_by_name: Optional[str] = None
    expected_lens_receive_date: Optional[date] = None
    # Phase 6.8 — vendor (lens supplier) PO reference. Sales enters the ID
    # issued when the lens was ordered from Zeiss / Essilor / etc; workshop
    # + finance use it to reconcile incoming lens stock.
    vendor_order_id: Optional[str] = None
    confirmed_by_sales: bool = False  # Must be True to submit
    confirmed_at: Optional[str] = None  # ISO timestamp


class WorkshopJobCreate(BaseModel):
    order_id: str
    frame_details: dict
    lens_details: dict
    prescription_id: str
    fitting_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    expected_date: date
    # Phase 6.8 — optional at create time; sales fills via a modal
    # right after order confirmation (PATCH /jobs/{id}/fitting-details)
    fitting_details: Optional[FittingDetails] = None


class WorkshopJobUpdate(BaseModel):
    fitting_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    expected_date: Optional[date] = None


class FittingDetailsUpdate(BaseModel):
    """Payload for PATCH /workshop/jobs/{id}/fitting-details."""

    fitting_details: FittingDetails


class WorkshopVendorPatch(BaseModel):
    """Payload for PATCH /workshop/jobs/{id}/vendor — admin assigns / edits
    the lens lab handling this job. All fields are individually optional;
    we only update what's supplied (so a partial form save doesn't blow
    away tracking_url, etc.)."""

    vendor_id: Optional[str] = None
    vendor_order_id: Optional[str] = None
    vendor_tracking_url: Optional[str] = None
    vendor_dispatch_date: Optional[str] = None  # ISO date
    vendor_received_date: Optional[str] = None


class WorkshopVendorStatusBody(BaseModel):
    """Payload for POST /workshop/jobs/{id}/vendor-status — admin (IMS user)
    logging a vendor-status update on behalf of the lab (e.g. lab phoned
    them). Source on the resulting history row is `ims_user`."""

    status: str
    note: Optional[str] = None


class LensStatusBody(BaseModel):
    """Payload for POST /workshop/jobs/{id}/lens-status — advance the lens
    lifecycle by exactly one forward step (validated by _next_lens_status_ok)."""

    status: str


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_job_number(repo=None) -> str:
    """Generate unique workshop job number with collision retry."""
    for _ in range(5):
        candidate = (
            f"WS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        )
        if repo is not None:
            try:
                existing = repo.collection.find_one({"job_number": candidate})
                if existing:
                    continue  # collision — retry
            except Exception:
                pass
        return candidate
    # Fallback: use full UUID to guarantee uniqueness
    return f"WS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:12].upper()}"


def job_to_frontend(job: dict) -> dict:
    """Convert workshop job from snake_case to camelCase for frontend"""
    if job is None:
        return job

    key_map = {
        "job_id": "id",
        "job_number": "jobNumber",
        "order_id": "orderId",
        "order_number": "orderNumber",
        "store_id": "storeId",
        "customer_id": "customerId",
        "customer_name": "customerName",
        "customer_phone": "customerPhone",
        "frame_details": "frameDetails",
        "frame_name": "frameName",
        "frame_barcode": "frameBarcode",
        "lens_details": "lensDetails",
        "lens_type": "lensType",
        "prescription_id": "prescriptionId",
        "fitting_instructions": "fittingInstructions",
        "special_notes": "notes",
        "technician_id": "assignedTo",
        "assigned_to": "assignedTo",
        "expected_date": "expectedDate",
        "promised_date": "promisedDate",
        "created_at": "createdAt",
        "completed_at": "completedAt",
        "updated_at": "updatedAt",
        "updated_by": "updatedBy",
        "created_by": "createdBy",
    }

    result = {}
    for key, value in job.items():
        # Drop MongoDB's BSON ObjectId — same reasoning as
        # orders.order_to_frontend: Pydantic/FastAPI's default JSON
        # encoder can't serialise ObjectId, and workshop_jobs carry
        # their own job_id/job_number so `_id` isn't needed in responses.
        if key == "_id":
            continue
        if key in key_map:
            result[key_map[key]] = value
        else:
            # Keep other fields as-is
            result[key] = value

    return result


# ============================================================================
# ENDPOINTS
# ============================================================================


# NOTE: Specific routes MUST come before /jobs/{job_id}


@router.get("")
@router.get("/")
async def get_workshop_root():
    """Root endpoint for workshop job list"""
    return {
        "module": "workshop",
        "status": "active",
        "message": "workshop jobs endpoint ready",
    }


@router.get("/pending")
async def get_pending_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get pending workshop jobs"""
    repo = get_workshop_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_pending(active_store)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/overdue")
async def get_overdue_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get overdue workshop jobs"""
    repo = get_workshop_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_overdue(active_store)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/ready")
async def get_ready_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get jobs ready for delivery"""
    repo = get_workshop_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        jobs = repo.find_ready(active_store)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.get("/technician-workload")
async def get_technician_workload(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get technician workload summary"""
    repo = get_workshop_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo and active_store:
        workload = repo.get_technician_workload(active_store)
        return {"workload": workload}

    return {"workload": []}


# ---------------------------------------------------------------------------
# Phase 6.4 — single-shot KPIs for the workshop dashboard header.
# The frontend currently computes Active / Urgent / Ready / Overdue on the
# client from the full job list. That works at small scale but means every
# workshop page load pulls every job in the store. This endpoint lets the
# client drop 4 HTTP calls and ~a few hundred KB of JSON in favour of one
# small summary call — and as a bonus exposes `avg_turnaround_days` and
# `completed_today` which the client couldn't cheaply compute before.
# ---------------------------------------------------------------------------


@router.get("/dashboard-kpis")
async def get_dashboard_kpis(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Aggregated workshop KPIs for the dashboard header.

    Returns:
        pending            — PENDING + IN_PROGRESS (i.e. "Active Jobs")
        in_progress        — IN_PROGRESS only
        qc_failed          — jobs sent back for rework
        ready_for_pickup   — READY status
        overdue            — pending/in_progress past expected_date
        completed_today    — COMPLETED or READY with completed_at == today
        delivered_today    — DELIVERED with status_updated_at == today
        avg_turnaround_days — mean (completed_at - created_at) across the
                              last 100 closed jobs. `None` if fewer than 5
                              samples exist (avoids noisy averages).

    Fail-soft: repo absent → returns zeros with null turnaround, never raises.
    """
    repo = get_workshop_repository()
    active_store = store_id or current_user.get("active_store_id")

    empty = {
        "pending": 0,
        "in_progress": 0,
        "qc_failed": 0,
        "ready_for_pickup": 0,
        "overdue": 0,
        "completed_today": 0,
        "delivered_today": 0,
        "avg_turnaround_days": None,
        "store_id": active_store,
        "as_of": datetime.now().isoformat(),
    }

    if repo is None or not active_store:
        return empty

    try:
        # One pass over the store's jobs so we don't hit Mongo five times
        # for what is effectively a group-by-status.
        all_jobs = repo.find_by_store(active_store)
    except Exception:
        return empty

    now = datetime.now()
    today_str = now.date().isoformat()

    pending = 0
    in_progress = 0
    qc_failed = 0
    ready = 0
    overdue = 0
    completed_today = 0
    delivered_today = 0
    turnaround_samples = []

    for job in all_jobs:
        status = job.get("status", "")

        if status == "PENDING":
            pending += 1
        elif status == "IN_PROGRESS":
            in_progress += 1
            pending += 1  # "Active" is PENDING + IN_PROGRESS in the UI
        elif status == "QC_FAILED":
            qc_failed += 1
        elif status == "READY":
            ready += 1

        # Overdue = open-ish job whose expected_date is in the past
        if status in ("PENDING", "IN_PROGRESS"):
            expected = job.get("expected_date")
            if expected:
                try:
                    if isinstance(expected, str):
                        exp_dt = datetime.fromisoformat(expected.replace("Z", "+00:00"))
                    elif isinstance(expected, datetime):
                        exp_dt = expected
                    else:
                        exp_dt = None
                    if exp_dt is not None and exp_dt < now:
                        overdue += 1
                except (ValueError, TypeError):
                    pass

        # Today counts — both by completed_at and by status_updated_at for
        # DELIVERED, so the "what shipped today" number is always live.
        completed_at = job.get("completed_at")
        if completed_at:
            ca_str = (
                completed_at
                if isinstance(completed_at, str)
                else completed_at.isoformat()
            )
            if ca_str.startswith(today_str):
                completed_today += 1

        if status == "DELIVERED":
            sua = job.get("status_updated_at")
            if sua:
                sua_str = sua if isinstance(sua, str) else sua.isoformat()
                if sua_str.startswith(today_str):
                    delivered_today += 1

        # Turnaround sample — only include finished jobs with both ts.
        if status in ("COMPLETED", "READY", "DELIVERED"):
            ca = job.get("completed_at")
            cr = job.get("created_at")
            if ca and cr:
                try:
                    ca_dt = (
                        ca
                        if isinstance(ca, datetime)
                        else datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
                    )
                    cr_dt = (
                        cr
                        if isinstance(cr, datetime)
                        else datetime.fromisoformat(str(cr).replace("Z", "+00:00"))
                    )
                    days = (ca_dt - cr_dt).total_seconds() / 86400.0
                    if days >= 0:
                        turnaround_samples.append(days)
                except (ValueError, TypeError):
                    pass

    # Cap sample size — latest 100 closed jobs are plenty and we've already
    # walked them; take the tail for a rolling view rather than all-time.
    if len(turnaround_samples) >= 5:
        recent = turnaround_samples[-100:]
        avg_turnaround = round(sum(recent) / len(recent), 2)
    else:
        avg_turnaround = None

    return {
        "pending": pending,  # PENDING + IN_PROGRESS
        "in_progress": in_progress,
        "qc_failed": qc_failed,
        "ready_for_pickup": ready,
        "overdue": overdue,
        "completed_today": completed_today,
        "delivered_today": delivered_today,
        "avg_turnaround_days": avg_turnaround,
        "store_id": active_store,
        "as_of": now.isoformat(),
    }


@router.get("/jobs/by-vendor/{vendor_id}")
async def list_jobs_by_vendor(
    vendor_id: str,
    include_delivered: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """Admin view of the same queue an external vendor sees through
    their portal — useful for checking what the lab is supposed to be
    seeing without copy-pasting their token URL.

    Registered BEFORE `/jobs/{job_id}` so the literal path segment
    `by-vendor` doesn't get matched as a job_id (FastAPI matches by
    registration order — first match wins).
    """
    repo = get_workshop_repository()
    if repo is None:
        return {"vendor_id": vendor_id, "jobs": [], "total": 0}

    filter_dict: dict = {"vendor_id": vendor_id}
    if not include_delivered:
        filter_dict["status"] = {"$nin": ["DELIVERED", "CANCELLED"]}

    jobs = (
        repo.find_many(filter_dict, skip=skip, limit=limit, sort=[("expected_date", 1)])
        or []
    )

    return {
        "vendor_id": vendor_id,
        "jobs": [job_to_frontend(j) for j in jobs],
        "total": len(jobs),
    }


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = Query(None),
    technician_id: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List workshop jobs with filters"""
    repo = get_workshop_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        filter_dict = {}
        if active_store:
            filter_dict["store_id"] = active_store
        if status:
            filter_dict["status"] = status
        if technician_id:
            filter_dict["technician_id"] = technician_id

        jobs = repo.find_many(
            filter_dict, skip=skip, limit=limit, sort=[("created_at", -1)]
        )
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.post("/jobs", status_code=201)
async def create_job(
    job: WorkshopJobCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new workshop job"""
    repo = get_workshop_repository()
    order_repo = get_order_repository()

    if repo is not None:
        # Verify order exists
        if order_repo is not None:
            order = order_repo.find_by_id(job.order_id)
            if order is None:
                raise HTTPException(status_code=404, detail="Order not found")

        job_data = {
            "job_number": generate_job_number(repo),
            "order_id": job.order_id,
            "store_id": current_user.get("active_store_id"),
            "frame_details": job.frame_details,
            "lens_details": job.lens_details,
            "prescription_id": job.prescription_id,
            "fitting_instructions": job.fitting_instructions,
            "special_notes": job.special_notes,
            "expected_date": job.expected_date.isoformat(),
            "fitting_details": (
                job.fitting_details.model_dump(mode="json")
                if job.fitting_details
                else None
            ),
            "status": "PENDING",
            "created_by": current_user.get("user_id"),
        }

        created = repo.create(job_data)
        if created:
            return {
                "job_id": created["job_id"],
                "job_number": created["job_number"],
                "message": "Workshop job created",
            }

        raise HTTPException(status_code=500, detail="Failed to create workshop job")

    return {
        "id": str(uuid.uuid4()),
        "jobNumber": generate_job_number(),
        "message": "Workshop job created",
    }


@router.patch("/jobs/{job_id}/fitting-details")
async def update_fitting_details(
    job_id: str,
    payload: FittingDetailsUpdate,
    current_user: dict = Depends(get_current_user),
):
    """
    Phase 6.8 — attach / update the lens-fitting measurements the sales
    staff fill after creating a prescription order. The sales staff
    confirms the power + product details are correct via the
    `confirmed_by_sales` checkbox before the workshop can accept the job.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")

    # Stamp metadata we want server-controlled rather than trusting the client.
    fd = payload.fitting_details.model_dump(mode="json")
    now = datetime.now()
    fd["order_date"] = fd.get("order_date") or now.date().isoformat()
    fd["order_time"] = fd.get("order_time") or now.strftime("%H:%M")
    fd["ordered_by"] = fd.get("ordered_by") or current_user.get("user_id")
    fd["ordered_by_name"] = fd.get("ordered_by_name") or current_user.get("username")
    if fd.get("confirmed_by_sales"):
        fd["confirmed_at"] = fd.get("confirmed_at") or now.isoformat()

    ok = repo.update(job_id, {"fitting_details": fd})
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save fitting details")

    return {"job_id": job_id, "fitting_details": fd, "message": "Fitting details saved"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, current_user: dict = Depends(get_current_user)):
    """Get workshop job by ID"""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is not None:
            return job_to_frontend(job)
        raise HTTPException(status_code=404, detail="Workshop job not found")

    return {"id": job_id}


@router.put("/jobs/{job_id}")
async def update_job(
    job_id: str, job: WorkshopJobUpdate, current_user: dict = Depends(get_current_user)
):
    """Update workshop job details"""
    repo = get_workshop_repository()

    if repo is not None:
        existing = repo.find_by_id(job_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")

        if existing.get("status") in ["COMPLETED", "READY", "DELIVERED"]:
            raise HTTPException(status_code=400, detail="Cannot update completed jobs")

        update_data = job.model_dump(exclude_unset=True)
        if "expected_date" in update_data and update_data["expected_date"]:
            update_data["expected_date"] = update_data["expected_date"].isoformat()
        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(job_id, update_data):
            return {"job_id": job_id, "message": "Workshop job updated"}

        raise HTTPException(status_code=500, detail="Failed to update workshop job")

    return {"job_id": job_id, "message": "Workshop job updated"}


@router.patch("/jobs/{job_id}/status")
async def update_job_status(
    job_id: str,
    status: str = Query(...),
    notes: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Update job status (generic endpoint) with state machine validation."""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")

        current_status = job.get("status", "PENDING")
        allowed = VALID_JOB_TRANSITIONS.get(current_status, set())
        if status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from {current_status} to {status}. "
                f"Allowed: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}.",
            )

        if repo.update_status(job_id, status, current_user.get("user_id"), notes):
            return {
                "job_id": job_id,
                "status": status,
                "message": f"Job status updated to {status}",
            }

        raise HTTPException(status_code=500, detail="Failed to update job status")

    return {
        "job_id": job_id,
        "status": status,
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

        if job.get("status") not in ["PENDING", "IN_PROGRESS"]:
            raise HTTPException(
                status_code=400, detail="Job cannot be assigned in current state"
            )

        # Validate technician exists and has WORKSHOP_STAFF role
        from ..dependencies import get_user_repository

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
async def start_job(job_id: str, current_user: dict = Depends(get_current_user)):
    """Start working on a job"""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")

        if job.get("status") != "PENDING":
            raise HTTPException(status_code=400, detail="Job must be PENDING to start")

        if repo.update_status(job_id, "IN_PROGRESS", current_user.get("user_id")):
            return {"job_id": job_id, "status": "IN_PROGRESS", "message": "Job started"}

        raise HTTPException(status_code=500, detail="Failed to start job")

    return {"message": "Job started"}


@router.post("/jobs/{job_id}/complete")
async def complete_job(job_id: str, current_user: dict = Depends(get_current_user)):
    """Mark job as completed (pending QC)"""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")

        if job.get("status") != "IN_PROGRESS":
            raise HTTPException(
                status_code=400, detail="Job must be IN_PROGRESS to complete"
            )

        if repo.update_status(job_id, "COMPLETED", current_user.get("user_id")):
            return {
                "job_id": job_id,
                "status": "COMPLETED",
                "message": "Job completed, pending QC",
            }

        raise HTTPException(status_code=500, detail="Failed to complete job")

    return {"message": "Job completed, pending QC"}


@router.post("/jobs/{job_id}/qc")
async def qc_job(
    job_id: str,
    passed: bool = Query(...),
    notes: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Perform QC on completed job"""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")

        if job.get("status") not in ["COMPLETED", "QC_FAILED"]:
            raise HTTPException(
                status_code=400, detail="Job must be COMPLETED or QC_FAILED for QC"
            )

        if repo.add_qc_result(job_id, passed, notes or "", current_user.get("user_id")):
            status = "READY" if passed else "QC_FAILED"
            return {
                "job_id": job_id,
                "status": status,
                "qc_passed": passed,
                "message": "QC recorded",
            }

        raise HTTPException(status_code=500, detail="Failed to record QC")

    return {"message": "QC recorded", "status": "READY" if passed else "QC_FAILED"}


@router.post("/jobs/{job_id}/rework")
async def rework_job(
    job_id: str,
    notes: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Send QC-failed job back for rework (QC_FAILED → IN_PROGRESS)."""
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")

        if job.get("status") != "QC_FAILED":
            raise HTTPException(
                status_code=400, detail="Only QC_FAILED jobs can be sent for rework"
            )

        rework_count = job.get("rework_count", 0) + 1
        repo.update(job_id, {"rework_count": rework_count})

        if repo.update_status(
            job_id,
            "IN_PROGRESS",
            current_user.get("user_id"),
            notes or f"Rework #{rework_count}",
        ):
            return {
                "job_id": job_id,
                "status": "IN_PROGRESS",
                "rework_count": rework_count,
                "message": f"Job sent for rework (attempt #{rework_count})",
            }

        raise HTTPException(status_code=500, detail="Failed to send job for rework")

    return {"message": "Job sent for rework"}


# ============================================================================
# LENS-ORDER LIFECYCLE + READY-NOTIFY
# ============================================================================
# A workshop job's physical lens moves NOT_ORDERED -> ORDERED -> RECEIVED ->
# MOUNTED. This is independent of the job's overall workflow status (PENDING /
# IN_PROGRESS / READY / ...) and tracks where the actual lens is. When the job
# is finished we ping the customer that it's ready for pickup.


@router.post("/jobs/{job_id}/lens-status")
async def update_lens_status(
    job_id: str,
    payload: LensStatusBody,
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Advance a job's lens lifecycle by ONE forward step.

    Forward-only along NOT_ORDERED -> ORDERED -> RECEIVED -> MOUNTED. Skips,
    backwards moves, and no-ops are rejected with 400. The matching timestamp
    field (lens_ordered_at / lens_received_at / lens_mounted_at) is stamped.
    Fail-soft: repo absent -> 503, never an unhandled 500.
    """
    target = (payload.status or "").strip().upper()
    if target not in LENS_STATUS_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown lens status {target!r}. Allowed: {', '.join(LENS_STATUS_ORDER)}.",
        )

    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")

    current = job.get("lens_status") or "NOT_ORDERED"
    if not _next_lens_status_ok(current, target):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot move lens status from {current} to {target}. "
                f"Lens lifecycle is forward-only: {' -> '.join(LENS_STATUS_ORDER)}."
            ),
        )

    now = datetime.now()
    update = {
        "lens_status": target,
        "lens_status_updated_by": current_user.get("user_id"),
    }
    ts_field = LENS_STATUS_TIMESTAMP_FIELD.get(target)
    if ts_field:
        update[ts_field] = now.isoformat()

    if not repo.update(job_id, update):
        raise HTTPException(status_code=500, detail="Failed to update lens status")

    # Audit (fail-soft)
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.lens_status",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {"from": current, "to": target},
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] lens_status audit failed: %s", e)

    # Branch B' sub-PR 4 -- on lens MOUNTED, hard-commit the reserved
    # lens-catalog cell (the unit physically left the tray and is in
    # the customer's frame). Fail-soft: a missing reservation, mongo
    # blip, or 409 here is logged but never blocks the lens-status
    # transition (the workshop has already cut the lens).
    if target == "MOUNTED":
        try:
            order_id = job.get("order_id")
            if order_id and get_order_repository is not None:
                order_repo = get_order_repository()
                if order_repo is not None:
                    order = order_repo.find_by_id(order_id)
                    if order:
                        from ..services.lens_stock_hook import (
                            commit_for_workshop_dispatch,
                        )

                        items_for_commit = order.get("items") or []
                        for idx, oi in enumerate(items_for_commit):
                            try:
                                await commit_for_workshop_dispatch(
                                    order_item=oi,
                                    order_id=order_id,
                                    line_index=idx,
                                    store_id=(
                                        order.get("store_id")
                                        or job.get("store_id")
                                        or ""
                                    ),
                                    user=current_user,
                                )
                            except Exception as cm_exc:  # noqa: BLE001
                                logger.warning(
                                    "[LENS_HOOK] commit on MOUNTED failed "
                                    "(order %s line %s): %s",
                                    order_id,
                                    idx,
                                    cm_exc,
                                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LENS_HOOK] MOUNTED commit outer error job=%s: %s",
                job_id,
                exc,
            )

    return {
        "job_id": job_id,
        "lens_status": target,
        **({ts_field: update[ts_field]} if ts_field else {}),
        "message": f"Lens status updated to {target}",
    }


def _ready_whatsapp_text(job: dict) -> str:
    """Plain-text 'ready for pickup' WhatsApp body. Pure (no IO)."""
    name = job.get("customer_name") or "Customer"
    job_no = job.get("job_number") or job.get("job_id") or ""
    tail = f" (Job {job_no})" if job_no else ""
    return (
        f"Hi {name}, your eyewear order{tail} is ready for pickup at our store. "
        f"Please visit us at your convenience. - Better Vision"
    )


@router.post("/jobs/{job_id}/notify-ready")
async def notify_ready(
    job_id: str,
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Notify the customer that their job is ready for pickup.

    Sends a WhatsApp via the existing MSG91 provider (DISPATCH_MODE-gated +
    fail-soft), stamps `ready_notified_at` on the job, and inserts a row into
    the `notifications` collection when available. Never raises on a provider
    or DB hiccup: the WhatsApp result is reported back in the response so the
    UI can surface SENT / SIMULATED / FAILED.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")

    phone = job.get("customer_phone") or job.get("customerPhone")
    now = datetime.now()

    # 1. WhatsApp (provider is DISPATCH_MODE-gated + fail-soft internally).
    wa_status = "no_phone"
    if phone:
        try:
            from agents.providers import send_whatsapp  # lazy import

            res = await send_whatsapp(phone, _ready_whatsapp_text(job))
            wa_status = getattr(res, "status", "SENT")
        except Exception as e:  # noqa: BLE001
            logger.warning("[WORKSHOP] notify-ready whatsapp failed: %s", e)
            wa_status = "FAILED"

    # 2. Stamp the job (fail-soft).
    try:
        repo.update(
            job_id,
            {
                "ready_notified_at": now.isoformat(),
                "ready_notified_by": current_user.get("user_id"),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] notify-ready stamp failed: %s", e)

    # 3. In-app notification row (fail-soft; only if the collection exists).
    notif_written = False
    try:
        db = get_db()
        if db is not None and getattr(db, "is_connected", True):
            coll = db.get_collection("notifications")
            if coll is not None:
                coll.insert_one(
                    {
                        "notification_id": f"NTF-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
                        "notification_type": "workshop_ready",
                        "user_id": current_user.get("user_id"),
                        "title": "Pickup notification sent",
                        "message": (
                            f"Customer notified that job "
                            f"{job.get('job_number') or job_id} is ready for pickup."
                        ),
                        "entity_type": "workshop_job",
                        "entity_id": job_id,
                        "action_url": "/workshop",
                        "channels": ["WHATSAPP", "IN_APP"],
                        "priority": "NORMAL",
                        "status": "SENT",
                        "created_at": now,
                    }
                )
                notif_written = True
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] notify-ready notification insert failed: %s", e)

    return {
        "job_id": job_id,
        "ready_notified_at": now.isoformat(),
        "whatsapp_status": wa_status,
        "notification_logged": notif_written,
        "message": "Pickup notification processed",
    }


# ============================================================================
# VENDOR / LENS-LAB ADMIN ENDPOINTS
# ============================================================================
# These three endpoints are the IMS-side complement to the public token-auth
# vendor portal (backend/api/routers/vendor_portal.py). Admin users assign a
# lab to a job, log status updates the lab phoned in, and pull a per-vendor
# queue view.


@router.patch("/jobs/{job_id}/vendor")
async def patch_job_vendor(
    job_id: str,
    payload: WorkshopVendorPatch,
    current_user: dict = Depends(get_current_user),
):
    """Admin assigns / updates the lens lab handling a workshop job.

    Setting `vendor_id` for the first time is the trigger that makes a
    job visible on the corresponding vendor portal token's `/jobs` feed.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")

    # Only admin / store-manager / workshop-staff can assign vendors. Sales
    # staff can see jobs but shouldn't be touching vendor IDs.
    user_roles = current_user.get("roles", [])
    if not any(
        r in user_roles
        for r in [
            "SUPERADMIN",
            "ADMIN",
            "AREA_MANAGER",
            "STORE_MANAGER",
            "WORKSHOP_STAFF",
        ]
    ):
        raise HTTPException(
            status_code=403, detail="Not authorized to manage vendor assignment"
        )

    update = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not update:
        return {"job_id": job_id, "message": "No changes"}

    # Validate vendor exists if a new vendor_id is supplied
    if "vendor_id" in update:
        vendor_repo = get_vendor_repository()
        if vendor_repo is not None:
            vendor = vendor_repo.find_by_id(update["vendor_id"])
            if vendor is None:
                raise HTTPException(status_code=404, detail="Vendor not found")
            # Cache the vendor's display name on the job for fast list rendering
            update["vendor_name"] = vendor.get("trade_name") or vendor.get("legal_name")

    update["vendor_updated_by"] = current_user.get("user_id")
    update["vendor_updated_at"] = datetime.now()

    if not repo.update(job_id, update):
        raise HTTPException(status_code=500, detail="Failed to update vendor fields")

    # Audit
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.vendor_assign",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": update,
                }
            )
    except Exception as e:
        logger.warning(f"workshop vendor_assign audit failed: {e}")

    return {"job_id": job_id, "message": "Vendor fields updated", **update}


@router.post("/jobs/{job_id}/vendor-status")
async def post_admin_vendor_status(
    job_id: str,
    payload: WorkshopVendorStatusBody,
    current_user: dict = Depends(get_current_user),
):
    """IMS user logs a vendor status update (e.g. "lab called, says
    DISPATCHED today"). Logged with source='ims_user' so the audit trail
    can distinguish phoned-in updates from the lab's own portal posts.
    """
    if payload.status not in ADMIN_VENDOR_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown vendor status. Allowed: {', '.join(sorted(ADMIN_VENDOR_STATUSES))}",
        )

    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    if not job.get("vendor_id"):
        raise HTTPException(
            status_code=400,
            detail="Job has no vendor assigned. PATCH /vendor first.",
        )

    now = datetime.now()
    history_entry = {
        "status": payload.status,
        "note": payload.note,
        "source": "ims_user",
        "logged_by": current_user.get("user_id"),
        "logged_at": now.isoformat(),
    }
    history = list(job.get("vendor_status_history") or [])
    history.append(history_entry)

    update = {
        "vendor_status": payload.status,
        "vendor_status_history": history,
        "vendor_status_updated_at": now,
    }
    if payload.status == "DISPATCHED" and not job.get("vendor_dispatch_date"):
        update["vendor_dispatch_date"] = now.isoformat()
    if payload.status == "DELIVERED" and not job.get("vendor_received_date"):
        update["vendor_received_date"] = now.isoformat()

    repo.update(job_id, update)

    # Audit
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.vendor_status",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "vendor_id": job.get("vendor_id"),
                        "status": payload.status,
                        "source": "ims_user",
                        "note": payload.note,
                    },
                }
            )
    except Exception as e:
        logger.warning(f"workshop vendor_status (ims) audit failed: {e}")

    return {
        "job_id": job_id,
        "vendor_status": payload.status,
        "logged_at": history_entry["logged_at"],
        "source": "ims_user",
    }


# NOTE: `/jobs/by-vendor/{vendor_id}` is registered up near the other
# specific `/jobs/...` routes so it doesn't get shadowed by the catch-all
# `/jobs/{job_id}` (FastAPI matches by registration order).
