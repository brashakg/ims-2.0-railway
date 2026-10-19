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
  - inbox                                          : approver + ACCOUNTANT
  - approve / reject                               : _DECIDE_ROLES (+ SUPERADMIN)

Supplier money (owner ruling 2026-10-01): an 'rtv' request's amount is the
supplier credit recorded on a vendor RMA, so every read here (inbox, mine,
get, the consume result) drops it for anyone outside the accounts roles
(services/payables_mask.strip_approval_money). Other action types keep theirs.

'rtv' approvals are the accounts roles' (owner ruling 2026-10-12, R1): the
engine routes one to ADMIN / SUPERADMIN / ACCOUNTANT at every amount tier
(services/approvals.may_approve, the payables rule). A store or area manager
neither finds one in the inbox, nor opens, approves, rejects or spends one by
id -- each answers 404, as for a missing request (its own maker still reads it
under "mine" / by id, amount-free). The ACCOUNTANT approves 'rtv' only; every
other action type refuses them (insufficient_tier), as before. Whoever raised
an 'rtv' cannot approve it ('rtv' is maker-checker): a second accounts person
does.

Approving, rejecting and spending by id (/approve, /reject, /consume) follow
the rule GET applies (_request_in_reach): its maker or consumer, else a caller
whose shops it is in. Anyone else gets exactly what a missing id gets (404),
checked before any role, tier or PIN gate could tell the two apart.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import user_store_scope, validate_store_access
from ..services.approvals import ApprovalEngine, may_approve
from ..services.cost_mask import AP_ROLES
from ..services.payables_mask import SUPPLIER_MONEY_ACTIONS, strip_approval_money

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles that may approve / reject a request (business approvers). ACCOUNTANT
# reads the inbox and approves supplier-money ('rtv') requests only (R1,
# _DECIDE_ROLES below); it is not a business approver of any other type.
_APPROVER_ROLES = ("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER")
_INBOX_ROLES = ("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")
# The approve / reject route gate: the business approvers plus the accounts
# roles (cost_mask.AP_ROLES), who approve supplier-money ('rtv') requests (R1).
# The gate only lets them reach the engine; ApprovalEngine.approve / reject
# decide per action type (an ACCOUNTANT on a refund -> insufficient_tier).
_DECIDE_ROLES = (*_APPROVER_ROLES, *(r for r in AP_ROLES if r not in _APPROVER_ROLES))


def _get_db():
    from database.connection import get_db

    return get_db().db


def _engine() -> ApprovalEngine:
    return ApprovalEngine(db=_get_db())


def _roles(user: Dict[str, Any]) -> List[str]:
    return list(user.get("roles", []) or [])


def _store_ids(user: Dict[str, Any]) -> List[str]:
    return list(user.get("store_ids", []) or [])


def _resolve_names(rows: List[Dict[str, Any]]) -> None:
    """Enrich each approval row IN PLACE with human names for the actor ids
    (requested_by / reviewed_by / consumed_by) so the UI reads in NAMES, not raw
    UUIDs. One batched users lookup for the whole page. Fail-soft: any error
    leaves the rows unchanged (the UI falls back to the id)."""
    if not rows:
        return
    ids = set()
    for r in rows:
        for k in ("requested_by", "reviewed_by", "consumed_by"):
            v = r.get(k)
            if v:
                ids.add(v)
    if not ids:
        return
    name_by_id: Dict[str, str] = {}
    try:
        db = _get_db()
        coll = db.get_collection("users") if db is not None else None
        if coll is not None:
            for u in coll.find(
                {"user_id": {"$in": list(ids)}},
                {"_id": 0, "user_id": 1, "full_name": 1, "username": 1},
            ):
                uid = u.get("user_id")
                if uid:
                    name_by_id[uid] = u.get("full_name") or u.get("username") or uid
    except Exception:  # noqa: BLE001
        return
    for r in rows:
        for src, dst in (
            ("requested_by", "requested_by_name"),
            ("reviewed_by", "reviewed_by_name"),
            ("consumed_by", "consumed_by_name"),
        ):
            v = r.get(src)
            if v and v in name_by_id:
                r[dst] = name_by_id[v]

    # backlog #4: the card also shows store_id -- add store_name beside it.
    try:
        from ..services.name_resolver import store_name_map

        db = _get_db()
        sids = [r.get("store_id") for r in rows if r.get("store_id")]
        smap = store_name_map(db, sids)
        for r in rows:
            sid = r.get("store_id")
            if sid and str(sid) in smap:
                r["store_name"] = smap[str(sid)]
    except Exception:  # noqa: BLE001
        pass


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
    """Open an approval request (any authenticated maker).

    A discount_override request is NEVER built here: the till's order check
    builds it from the server's own pricing of the bill
    (services/discount_approval.ask_manager), so a manager never approves
    figures the cashier typed."""
    if body.action_type == "discount_override":
        raise HTTPException(
            status_code=400,
            detail=(
                "A discount approval is raised from the till (Complete sale -> "
                "Ask a manager to approve), not directly."
            ),
        )
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
    STORE/AREA managers are store-scoped; ADMIN/SUPERADMIN see every store.
    An explicit ?store_id must be one of the caller's shops (the one shop
    rule, validate_store_access), never another shop's queue. Each row
    carries ``can_approve``: whether approve / reject would pass this caller's
    role, maker-checker and shop gates (ApprovalEngine.can_act), so a screen
    offers the buttons only where they work."""
    eng = _engine()
    if store_id:
        validate_store_access(store_id, current_user)
    scoped = [store_id] if store_id else _store_ids(current_user)
    roles = _roles(current_user)
    rows = eng.list_inbox(
        approver_roles=roles,
        store_ids=scoped,
        status=status or "REQUESTED",
        limit=200,
    )
    for row in rows:
        row["can_approve"] = eng.can_act(
            row,
            approver_user_id=current_user.get("user_id"),
            approver_roles=roles,
            approver_store_ids=_store_ids(current_user),
        )
    _resolve_names(rows)
    rows = [strip_approval_money(r, current_user) for r in rows]
    return {"requests": rows, "total": len(rows)}


@router.get("/requests/mine")
async def get_my_requests(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """A maker's own requests + their status (and approval_token once approved)."""
    rows = _engine().list_mine(requested_by=current_user.get("user_id"), limit=200)
    _resolve_names(rows)
    rows = [strip_approval_money(r, current_user) for r in rows]
    return {"requests": rows, "total": len(rows)}


def _request_in_reach(eng: ApprovalEngine, doc: Dict[str, Any], user: Dict[str, Any]) -> bool:
    """The caller may open this request: its maker or consumer, or by the
    engine's shop rule over the caller's shops (their store_ids plus the
    active shop, dependencies.user_store_scope). A supplier-money ('rtv')
    request is further opened only by a role that may approve it (R1)."""
    uid = user.get("user_id")
    if uid and uid in (doc.get("requested_by"), doc.get("consumed_by")):
        return True
    if doc.get("action_type") in SUPPLIER_MONEY_ACTIONS and not may_approve(doc, _roles(user)):
        return False
    _cross, stores = user_store_scope(user)
    # The engine's one shop rule, reused rather than copied.
    # pylint: disable-next=protected-access
    return eng._store_scope_ok(doc.get("store_id"), set(_roles(user)), sorted(stores))


def _in_reach_or_missing(eng: ApprovalEngine, request_id: str, user: Dict[str, Any]) -> None:
    """approve / reject: a request the caller may not open by id (the rule GET
    applies, _request_in_reach) answers exactly what a missing id answers --
    the engine's 404 not_found -- before the engine's tier, maker-checker,
    store or PIN gate can tell another shop's request from no request (F63)."""
    doc = eng.get(request_id)
    if doc is not None and not _request_in_reach(eng, doc, user):
        _decode({"ok": False, "http": 404, "error": "not_found"}, success_keys=())


@router.get("/requests/{request_id}")
async def get_request(
    request_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Fetch one request. The approval_token is only revealed to the maker, the
    consumer, or an HQ role (ADMIN/SUPERADMIN) -- an unrelated approver sees the
    request but not the spendable token.

    Shop scope (F63): a request is read by its maker / consumer, or by anyone
    whose shops it is in by the engine's own rule (ApprovalEngine.
    _store_scope_ok, the one approve / reject / the inbox apply: HQ every
    shop, a shop-less request org-wide, else one of the caller's shops).
    Anyone else gets the same 404 a missing request gets -- never another
    shop's amount, context or reviewer by typing its id. An 'rtv' request
    answers the same 404 to anyone who may not approve one (R1) unless it is
    their own."""
    eng = _engine()
    doc = eng.get(request_id)
    if not doc or not _request_in_reach(eng, doc, current_user):
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
    _resolve_names([out])
    return strip_approval_money(out, current_user)


@router.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: PinAction,
    current_user: Dict[str, Any] = Depends(require_roles(*_DECIDE_ROLES)),
):
    """Approve a request with the approver's PIN. The state transition + token
    mint are a single atomic op inside the engine. An 'rtv' request is the
    accounts roles' at any tier (R1); a manager gets 404 for one, and so does
    anyone for another shop's request (_in_reach_or_missing)."""
    eng = _engine()
    _in_reach_or_missing(eng, request_id, current_user)
    res = eng.approve(
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
    current_user: Dict[str, Any] = Depends(require_roles(*_DECIDE_ROLES)),
):
    """Reject a request with the approver's PIN. Same routing and the same
    shop rule as approve."""
    eng = _engine()
    _in_reach_or_missing(eng, request_id, current_user)
    res = eng.reject(
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
    use guard is atomic inside the engine. An 'rtv' approval is spent only by
    a role that may approve one (R1); anyone else gets 404, the approval
    untouched.

    Shop scope: the request must be one the caller may open by id
    (_request_in_reach, the rule GET applies -- its maker or consumer, else
    the engine's shop rule over the caller's shops). Anyone else gets the 404
    a missing request gets here, and the approval is left unspent: a request
    id alone never spends another shop's approval."""
    eng = _engine()
    doc = eng.get(request_id)
    if doc is not None and not _request_in_reach(eng, doc, current_user):
        raise HTTPException(status_code=404, detail="not_found")
    res = eng.consume_approval(
        consumed_by=current_user.get("user_id"),
        action_type=body.action_type,
        request_id=request_id,
        approval_token=body.approval_token,
        amount=body.amount,
        consumer_roles=_roles(current_user),
    )
    if res.get("ok"):
        return {**res, "request": strip_approval_money(res.get("request"), current_user)}
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
