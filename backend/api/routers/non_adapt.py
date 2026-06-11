"""
IMS 2.0 - Feature #14: Non-adaptation / remake tracking router
==============================================================
HTTP surface for ``api/services/non_adapt.py``. A customer who cannot ADAPT to
new spectacles (esp. progressives) gets a REMAKE tracked against the original
order; the charge is decided by an E2 policy (within a window -> free / %; outside
-> chargeable). The non-adapt rate is a QUALITY signal (by reason / optometrist /
lens brand).

READ-ONLY over orders/workshop (a remake order/job goes through the EXISTING
create path) -- #14 only records the non-adapt + the remake LINK + the policy
charge DECISION. Every route store-scopes (validate_store_access) and gates by
role: a plain cashier/sales role can never record a non-adapt or initiate a
(possibly waived) remake. The waiver is audited.

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import validate_store_access, get_order_repository
from ..services import non_adapt as svc

router = APIRouter(tags=["non-adapt"])

# Who may record a non-adaptation + initiate a remake: clinical + store
# management (a non-adapt is an Rx/quality judgement, not a till action). A plain
# cashier / sales / workshop role is intentionally absent -> 403.
_RECORD_ROLES = {"OPTOMETRIST", "STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
# Who may read the quality report (management + finance).
_REPORT_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "ACCOUNTANT", "SUPERADMIN"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require(user: Dict[str, Any], allowed: set, what: str):
    if not (_roles(user) & allowed):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _engine():
    return svc.NonAdaptEngine(_get_db())


def _audit(action: str, *, entity_id: str, actor: Dict[str, Any],
           store_id: Optional[str], detail: Dict[str, Any]) -> None:
    try:
        from ..dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create({
            "action": action, "entity_type": "non_adapt_record", "entity_id": entity_id,
            "store_id": store_id or actor.get("active_store_id"),
            "user_id": actor.get("user_id"),
            "user_name": actor.get("full_name") or actor.get("username"),
            "severity": "INFO", "source": "non_adapt", "detail": detail or {},
        })
    except Exception:  # noqa: BLE001
        return


def _load_order(order_id: str) -> Dict[str, Any]:
    repo = get_order_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="order store unavailable")
    order = repo.find_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="original order not found")
    return order


def _raise_svc(exc: "svc.NonAdaptError"):
    raise HTTPException(status_code=int(getattr(exc, "status", 400)), detail=str(exc))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RecordBody(BaseModel):
    order_id: str = Field(..., description="Original order the customer can't adapt to")
    item_id: Optional[str] = Field(None, description="Specific lens line item (optional)")
    reason: str = Field(..., description=f"One of {list(svc.NON_ADAPT_REASONS)}")
    reason_note: Optional[str] = None
    optometrist_id: Optional[str] = None
    rx_recheck_required: bool = True
    prescription_id: Optional[str] = None


class RemakeBody(BaseModel):
    remake_order_id: Optional[str] = Field(None, description="New remake order id (if created)")
    remake_workshop_job_id: Optional[str] = Field(None, description="New lab job id (if created)")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/record")
async def record_non_adapt(body: RecordBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Record a non-adaptation against an original order line. Clinical/manager only."""
    _require(current_user, _RECORD_ROLES, "record a non-adaptation")
    order = _load_order(body.order_id)
    store_id = order.get("store_id")
    validate_store_access(store_id, current_user)
    line = svc.find_order_line(order, body.item_id)
    # Adversarial P1: a supplied item_id that matches NO order line returns None,
    # which would drive the charge basis to 0 -> a chargeable remake silently
    # becomes FREE + escapes the waiver audit. Fail loud instead.
    if body.item_id and line is None:
        raise HTTPException(
            status_code=400,
            detail=f"order line {body.item_id} not found on order {body.order_id}",
        )
    try:
        rec = _engine().record(
            order=order, line=line, reason=body.reason, store_id=store_id,
            actor=current_user, reason_note=body.reason_note,
            optometrist_id=body.optometrist_id, rx_recheck_required=body.rx_recheck_required,
            prescription_id=body.prescription_id,
        )
    except svc.NonAdaptError as exc:
        _raise_svc(exc)
    _audit("non_adapt.record", entity_id=rec["record_id"], actor=current_user,
           store_id=store_id, detail={"reason": rec["reason"], "within_window": rec["within_window"]})
    return rec


@router.post("/{record_id}/remake")
async def initiate_remake(record_id: str, body: RemakeBody,
                          current_user: Dict[str, Any] = Depends(get_current_user)):
    """Link a remake (order + lab job) to a recorded non-adaptation. Clinical/manager
    only; a waived (free/discounted) remake stamps + audits the authorizer so a
    cashier can never silently waive a charge."""
    _require(current_user, _RECORD_ROLES, "initiate a remake")
    rec = _engine().get(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="non-adapt record not found")
    validate_store_access(rec.get("store_id"), current_user)
    waived = bool(rec.get("charge_waived"))
    try:
        updated = _engine().link_remake(
            record_id,
            remake_order_id=body.remake_order_id,
            remake_workshop_job_id=body.remake_workshop_job_id,
            actor=current_user,
            authorized_by=current_user.get("user_id") if waived else None,
        )
    except svc.NonAdaptError as exc:
        _raise_svc(exc)
    if waived:
        _audit("non_adapt.remake_waiver", entity_id=record_id, actor=current_user,
               store_id=rec.get("store_id"),
               detail={"original_cost_paise": rec.get("original_cost_paise"),
                       "remake_charge_paise": rec.get("remake_charge_paise"),
                       "within_window": rec.get("within_window")})
    return updated


@router.get("/order/{order_id}")
async def list_for_order(order_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Per-order non-adapt / remake history. Clinical/manager only + store-scoped."""
    _require(current_user, _RECORD_ROLES, "view non-adaptation history")
    order = _load_order(order_id)
    validate_store_access(order.get("store_id"), current_user)
    return {"items": _engine().list_for_order(order_id, store_id=order.get("store_id"))}


@router.get("/{record_id}")
async def get_record(record_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """One non-adapt record. Clinical/manager only + store-scoped."""
    _require(current_user, _RECORD_ROLES, "view a non-adaptation record")
    rec = _engine().get(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="non-adapt record not found")
    validate_store_access(rec.get("store_id"), current_user)
    return rec


@router.get("")
async def quality_report(
    store_id: str = Query(..., description="Store to report on"),
    reason: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Non-adapt QUALITY report: counts by reason / optometrist / lens brand +
    waiver/charge tallies. Management/finance only + store-scoped."""
    _require(current_user, _REPORT_ROLES, "view the non-adaptation report")
    validate_store_access(store_id, current_user)
    return _engine().report(store_id=store_id, reason=reason, date_from=date_from, date_to=date_to)
