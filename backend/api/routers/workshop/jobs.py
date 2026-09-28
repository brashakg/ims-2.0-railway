"""Job records: list (and the per-vendor queue), create (idempotent per order),
fitting details, read and edit.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query, Response
from typing import Optional
from datetime import datetime
import uuid
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_db,
    get_workshop_repository,
    get_order_repository,
    get_audit_repository,
    validate_store_access,
    can_access_store_scoped,
)
from ._shared import _FITTING_ROLES, _check_dc_hardlock, logger, router
from .models import FittingDetailsUpdate, WorkshopJobCreate, WorkshopJobUpdate
from .helpers import (
    _assert_job_store_access,
    _stamp_job_actor_names,
    _verify_job_prescription,
    generate_job_number,
    job_to_frontend,
)


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
    _stamp_job_actor_names(jobs)

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
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

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
        _stamp_job_actor_names(jobs)
        jobs_formatted = [job_to_frontend(j) for j in jobs]
        return {"jobs": jobs_formatted, "total": len(jobs_formatted)}

    return {"jobs": [], "total": 0}


@router.post("/jobs", status_code=201)
async def create_job(
    job: WorkshopJobCreate,
    response: Response,
    current_user: dict = Depends(get_current_user),
):
    """Create a new workshop job -- IDEMPOTENT per order.

    An order gets exactly ONE lab job. Returns 201 with the new job, or 200 with
    the EXISTING job when one already covers this order (see the dedup below).
    """
    repo = get_workshop_repository()
    order_repo = get_order_repository()

    if repo is not None:
        # Verify order exists
        order = None
        if order_repo is not None:
            order = order_repo.find_by_id(job.order_id)
            if order is None:
                raise HTTPException(status_code=404, detail="Order not found")

        # ------------------------------------------------------------------
        # DEDUP: one order, one lab job. MUST come before every other check.
        # ------------------------------------------------------------------
        # This door had no dedup at all, while the confirm/payment safety net
        # (orders._ensure_workshop_job_for_order) does. On the DEFAULT POS
        # prescription-sale path those two race deterministically, not rarely:
        # the client awaits addPayment, the payment flips DRAFT -> CONFIRMED and
        # the safety net creates job #1 (its find_by_order is empty because the
        # client has not called yet) and stamps order.workshop_job_id; the client
        # then calls THIS endpoint, which happily created job #2. Every such sale
        # produced two PENDING lab jobs, and the fitting-details modal wrote the
        # Rx onto job #2 while the order pointed at job #1 -- a duplicate lens
        # grind and a duplicate external-lab order, in real rupees.
        #
        # We mirror the safety net's lookup exactly (find_by_order + reverse
        # pointer backfill) so the two cannot drift, and we deliberately return
        # THE JOB THE ORDER POINTS AT when the pointer is set -- that is the job
        # the client writes fitting details onto, so the Rx has to land there.
        #
        # ORDERING NOTE (deliberate): this runs BEFORE _verify_job_prescription
        # and before the F9 DC hardlock. A second call for an order that already
        # has a job is a duplicate REQUEST, not a new clinical decision -- if it
        # arrived with a blank or mismatched prescription_id it must harmlessly
        # return the existing job, never 422. Verification still guards the call
        # that actually creates the job.
        existing_jobs = []
        try:
            existing_jobs = list(repo.find_by_order(job.order_id) or [])
        except Exception as _dedup_exc:  # noqa: BLE001
            # A lookup failure must not block a real create; worst case we fall
            # through to the pre-existing behaviour.
            logger.warning("[WORKSHOP] create dedup lookup failed: %s", _dedup_exc)
            existing_jobs = []
        if existing_jobs:
            # WHICH job to return when more than one already exists. This is not
            # hypothetical: a read-only prod query found 1 of 4 live jobs is
            # already a duplicate pair, created before this dedup existed. The
            # rule is deliberate, because returning an arbitrary one would hand
            # the client a job the ORDER does not reference -- the exact harm
            # being fixed:
            #   1. the job order.workshop_job_id points at, when that pointer is
            #      set AND still resolves to one of this order's jobs; else
            #   2. the OLDEST job (find_by_order does not sort, so sort here --
            #      Mongo natural order is not a guarantee).
            pointed_id = order.get("workshop_job_id") if isinstance(order, dict) else None
            oldest = sorted(
                existing_jobs, key=lambda j: str(j.get("created_at") or "")
            )[0]
            chosen = next(
                (j for j in existing_jobs if j.get("job_id") == pointed_id),
                oldest,
            )
            # Backfill the reverse pointer when the order has none, so the order
            # and the client agree on which job carries the Rx.
            if order_repo is not None and isinstance(order, dict) and not pointed_id:
                try:
                    order_repo.update(
                        job.order_id,
                        {
                            "workshop_job_id": chosen.get("job_id"),
                            "workshop_job_number": chosen.get("job_number"),
                        },
                    )
                except Exception:  # noqa: BLE001 -- best effort
                    pass
            response.status_code = 200
            return {
                "job_id": chosen.get("job_id"),
                "job_number": chosen.get("job_number"),
                "dc_hardlock_override": False,
                "existing": True,
                "message": "Workshop job already exists for this order",
            }

        # PATIENT SAFETY: the prescription_id stored on the job is what the bench
        # grinds -- verify it EXISTS, belongs to THIS order's customer, and is not
        # expired (Store-Manager+ override) before the job is opened. Reuses the
        # canonical POS Rx gate's rules; see _verify_job_prescription.
        _verify_job_prescription(job.prescription_id, order, current_user)

        store_id = current_user.get("active_store_id")
        created_at = datetime.now().isoformat()

        # F9 -- LENS DC HARDLOCK. An external-lab lens (lens_status=ORDERED) may
        # not be worked until an accepted Delivery Challan covering its SKU
        # exists at this store. Raises 422 (code DC_HARDLOCK) when blocked; an
        # ADMIN+ can bypass with override_reason (audited below). In-house lenses
        # and a disabled flag pass straight through.
        # At create the job's lens_status is usually unset (the lens is ORDERED
        # later via the lens lifecycle) -> exempt here; the REAL gate is the
        # -> IN_PROGRESS transition below. We still pass any lens_status carried on
        # the create payload so an already-ORDERED create is gated too.
        hardlock = _check_dc_hardlock(
            get_db(),
            (job.lens_details or {}).get("lens_status"),
            (job.lens_details or {}).get("product_id"),
            store_id,
            created_at,
            current_user,
            job.override_reason,
        )

        job_data = {
            "job_number": generate_job_number(repo),
            "order_id": job.order_id,
            "store_id": store_id,
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
            "created_at": created_at,
            "created_by": current_user.get("user_id"),
        }

        created = repo.create(job_data)
        if created:
            # F9 -- if the DC hardlock was OVERRIDDEN, write an immutable audit
            # row (the override is a control bypass and MUST be recorded).
            # Fail-soft: an audit failure never fails the job create.
            if hardlock.get("override_applied"):
                try:
                    audit = get_audit_repository()
                    if audit is not None:
                        audit.create(
                            {
                                "action": "dc_hardlock_override",
                                "entity_type": "workshop_job",
                                "entity_id": created["job_id"],
                                "user_id": current_user.get("user_id"),
                                "detail": {
                                    "job_number": created["job_number"],
                                    "order_id": job.order_id,
                                    "store_id": store_id,
                                    "product_id": (job.lens_details or {}).get(
                                        "product_id"
                                    ),
                                    "reason": hardlock.get("reason"),
                                },
                            }
                        )
                except Exception:
                    pass
            # Stamp the reverse pointer on the order so an order can find its
            # workshop job directly (the link was previously one-way: job ->
            # order only). Best-effort: a stamp failure never fails job create.
            if order_repo is not None:
                try:
                    order_repo.update(
                        job.order_id,
                        {
                            "workshop_job_id": created["job_id"],
                            "workshop_job_number": created["job_number"],
                        },
                    )
                except Exception:
                    pass
            return {
                "job_id": created["job_id"],
                "job_number": created["job_number"],
                "dc_hardlock_override": bool(hardlock.get("override_applied")),
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
    current_user: dict = Depends(require_roles(*_FITTING_ROLES)),
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
    _assert_job_store_access(job, current_user)

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
            # NEW-IDOR-by-id: a workshop job carries customer + medical Rx data;
            # existence-hide one whose store the caller can't access (cross-store
            # PII leak). Admins / area-managers pass.
            if not can_access_store_scoped(job.get("store_id"), current_user):
                raise HTTPException(status_code=404, detail="Workshop job not found")
            _stamp_job_actor_names([job])
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
        # NEW-IDOR-by-id: don't let a store-scoped caller mutate another store's job.
        if not can_access_store_scoped(existing.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Workshop job not found")

        # Bug fix: READY and QC_FAILED were missing from the immutable-status
        # guard. A job in READY or CANCELLED state must not have its details
        # changed out from under QC/delivery. COMPLETED is intentionally
        # included so QC rework can't silently alter specs mid-check.
        if existing.get("status") in ["COMPLETED", "READY", "DELIVERED", "CANCELLED"]:
            raise HTTPException(
                status_code=400,
                detail="Cannot update completed, ready, delivered, or cancelled jobs",
            )

        update_data = job.model_dump(exclude_unset=True)
        if "expected_date" in update_data and update_data["expected_date"]:
            update_data["expected_date"] = update_data["expected_date"].isoformat()
        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(job_id, update_data):
            return {"job_id": job_id, "message": "Workshop job updated"}

        raise HTTPException(status_code=500, detail="Failed to update workshop job")

    return {"job_id": job_id, "message": "Workshop job updated"}
