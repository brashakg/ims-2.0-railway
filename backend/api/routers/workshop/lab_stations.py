"""F2 internal lab routing: station registry, station queue, the bench scan
and the job-card print stamp.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query
from typing import Optional
from datetime import datetime
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_db,
    get_workshop_repository,
    get_audit_repository,
    validate_store_access,
    can_access_store_scoped,
)
from ._shared import logger, router
from .models import LabScanBody, LabStationUpsert
from .lens import _perform_ready_notify


# ============================================================================
# F2 -- INTERNAL LAB ROUTING (disposable barcoded job cards)
# ============================================================================
# A workshop order travels through in-house benches (INTAKE -> EDGING ->
# COATING -> QC_LAB -> DISPATCH -> PICKUP). A disposable Code128 job card rides
# with the job; each bench scans it; the forward-only gate advances
# current_station + records per-station dwell. Reuses the EXISTING
# WorkshopJobRepository + notify_ready path; calls NO money/E3 engine (no
# stocked-unit state changes here). See api/services/lab_routing.py.

# Roles allowed to scan at a lab bench. Mirrors labels.SCAN_ROLES (CASHIER is
# included for the front-desk PICKUP scan). SUPERADMIN passes via require_roles.
_LAB_SCAN_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "WORKSHOP_STAFF",
    "CASHIER",
)

# Roles allowed to configure (upsert) a store's station registry. Manager ladder
# only -- bench staff scan, managers configure.
_STATION_CONFIG_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
)


@router.get("/stations")
async def list_lab_stations(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List the lab stations configured for a store, in sequence order.

    Store-scoped: resolves to the caller's active store when store_id is omitted.
    Seeds the 6 defaults on first use. Fail-soft: no DB -> empty list."""
    from ...services import lab_routing

    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )
    db = get_db()
    if db is None or not active_store:
        return {"stations": [], "store_id": active_store}
    stations = lab_routing.list_stations(db, active_store)
    return {"stations": stations, "store_id": active_store, "total": len(stations)}


@router.post("/stations")
async def upsert_lab_station(
    body: LabStationUpsert,
    current_user: dict = Depends(require_roles(*_STATION_CONFIG_ROLES)),
):
    """Create or update a single lab station config for a store (key store+code).

    STORE_MANAGER+ only. Validates `code` against the canonical vocabulary."""
    from ...services import lab_routing

    active_store = validate_store_access(body.store_id, current_user) or current_user.get(
        "active_store_id"
    )
    if not active_store:
        raise HTTPException(status_code=400, detail="No store in scope")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    ok, station, reason = lab_routing.upsert_station(
        db,
        store_id=active_store,
        code=body.code,
        actor_id=current_user.get("user_id"),
        label=body.label,
        sequence_order=body.sequence_order,
        is_active=body.is_active,
        target_dwell_minutes=body.target_dwell_minutes,
        advances_job_status=body.advances_job_status,
        auto_notify_customer=body.auto_notify_customer,
    )
    if not ok:
        if reason == "UNKNOWN_STATION":
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_station", "message": f"Unknown station {body.code}."},
            )
        if reason == "INVALID_ADVANCE_STATUS":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_advance_status",
                    "message": (
                        "A station may only advance a job to one of: "
                        + ", ".join(lab_routing.VALID_ADVANCES_JOB_STATUS)
                        + " (or blank to leave the status unchanged)."
                    ),
                },
            )
        raise HTTPException(status_code=503, detail="Failed to save station config")

    # PATIENT SAFETY: station config decides which scan hands a job to a patient
    # (advances_job_status) and whether the customer is auto-notified, so every
    # edit is an auditable control change. Fail-soft -- an audit failure must not
    # lose the operator's config change.
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.station_config_upsert",
                    "entity_type": "lab_station",
                    "entity_id": (station or {}).get("station_id") or body.code,
                    "store_id": active_store,
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "code": body.code,
                        "advances_job_status": (station or {}).get("advances_job_status"),
                        "auto_notify_customer": (station or {}).get("auto_notify_customer"),
                        "is_active": (station or {}).get("is_active"),
                        "sequence_order": (station or {}).get("sequence_order"),
                    },
                }
            )
    except Exception as _audit_exc:  # noqa: BLE001
        logger.warning("[WORKSHOP] station config audit failed: %s", _audit_exc)

    return {"ok": True, "station": station}


@router.get("/stations/{code}/queue")
async def get_station_queue(
    code: str,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_LAB_SCAN_ROLES)),
):
    """Jobs currently AT a given station for a store, oldest-first.

    Each row carries time-at-station + an SLA colour chip. Store-scoped."""
    from ...services import lab_routing

    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )
    db = get_db()
    if db is None or not active_store:
        return {"station": code.upper(), "store_id": active_store, "jobs": [], "total": 0}
    jobs = lab_routing.station_queue(db, active_store, code)
    return {
        "station": code.upper(),
        "store_id": active_store,
        "jobs": jobs,
        "total": len(jobs),
    }


@router.post("/scan")
async def lab_scan(
    body: LabScanBody,
    current_user: dict = Depends(require_roles(*_LAB_SCAN_ROLES)),
):
    """Scan a disposable job card at a lab bench -- the F2 core.

    Resolves the job by scanned_code (job_number or job_id, store-scoped),
    validates `station_code` is the NEXT active station in this job's sequence,
    advances current_station, records server-computed dwell for the station the
    job is leaving, appends to scan_history, and -- when the station config says
    so -- transitions job status (DISPATCH -> READY, PICKUP -> DELIVERED) and
    fires the customer 'ready for pickup' notify INLINE (fail-soft, never blocks
    the scan response).

    LOUD-failure contract: returns HTTP 200 with {ok:false, reason} on any guard
    failure WITHOUT mutating state (the scan box renders a rich in-page error).
    reasons: REPO_UNAVAILABLE / NOT_FOUND / NO_STATIONS / TERMINAL_STAGE /
             UNKNOWN_STATION / WRONG_STATION / ALREADY_HERE / CONCURRENT_CONFLICT
    """
    from ...services import lab_routing

    repo = get_workshop_repository()
    db = get_db()
    if repo is None or db is None:
        return {
            "ok": False,
            "reason": "REPO_UNAVAILABLE",
            "message": "Workshop repository unavailable; cannot route.",
        }

    code = (body.scanned_code or "").strip()
    if not code:
        return {"ok": False, "reason": "NOT_FOUND", "message": "Empty scan code."}

    # Resolve the job: try job_number first (what the card encodes), then job_id.
    job = repo.find_by_number(code)
    if job is None:
        job = repo.find_by_id(code)
    if job is None:
        return {
            "ok": False,
            "reason": "NOT_FOUND",
            "message": f"No workshop job matches the scanned code {code}.",
        }

    # Store-scope guard: existence-hide a cross-store job (medical PII / IDOR).
    if not can_access_store_scoped(job.get("store_id"), current_user):
        return {
            "ok": False,
            "reason": "NOT_FOUND",
            "message": f"No workshop job matches the scanned code {code}.",
        }

    result = lab_routing.advance_lab_station(
        db, job, body.station_code, current_user.get("user_id")
    )
    if not result.get("ok"):
        return result

    # Audit (fail-soft) -- one row per successful lab scan.
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.lab_scan",
                    "entity_type": "workshop_job",
                    "entity_id": result.get("job_id"),
                    "store_id": result.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "from_station": result.get("previous_station"),
                        "to_station": result.get("current_station"),
                        "status": result.get("stage"),
                    },
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] lab-scan audit failed: %s", e)

    # Auto-notify on DISPATCH -> READY (fail-soft; MUST NOT roll back the scan).
    if result.get("auto_notify"):
        try:
            fresh = repo.find_by_id(result.get("job_id")) or job
            notify = await _perform_ready_notify(fresh, current_user.get("user_id"))
            result["notify"] = notify
        except Exception as e:  # noqa: BLE001
            logger.warning("[WORKSHOP] auto-notify on dispatch failed: %s", e)
            result["notify"] = {"whatsapp_status": "FAILED"}

    return result


@router.post("/jobs/{job_id}/print-job-card")
async def print_job_card(
    job_id: str,
    current_user: dict = Depends(require_roles(*_LAB_SCAN_ROLES)),
):
    """Stamp job_card_printed_at / _by on a job and return its traveler label
    payload (the data the disposable Code128 job card prints). Idempotent --
    re-printing is allowed and re-stamps the timestamp."""
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")
    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    if not can_access_store_scoped(job.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Workshop job not found")

    now = datetime.now()
    try:
        repo.update(
            job_id,
            {
                "job_card_printed_at": now.isoformat(),
                "job_card_printed_by": current_user.get("user_id"),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[WORKSHOP] print-job-card stamp failed: %s", e)

    return {
        "ok": True,
        "job_id": job_id,
        "job_number": job.get("job_number"),
        "barcode_value": job.get("job_number") or job_id,
        "job_card_printed_at": now.isoformat(),
        "message": "Job card stamped; print the traveler label.",
    }
