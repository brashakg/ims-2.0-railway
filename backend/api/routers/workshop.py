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

from .auth import get_current_user
from ..dependencies import get_workshop_repository, get_order_repository

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


class WorkshopJobCreate(BaseModel):
    order_id: str
    frame_details: dict
    lens_details: dict
    prescription_id: str
    fitting_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    expected_date: date


class WorkshopJobUpdate(BaseModel):
    fitting_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    expected_date: Optional[date] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_job_number(repo=None) -> str:
    """Generate unique workshop job number with collision retry."""
    for _ in range(5):
        candidate = f"WS-{datetime.now().strftime('%y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
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

@router.get("/")
async def get_workshop_root():
    """Root endpoint for workshop job list"""
    return {"module": "workshop", "status": "active", "message": "workshop jobs endpoint ready"}


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
            ca_str = completed_at if isinstance(completed_at, str) else completed_at.isoformat()
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
                    ca_dt = ca if isinstance(ca, datetime) else datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
                    cr_dt = cr if isinstance(cr, datetime) else datetime.fromisoformat(str(cr).replace("Z", "+00:00"))
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
        "pending": pending,                   # PENDING + IN_PROGRESS
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
                       f"Allowed: {', '.join(sorted(allowed)) if allowed else 'none (terminal state)'}."
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
                raise HTTPException(status_code=404, detail=f"Technician {technician_id} not found")
            tech_roles = tech_user.get("roles", [])
            if not any(r in tech_roles for r in ["WORKSHOP_STAFF", "STORE_MANAGER", "ADMIN", "SUPERADMIN"]):
                raise HTTPException(
                    status_code=400,
                    detail=f"User {tech_user.get('full_name', technician_id)} is not a workshop technician"
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
                status_code=400,
                detail="Only QC_FAILED jobs can be sent for rework"
            )

        rework_count = job.get("rework_count", 0) + 1
        repo.update(job_id, {"rework_count": rework_count})

        if repo.update_status(job_id, "IN_PROGRESS", current_user.get("user_id"), notes or f"Rework #{rework_count}"):
            return {
                "job_id": job_id,
                "status": "IN_PROGRESS",
                "rework_count": rework_count,
                "message": f"Job sent for rework (attempt #{rework_count})",
            }

        raise HTTPException(status_code=500, detail="Failed to send job for rework")

    return {"message": "Job sent for rework"}
