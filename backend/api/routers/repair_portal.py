"""
IMS 2.0 - Feature #48: Multi-category servicing & repair portal router
======================================================================
HTTP surface for ``api/services/repair_portal.py``. A new service-revenue
stream: a per-store service CATALOG (frame repair / watch battery / repair /
send-to-vendor / ...) and a repair-JOB lifecycle
(INTAKE -> IN_PROGRESS -> SENT_TO_VENDOR -> READY -> DELIVERED, + CANCELLED).

Every route is store-scoped (``validate_store_access``) -- a BV-1 actor can
neither see nor mutate a BV-2 catalog/job. Catalog edits are CATALOG_MANAGER+;
intake + transitions are the store staff family. A transition to READY fires a
DARK status SMS (a PENDING notification row; nothing leaves while
DISPATCH_MODE=off). POS-billing on DELIVERED is DEFERRED -- DELIVERED just
stamps the status; orders.py / POS are NOT touched here.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import repair_portal as svc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["repairs"])

# Catalog edits change what a store can sell -> catalog/admin ladder.
_CATALOG_ROLES = {"CATALOG_MANAGER", "ADMIN", "SUPERADMIN"}
# Intake + lifecycle transitions are floor + management counter work.
_JOB_ROLES = {
    "SALES_STAFF",
    "SALES_CASHIER",
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
    "SUPERADMIN",
}
# Read catalog/jobs: any authenticated store staff.
_READ_ROLES = {
    "SALES_STAFF",
    "SALES_CASHIER",
    "CASHIER",
    "OPTOMETRIST",
    "CATALOG_MANAGER",
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ACCOUNTANT",
    "ADMIN",
    "SUPERADMIN",
}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require(user: Dict[str, Any], allowed: set, what: str):
    if not (_roles(user) & allowed):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _raise(exc: "svc.RepairError"):
    raise HTTPException(status_code=int(getattr(exc, "status", 400)), detail=str(exc))


def _audit(action, *, entity_id, actor, store_id, detail):
    try:
        from ..dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": action,
                "entity_type": "repair_job",
                "entity_id": entity_id,
                "store_id": store_id,
                "user_id": actor.get("user_id"),
                "severity": "INFO",
                "source": "repair_portal",
                "detail": detail or {},
            }
        )
    except Exception:  # noqa: BLE001
        return


def _ready_sms(job: Dict[str, Any]) -> None:
    """DARK status SMS on READY. Rides notification_service (PENDING row;
    DISPATCH_MODE-gated -- nothing leaves on a fresh deploy). Fail-soft."""
    try:
        import asyncio

        from ..services import notification_service as ns

        coro = ns.send_notification(
            store_id=job.get("store_id") or "",
            customer_id=job.get("customer_id") or "",
            customer_phone=job.get("walkin_mobile") or "",
            customer_name=job.get("walkin_name") or "",
            template_id="repair_ready",
            channel="SMS",
            category="SERVICE",
            variables={"job_id": job.get("job_id"), "service": job.get("service_type")},
            related_entity_type="repair_job",
            related_entity_id=job.get("job_id"),
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return
        # Inside a running loop (route handler) -- schedule fire-and-forget.
        asyncio.ensure_future(coro)
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ServiceUpsertBody(BaseModel):
    service_id: Optional[str] = None
    name: str = Field(..., min_length=1)
    category: str
    default_price_paise: int = Field(0, ge=0)
    enabled_store_ids: List[str] = Field(default_factory=list)
    active: bool = True


class JobIntakeBody(BaseModel):
    store_id: str = Field(..., min_length=1)
    service_id: Optional[str] = None
    service_type: str = Field(..., min_length=1)
    customer_id: Optional[str] = None
    walkin_name: Optional[str] = None
    walkin_mobile: Optional[str] = None
    quoted_price_paise: Optional[int] = None
    quoted_price: Optional[float] = None


class TransitionBody(BaseModel):
    to: str = Field(..., min_length=1)
    note: Optional[str] = None
    vendor_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/catalog")
async def get_catalog(
    store_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Active services enabled for a store. Read roles + store-scoped."""
    _require(current_user, _READ_ROLES, "view the repair catalog")
    validate_store_access(store_id, current_user)
    return {"services": svc.list_services(_get_db(), store_id)}


@router.post("/catalog")
async def create_service(
    body: ServiceUpsertBody, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Create a catalog service (CATALOG_MANAGER+)."""
    _require(current_user, _CATALOG_ROLES, "manage the repair catalog")
    payload = body.dict()
    payload["service_id"] = None  # POST always creates
    try:
        return svc.upsert_service(_get_db(), payload, actor=current_user)
    except svc.RepairError as exc:
        _raise(exc)


@router.put("/catalog/{service_id}")
async def update_service(
    service_id: str,
    body: ServiceUpsertBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Replace a catalog service (CATALOG_MANAGER+)."""
    _require(current_user, _CATALOG_ROLES, "manage the repair catalog")
    payload = body.dict()
    payload["service_id"] = service_id
    try:
        return svc.upsert_service(_get_db(), payload, actor=current_user)
    except svc.RepairError as exc:
        _raise(exc)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@router.post("/jobs")
async def open_job(
    body: JobIntakeBody, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Open a repair job at INTAKE. Staff roles + store-scoped."""
    _require(current_user, _JOB_ROLES, "open a repair job")
    validate_store_access(body.store_id, current_user)
    try:
        job = svc.open_job(_get_db(), body.store_id, body.dict(), actor=current_user)
    except svc.RepairError as exc:
        _raise(exc)
    _audit(
        "repair.intake",
        entity_id=job["job_id"],
        actor=current_user,
        store_id=body.store_id,
        detail={"service_type": job.get("service_type")},
    )
    return job


@router.post("/jobs/{job_id}/transition")
async def transition_job(
    job_id: str,
    body: TransitionBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Advance a job's status (guarded atomic transition). Staff + store-scoped.
    On READY, fires a DARK status SMS."""
    _require(current_user, _JOB_ROLES, "update a repair job")
    db = _get_db()
    job = svc.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="repair job not found")
    store_id = job.get("store_id")
    validate_store_access(store_id, current_user)
    try:
        result = svc.transition_job(
            db,
            job_id,
            store_id,
            body.to,
            actor=current_user,
            note=body.note,
            vendor_id=body.vendor_id,
        )
    except svc.RepairError as exc:
        _raise(exc)
    if result["to"] == svc.STATUS_READY:
        _ready_sms(result["job"])
    _audit(
        "repair.transition",
        entity_id=job_id,
        actor=current_user,
        store_id=store_id,
        detail={"from": result["from"], "to": result["to"]},
    )
    return result["job"]


@router.get("/jobs")
async def list_jobs(
    store_id: str,
    status: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """List a store's repair jobs (optionally by status). Read + store-scoped."""
    _require(current_user, _READ_ROLES, "view repair jobs")
    validate_store_access(store_id, current_user)
    return {"jobs": svc.list_jobs(_get_db(), store_id, status=status)}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """One repair job. Read + store-scoped."""
    _require(current_user, _READ_ROLES, "view a repair job")
    job = svc.get_job(_get_db(), job_id)
    if not job:
        raise HTTPException(status_code=404, detail="repair job not found")
    validate_store_access(job.get("store_id"), current_user)
    return job
