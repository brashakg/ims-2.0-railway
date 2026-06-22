"""
IMS 2.0 - Feature #18: Vendor volume-rebate tracker router
==========================================================
HTTP surface for ``api/services/rebate_engine.py``. Configure per-vendor
volume-rebate agreements (tiered on accepted purchase-invoice spend), preview a
period's earned rebate, and MANUALLY post it -- which REDUCES VENDOR AP via a
credit note (you owe the vendor less) and records a Tally JV intent (credit the
vendor ledger / debit Rebates-Receivable; not live-dispatched). Owner decision:
manual-post only (no auto-posting); the (agreement_id, period_start) unique
index makes a double-post impossible.

Finance roles only (mirrors vendor-bill access). No emoji (Windows cp1252).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..services import rebate_engine as svc

router = APIRouter(tags=["vendor-rebates"])

_AP_ROLES = {"ACCOUNTANT", "ADMIN", "SUPERADMIN"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require(user: Dict[str, Any], what: str):
    if not (_roles(user) & _AP_ROLES):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _raise(exc: "svc.RebateError"):
    raise HTTPException(status_code=int(getattr(exc, "status", 400)), detail=str(exc))


def _period_lock_check(posting_date):
    """Bound finance period-lock; raises HTTPException(423) on a locked period."""
    from .finance import check_period_locked

    check_period_locked(_get_db(), posting_date)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AgreementBody(BaseModel):
    vendor_id: str = Field(..., min_length=1)
    name: Optional[str] = None
    period: str = "MONTHLY"
    tiers: List[Dict[str, Any]] = Field(default_factory=list)
    active: bool = True


class AgreementUpdateBody(BaseModel):
    name: Optional[str] = None
    period: Optional[str] = None
    tiers: Optional[List[Dict[str, Any]]] = None
    active: Optional[bool] = None


class PostBody(BaseModel):
    agreement_id: str = Field(..., min_length=1)
    period_start: str = Field(..., min_length=1)
    period_end: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Agreements
# ---------------------------------------------------------------------------


@router.post("/agreements")
async def create_agreement(
    body: AgreementBody, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Create a rebate agreement (validates the tier ladder up front)."""
    _require(current_user, "manage vendor rebates")
    try:
        return svc.create_agreement(_get_db(), body.model_dump(), actor=current_user)
    except svc.RebateError as exc:
        _raise(exc)


@router.get("/agreements")
async def list_agreements(
    vendor_id: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    _require(current_user, "view vendor rebates")
    return {"agreements": svc.list_agreements(_get_db(), vendor_id)}


@router.put("/agreements/{agreement_id}")
async def update_agreement(
    agreement_id: str,
    body: AgreementUpdateBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    _require(current_user, "manage vendor rebates")
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return svc.update_agreement(
            _get_db(), agreement_id, payload, actor=current_user
        )
    except svc.RebateError as exc:
        _raise(exc)


@router.get("/agreements/{agreement_id}/preview")
async def preview(
    agreement_id: str,
    period_start: str,
    period_end: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Compute spend + resolved tier + earned rebate for a period. NO write."""
    _require(current_user, "view vendor rebates")
    try:
        return svc.preview(_get_db(), agreement_id, period_start, period_end)
    except svc.RebateError as exc:
        _raise(exc)


# ---------------------------------------------------------------------------
# Manual post (reduces vendor AP) + ledger
# ---------------------------------------------------------------------------


@router.post("/post")
async def post_rebate(
    body: PostBody, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Manually post a period's earned rebate. Double-post-guarded (409). The
    posted rebate REDUCES the vendor's AP via a credit note + records the Tally
    JV intent. 423 if the posting period is locked."""
    _require(current_user, "post a vendor rebate")
    try:
        return svc.post(
            _get_db(),
            body.agreement_id,
            body.period_start,
            body.period_end,
            actor=current_user,
            period_lock_check=_period_lock_check,
        )
    except svc.RebateError as exc:
        _raise(exc)


@router.get("/ledger")
async def list_ledger(
    vendor_id: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    _require(current_user, "view vendor rebates")
    return {"ledger": svc.list_ledger(_get_db(), vendor_id)}


@router.get("/ledger/{rebate_id}")
async def get_ledger(
    rebate_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    _require(current_user, "view vendor rebates")
    row = svc.get_ledger(_get_db(), rebate_id)
    if not row:
        raise HTTPException(status_code=404, detail="rebate ledger row not found")
    return row
