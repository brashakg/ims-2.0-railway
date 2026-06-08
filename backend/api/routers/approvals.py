"""
IMS 2.0 - E4 Approvals Router
==============================
HTTP surface for the PIN-gated maker-checker engine in
``api/services/approvals.py``. Mounted at /api/v1/approvals (see api/main.py).

Every business rule + concurrency guard lives in ApprovalEngine; this layer only
maps the engine's ``{"ok", "http", "error"}`` results to HTTPExceptions and reads
the caller identity from the JWT. PIN values are accepted in the request body but
never logged, never echoed, never persisted in plaintext.

Roles (mirrors rbac_policy POLICY):
  - create request / consume / my-requests / get  : any AUTHENTICATED maker
  - inbox                                          : approver + ACCOUNTANT (read-only)
  - approve / reject                               : _APPROVER_ROLES (+ SUPERADMIN)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..services.approvals import ApprovalEngine

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles that may approve / reject a request (business approvers). ACCOUNTANT is
# inbox-read-only (can see pending) but not a business approver here.
_APPROVER_ROLES = ("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER")
_INBOX_ROLES = ("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")


def _get_db():
    from database.connection import get_db

    return get_db().db


def _engine() -> ApprovalEngine:
    return ApprovalEngine(db=_get_db())


def _roles(user: Dict[str, Any]) -> List[str]:
    return list(user.get("roles", []) or [])


def _store_ids(user: Dict[str, Any]) -> List[str]:
    return list(user.get("store_ids", []) or [])


# ============================================================================
# Schemas
# ============================================================================


class RequestCreate(BaseModel):
    action_type: str
    store_id: Optional[str] = None
    entity_id: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)
    context: Optional[Dict[str, Any]] = None
    reason: str = ""
    required_tier: Optional[str] = None
    dedupe_key: Optional[str] = None


class PinAction(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)


class RejectAction(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)
    reason: str = ""


class ConsumeAction(BaseModel):
    action_type: str
    approval_token: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0)


# ============================================================================
# Endpoints
# ============================================================================


@router.post("/requests")
async def create_request(
    body: RequestCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Open an approval request (any authenticated maker)."""
    res = _engine().request(
        action_type=body.action_type,
        requested_by=current_user.get("user_id"),
        requested_by_roles=_roles(current_user),
        store_id=body.store_id or current_user.get("active_store_id"),
        entity_id=body.entity_id,
        amount=body.amount,
        context=body.context,
        reason=body.reason,
        required_tier=body.required_tier,
        dedupe_key=body.dedupe_key,
    )
    if not res.get("ok"):
        err = res.get("error")
        if err == "no_db":
            raise HTTPException(status_code=503, detail="Approval store unavailable")
        if err == "unknown_action_type":
            raise HTTPException(status_code=400, detail="Unknown action_type")
        raise HTTPException(status_code=400, detail=err or "request_failed")
    return res


@router.get("/requests/inbox")
async def get_inbox(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(require_roles(*_INBOX_ROLES)),
):
    """Approver inbox. Defaults to REQUESTED; pass status=ALL for history.
    STORE/AREA managers are store-scoped; ADMIN/SUPERADMIN see every store."""
    eng = _engine()
    scoped = [store_id] if store_id else _store_ids(current_user)
    rows = eng.list_inbox(
        approver_roles=_roles(current_user),
        store_ids=scoped,
        status=status or "REQUESTED",
        limit=200,
    )
    return {"requests": rows, "total": len(rows)}


@router.get("/requests/mine")
async def get_my_requests(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """A maker's own requests + their status (and approval_token once approved)."""
    rows = _engine().list_mine(requested_by=current_user.get("user_id"), limit=200)
    return {"requests": rows, "total": len(rows)}


@router.get("/requests/{request_id}")
async def get_request(
    request_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Fetch one request. The approval_token is only revealed to the maker, the
    consumer, or an HQ role (ADMIN/SUPERADMIN) -- an unrelated approver sees the
    request but not the spendable token."""
    doc = _engine().get(request_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    uid = current_user.get("user_id")
    roles = set(_roles(current_user))
    can_see_token = (
        uid in (doc.get("requested_by"), doc.get("consumed_by"))
        or bool(roles & {"SUPERADMIN", "ADMIN"})
    )
    out = dict(doc)
    if not can_see_token:
        out.pop("approval_token", None)
    return out


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: PinAction,
    current_user: Dict[str, Any] = Depends(require_roles(*_APPROVER_ROLES)),
):
    """Approve a request with the approver's PIN. The state transition + token
    mint are a single atomic op inside the engine."""
    res = _engine().approve(
        request_id,
        approver_user_id=current_user.get("user_id"),
        approver_roles=_roles(current_user),
        pin=body.pin,
        approver_store_ids=_store_ids(current_user),
    )
    return _decode(res, success_keys=("approval_token", "status", "reviewed_at", "request_id"))


@router.post("/requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    body: RejectAction,
    current_user: Dict[str, Any] = Depends(require_roles(*_APPROVER_ROLES)),
):
    """Reject a request with the approver's PIN."""
    res = _engine().reject(
        request_id,
        approver_user_id=current_user.get("user_id"),
        approver_roles=_roles(current_user),
        pin=body.pin,
        reason=body.reason,
        approver_store_ids=_store_ids(current_user),
    )
    return _decode(res, success_keys=("status", "request_id"))


@router.post("/requests/{request_id}/consume")
async def consume_request(
    request_id: str,
    body: ConsumeAction,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Spend an APPROVED approval exactly once (maker or approver). The single-
    use guard is atomic inside the engine."""
    res = _engine().consume_approval(
        consumed_by=current_user.get("user_id"),
        action_type=body.action_type,
        request_id=request_id,
        approval_token=body.approval_token,
        amount=body.amount,
    )
    if res.get("ok"):
        return res
    err = res.get("error")
    code_map = {
        "already_consumed": 409,
        "expired": 410,
        "action_mismatch": 400,
        "amount_exceeded": 400,
        "not_approved": 409,
        "not_found": 404,
        "missing_identifier": 400,
        "no_db": 503,
    }
    raise HTTPException(status_code=code_map.get(err, 400), detail=err or "consume_failed")


# ============================================================================
# Result mapping
# ============================================================================


def _decode(res: Dict[str, Any], *, success_keys) -> Dict[str, Any]:
    """Map an engine {"ok", "http", "error"} result to a response or
    HTTPException. The engine pre-computes the HTTP code for failures."""
    if res.get("ok"):
        out = {"ok": True}
        for k in success_keys:
            if k in res:
                out[k] = res[k]
        return out
    code = int(res.get("http", 400))
    detail: Dict[str, Any] = {"error": res.get("error", "failed")}
    if "remaining" in res:
        detail["remaining"] = res["remaining"]
    if "retry_after_min" in res:
        detail["retry_after_min"] = res["retry_after_min"]
    if "status" in res:
        detail["status"] = res["status"]
    raise HTTPException(status_code=code, detail=detail)
