"""Vendor / lens-lab admin endpoints: assign a lab to a job and log a
vendor-status update.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends
from datetime import datetime
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_workshop_repository,
    get_audit_repository,
    get_vendor_repository,
)
from ._shared import ADMIN_VENDOR_STATUSES, WORKSHOP_ROLES, logger, router
from .models import WorkshopVendorPatch, WorkshopVendorStatusBody
from .helpers import _assert_job_store_access


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
    _assert_job_store_access(job, current_user)

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
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
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
    _assert_job_store_access(job, current_user)
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
