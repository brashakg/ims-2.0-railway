"""F13 remake justification: the reason-code taxonomy and spoilage analytics.

Moved verbatim out of the 3,528-line api/routers/workshop.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta
from ..auth import get_current_user
from ...services import spoilage_analytics
from ...dependencies import get_db, get_workshop_repository, validate_store_access
from ._shared import logger, router


# ============================================================================
# F13 -- REMAKE JUSTIFICATION + SPOILAGE ANALYTICS
# ============================================================================
# Every rework carries a reason-code from an owner-editable taxonomy plus a
# WAC-based spoilage cost in paise (stamped by rework_job above). These
# endpoints expose the taxonomy (GET anyone authenticated, PUT admin-only)
# and the margin-bleed rollup for the workshop dashboard (manager+).

_SPOILAGE_MANAGER_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN")


class RemakeReasonCodesBody(BaseModel):
    """Replacement taxonomy: [{code, label, category}]."""

    codes: List[dict] = Field(default_factory=list)


def _job_in_spoilage_window(job: dict, cutoff_iso: str) -> bool:
    """True when the job belongs in the spoilage window: CREATED in-window OR
    carrying a remake event stamped in-window (an old job remade yesterday IS
    current margin bleed). ISO-string compare, same convention as the KPI walk.
    Pure."""
    created = job.get("created_at")
    created_s = created.isoformat() if isinstance(created, datetime) else str(created or "")
    if created_s >= cutoff_iso:
        return True
    for entry in job.get("remake_reasons") or []:
        if not isinstance(entry, dict):
            continue
        at = entry.get("at")
        at_s = at.isoformat() if isinstance(at, datetime) else str(at or "")
        if at_s >= cutoff_iso:
            return True
    return False


@router.get("/remake-reason-codes")
async def get_remake_reason_codes(
    current_user: dict = Depends(get_current_user),
):
    """The remake reason-code taxonomy (ordered). Seeded default until the
    owner edits it via PUT. Any authenticated role may read it -- the rework
    flow needs it at the bench."""
    return {"codes": spoilage_analytics.list_codes(get_db())}


@router.put("/remake-reason-codes")
async def put_remake_reason_codes(
    payload: RemakeReasonCodesBody,
    current_user: dict = Depends(get_current_user),
):
    """REPLACE the remake reason-code taxonomy (ADMIN/SUPERADMIN only).

    Validation is strict (fail loudly): non-empty list; every entry needs a
    non-empty code + label and a category in VALID_CATEGORIES; codes unique.
    """
    roles = set(current_user.get("roles") or [])
    if not roles.intersection({"ADMIN", "SUPERADMIN"}):
        raise HTTPException(
            status_code=403,
            detail="Only ADMIN/SUPERADMIN may edit remake reason codes",
        )
    err = spoilage_analytics.validate_codes_payload(payload.codes)
    if err:
        raise HTTPException(status_code=422, detail=err)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    codes = spoilage_analytics.normalize_codes_payload(payload.codes)
    try:
        db.get_collection(spoilage_analytics.REASON_CODES_DOC_ID).update_one(
            {"_id": spoilage_analytics.REASON_CODES_DOC_ID},
            {
                "$set": {
                    "codes": codes,
                    "seeded_default": False,
                    "updated_by": current_user.get("user_id"),
                    "updated_at": datetime.now(),
                }
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[WORKSHOP] reason-code save failed: %s", exc)
        raise HTTPException(status_code=503, detail="Failed to save reason codes")
    return {
        "codes": codes,
        "count": len(codes),
        "message": "Remake reason codes updated",
    }


@router.get("/spoilage-analytics")
async def get_spoilage_analytics(
    days: int = Query(90, ge=1, le=730),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Margin-bleed rollup: remake rate + spoilage cost (paise) by category /
    reason / technician over the last `days` (default 90). Manager+ only --
    it exposes cost data.

    Store-scoped like dashboard-kpis (validate_store_access); ADMIN/SUPERADMIN
    with no store resolved get the chain-wide view. Fail-soft: repo absent ->
    an empty summary, never a 500.
    """
    roles = set(current_user.get("roles") or [])
    if not roles.intersection(_SPOILAGE_MANAGER_ROLES):
        raise HTTPException(
            status_code=403, detail="Manager role required for spoilage analytics"
        )

    repo = get_workshop_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    jobs: List[dict] = []
    if repo is not None:
        try:
            if active_store:
                jobs = repo.find_by_store(active_store)
            elif roles.intersection({"ADMIN", "SUPERADMIN"}):
                # Chain-wide margin bleed for the owner's roles.
                jobs = repo.find_many({}, limit=0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WORKSHOP] spoilage job fetch failed: %s", exc)
            jobs = []

    cutoff_iso = (datetime.now() - timedelta(days=days)).isoformat()
    windowed = [j for j in jobs if _job_in_spoilage_window(j, cutoff_iso)]
    summary = spoilage_analytics.build_spoilage_summary(windowed, window_days=days)
    summary["store_id"] = active_store
    summary["as_of"] = datetime.now().isoformat()
    return summary
