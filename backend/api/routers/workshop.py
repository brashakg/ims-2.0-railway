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

from .auth import get_current_user
from ..dependencies import get_workshop_repository, get_order_repository

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


def generate_job_number() -> str:
    """Generate unique workshop job number"""
    return f"WS-{datetime.now().strftime('%y%m%d')}-{str(uuid.uuid4())[:6].upper()}"


def job_to_frontend(job: dict) -> dict:
    """Convert workshop job from snake_case to camelCase for frontend"""
    if not job:
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
@router.get("/pending")
async def get_pending_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get pending workshop jobs"""
    repo = get_workshop_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo:
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

    if repo:
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

    if repo:
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

    if repo:
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

    if repo:
        # Verify order exists
        if order_repo:
            order = order_repo.find_by_id(job.order_id)
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

        job_data = {
            "job_number": generate_job_number(),
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

    if repo:
        job = repo.find_by_id(job_id)
        if job:
            return job_to_frontend(job)
        raise HTTPException(status_code=404, detail="Workshop job not found")

    return {"id": job_id}


@router.put("/jobs/{job_id}")
async def update_job(
    job_id: str, job: WorkshopJobUpdate, current_user: dict = Depends(get_current_user)
):
    """Update workshop job details"""
    repo = get_workshop_repository()

    if repo:
        existing = repo.find_by_id(job_id)
        if not existing:
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
    """Update job status (generic endpoint)"""
    repo = get_workshop_repository()

    if repo:
        job = repo.find_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Workshop job not found")

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

    if repo:
        job = repo.find_by_id(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Workshop job not found")

        if job.get("status") not in ["PENDING", "IN_PROGRESS"]:
            raise HTTPException(
                status_code=400, detail="Job cannot be assigned in current state"
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

    if repo:
        job = repo.find_by_id(job_id)
        if not job:
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

    if repo:
        job = repo.find_by_id(job_id)
        if not job:
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

    if repo:
        job = repo.find_by_id(job_id)
        if not job:
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
