"""Lens QC: pass / fail, the structured QC checklist (with audited waiver) and
rework (capped, with a spoilage cost).

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query
from typing import Optional
from datetime import datetime
import uuid
from ..auth import require_roles
from ...services import spoilage_analytics
from ...dependencies import get_db, get_workshop_repository, get_audit_repository
from ._shared import (
    WORKSHOP_ROLES,
    _QC_INPUT_STATUSES,
    _QC_INPUT_STATUS_MESSAGE,
    logger,
    router,
)
from .models import QcChecklistBody
from .helpers import _assert_job_store_access


@router.post("/jobs/{job_id}/qc")
async def qc_job(
    job_id: str,
    passed: bool = Query(...),
    notes: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Simple pass/fail QC result on a completed job.

    Gate changed to WORKSHOP_ROLES (WORKSHOP_STAFF / STORE_MANAGER / AREA_MANAGER / ADMIN
    + implicit SUPERADMIN). Sales staff cannot run QC.

    A job with passed=True advances to READY; passed=False advances to QC_FAILED.
    The job must be in one of _QC_INPUT_STATUSES; any other state returns 400.
    """
    repo = get_workshop_repository()

    if repo is not None:
        job = repo.find_by_id(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Workshop job not found")
        _assert_job_store_access(job, current_user)

        if job.get("status") not in _QC_INPUT_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=_QC_INPUT_STATUS_MESSAGE.format(status=job.get("status")),
            )

        if repo.add_qc_result(
            job_id,
            passed,
            notes or "",
            current_user.get("user_id"),
        ):
            status = "READY" if passed else "QC_FAILED"
            return {
                "job_id": job_id,
                "status": status,
                "qc_passed": passed,
                "message": "QC recorded",
            }

        raise HTTPException(status_code=500, detail="Failed to record QC")

    return {"message": "QC recorded", "status": "READY" if passed else "QC_FAILED"}


@router.post("/jobs/{job_id}/qc-checklist")
async def qc_checklist(
    job_id: str,
    payload: QcChecklistBody,
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Submit a structured per-item QC checklist for a workshop job.

    This is the authoritative QC endpoint for the checklist feature. It
    stores each check item (key, label, pass/fail, note) along with the
    reviewer identity and a timestamp, then advances the job status:

    - All items passed (or waived with reason): job -> READY_FOR_PICKUP
    - Any item failed without waiver: job -> QC_FAILED

    A job MUST NOT reach READY status via any other path unless QC passed or
    was explicitly waived here. This endpoint enforces that invariant.

    Accepted input states: _QC_INPUT_STATUSES (see its comment -- it covers every
    state the QC handover gate can strand a job in, including the IN_PROGRESS the
    scan flow actually parks a held job in). A FAIL on a job already on the
    pickup shelf correctly pulls it back off into QC_FAILED for rework.

    Gate: WORKSHOP_STAFF / STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN.
    Sales staff and cashiers cannot run QC.
    """
    repo = get_workshop_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Workshop repository unavailable")

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    if job.get("status") not in _QC_INPUT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=_QC_INPUT_STATUS_MESSAGE.format(status=job.get("status")),
        )

    # Validate waiver: waived=True requires a waive_reason
    if payload.waived and not (payload.waive_reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="waive_reason is required when waived=True",
        )

    # Determine overall pass/fail: all items must pass unless explicitly waived
    all_passed = all(item.passed for item in payload.checklist)
    effective_pass = all_passed or payload.waived

    # Stamp each checklist item with reviewer identity + timestamp
    now = datetime.now()
    stamped_items = [
        {
            "key": item.key,
            "label": item.label,
            "passed": item.passed,
            "note": item.note or "",
            "checked_by": current_user.get("user_id"),
            "checked_at": now.isoformat(),
        }
        for item in payload.checklist
    ]

    notes_parts = []
    if payload.overall_notes:
        notes_parts.append(payload.overall_notes)
    if payload.waived:
        notes_parts.append(
            "QC WAIVED by {}: {}".format(
                current_user.get("username") or current_user.get("user_id"),
                payload.waive_reason,
            )
        )
    combined_notes = " | ".join(notes_parts) if notes_parts else ""

    # Audit (fail-soft)
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "workshop.qc_checklist",
                    "entity_type": "workshop_job",
                    "entity_id": job_id,
                    "store_id": job.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "effective_pass": effective_pass,
                        "waived": payload.waived,
                        "item_count": len(stamped_items),
                        "failed_items": [
                            i["key"] for i in stamped_items if not i["passed"]
                        ],
                    },
                }
            )
    except Exception as audit_exc:  # noqa: BLE001
        logger.warning("[WORKSHOP] qc_checklist audit failed: %s", audit_exc)

    if repo.add_qc_result(
        job_id,
        effective_pass,
        combined_notes,
        current_user.get("user_id"),
        checklist_items=stamped_items,
        waived=payload.waived,
        waive_reason=payload.waive_reason,
    ):
        target_status = "READY" if effective_pass else "QC_FAILED"
        return {
            "job_id": job_id,
            "status": target_status,
            "qc_passed": effective_pass,
            "all_items_passed": all_passed,
            "waived": payload.waived,
            "checklist": stamped_items,
            "message": (
                "QC checklist submitted — job is now ready for pickup"
                if effective_pass
                else "QC checklist submitted — job flagged for rework"
            ),
        }

    raise HTTPException(status_code=500, detail="Failed to record QC checklist")


@router.post("/jobs/{job_id}/rework")
async def rework_job(
    job_id: str,
    remake_reason_code: Optional[str] = Query(None),
    spoilage_category: Optional[str] = Query(None),
    notes: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*WORKSHOP_ROLES)),
):
    """Send QC-failed job back for rework (QC_FAILED → IN_PROGRESS).

    F13: a rework is a REMAKE -- a spoiled lens and real margin bleed. The
    caller MUST justify it with a `remake_reason_code` from the owner-editable
    taxonomy (422 otherwise -- fail loudly). The spoiled lens is costed in
    integer paise from the product's weighted-average cost (`cost_price`,
    maintained by the purchase_match moving-average true-up; fail-soft 0) and
    the justification entry is appended to `job.remake_reasons[]` in the SAME
    single guarded find_one_and_update that flips the status -- a concurrent
    double-rework loses the guard and 409s instead of double-appending.
    `spoilage_category` optionally overrides the code's default fault category.
    A SPOILAGE row is also written to lens_stock_audit (fail-soft).
    """
    repo = get_workshop_repository()

    if repo is None:
        return {"message": "Job sent for rework"}

    job = repo.find_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Workshop job not found")
    _assert_job_store_access(job, current_user)

    if job.get("status") != "QC_FAILED":
        raise HTTPException(
            status_code=400, detail="Only QC_FAILED jobs can be sent for rework"
        )

    # --- F13: required justification (no reason, no remake) ---
    db = get_db()
    code = (remake_reason_code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="remake_reason_code is required")
    known_codes = spoilage_analytics.valid_codes(db)
    code_entry = known_codes.get(code)
    if code_entry is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown remake_reason_code {code!r}. "
                f"Allowed: {', '.join(sorted(known_codes))}"
            ),
        )
    category = (spoilage_category or "").strip().upper() or str(
        code_entry.get("category") or ""
    )
    if category not in spoilage_analytics.VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid spoilage_category {category!r}. "
                f"Allowed: {', '.join(spoilage_analytics.VALID_CATEGORIES)}"
            ),
        )

    # --- F13: WAC spoilage cost (fail-soft 0; a costing gap never blocks) ---
    def _lens_cost_rupees(j: dict):
        pid = ((j.get("lens_details") or {}).get("product_id")) or j.get(
            "lens_product_id"
        )
        if not pid or db is None:
            return None
        try:
            prod = db.get_collection("products").find_one({"product_id": pid})
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(prod, dict):
            return None
        # cost_price IS the WAC: purchase_match.moving_average_cost true-ups
        # blend every invoice receipt into it.
        return prod.get("cost_price")

    cost_paise = spoilage_analytics.spoilage_cost_paise(job, _lens_cost_rupees)

    now = datetime.now()
    attempt = int(job.get("rework_count") or 0) + 1
    remake_entry = {
        "reason_code": code,
        "category": category,
        "cost_paise": cost_paise,
        "by": current_user.get("user_id"),
        "at": now.isoformat(),
        "notes": notes,
    }

    # SINGLE atomic write: status advance + rework_count increment + the
    # remake_reasons append, guarded on status=QC_FAILED (mirrors the
    # blind-stock-take soft-lock pattern). Exactly one concurrent rework wins.
    from pymongo import ReturnDocument

    try:
        updated = repo.collection.find_one_and_update(
            {repo.id_field: job_id, "status": "QC_FAILED"},
            {
                "$set": {
                    "status": "IN_PROGRESS",
                    "status_updated_at": now,
                    "status_updated_by": current_user.get("user_id"),
                    "status_notes": notes or f"Rework #{attempt}",
                    "updated_at": now,
                },
                "$inc": {"rework_count": 1},
                "$push": {"remake_reasons": remake_entry},
            },
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WORKSHOP] rework atomic update failed for %s: %s", job_id, exc)
        updated = None
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Job is no longer QC_FAILED (raced by another update)",
        )

    rework_count = int(updated.get("rework_count") or attempt)

    # SPOILAGE audit row (fail-soft, mirrors lens_stock's audit convention).
    try:
        if db is not None:
            db.get_collection("lens_stock_audit").insert_one(
                {
                    "audit_id": uuid.uuid4().hex,
                    "source_type": "SPOILAGE",
                    "job_id": job_id,
                    "store_id": job.get("store_id"),
                    "reason_code": code,
                    "category": category,
                    "cost_paise": cost_paise,
                    "by": current_user.get("user_id"),
                    "at": now,
                }
            )
    except Exception as audit_exc:  # noqa: BLE001
        logger.warning(
            "[WORKSHOP] SPOILAGE audit insert failed for %s: %s", job_id, audit_exc
        )

    return {
        "job_id": job_id,
        "status": "IN_PROGRESS",
        "rework_count": rework_count,
        "remake_reason_code": code,
        "spoilage_category": category,
        "spoilage_cost_paise": cost_paise,
        "message": f"Job sent for rework (attempt #{rework_count})",
    }
