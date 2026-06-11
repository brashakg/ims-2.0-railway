"""
IMS 2.0 - F23 Blind EOD cash tally & Z-Read router
===================================================
HTTP surface for the blind till-session engine in ``api/services/eod_tally.py``
(which derives the expected-cash figure from E5's
``tender_reconciliation.reconcile_window`` over ``order.payments[]`` -- POS
capture is UNCHANGED).

Mounted at /api/v1/till WITHOUT a router-level role gate (a cashier must reach
open + blind-submit); every route gates inline + store-scopes via
``validate_store_access`` (a store closes its OWN day).

BLIND ENFORCEMENT: the expected figure + variance are NEVER returned to a
SALES_CASHIER / CASHIER before a manager locks. Open + blind-submit responses
for those roles are run through ``redact_for_cashier`` at the DATA layer, not
just the UI.

Routes (mirrors rbac_policy POLICY):
  POST /till/sessions                     open a session (cashier/manager+)
  POST /till/sessions/{id}/blind-submit   submit blind count (cashier/manager+)
  POST /till/sessions/{id}/lock           reveal variance + soft-lock (manager+)
  POST /till/sessions/{id}/reopen         release the soft-lock (reopen roles)
  GET  /till/sessions                     list sessions (manager+/finance)
  GET  /till/sessions/{id}                one session (cashier sees redacted)
  GET  /till/sessions/{id}/zread          full Z-Read report (manager+/finance)

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import eod_tally as till

router = APIRouter(tags=["till"])

# Who can OPEN / submit a blind count: the cashier roles + store management.
_TILL_OPERATE_ROLES = {
    "SALES_CASHIER",
    "CASHIER",
    "SALES_STAFF",
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
    "SUPERADMIN",
}
# Who may reveal the expected figure + LOCK the Z-Read (managers + above).
_TILL_LOCK_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
# Who may READ the expected/variance (Z-Read + full session view).
_TILL_READ_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "ACCOUNTANT", "SUPERADMIN"}
# Roles that NEVER see the expected figure pre-lock (blind data-layer redaction).
_CASHIER_ONLY_ROLES = {"SALES_CASHIER", "CASHIER", "SALES_STAFF"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _is_cashier_only(user: Dict[str, Any]) -> bool:
    """True if the caller holds ONLY cashier/floor roles (no manager+ role).
    Such a caller must never see the expected figure before the manager locks."""
    roles = _roles(user)
    if roles & _TILL_READ_ROLES:
        return False
    return bool(roles & _CASHIER_ONLY_ROLES)


def _validate_session_date(raw: Optional[str]) -> str:
    """Validate + normalize the opening ``session_date`` to a bounded IST day.

    The expected-cash window is derived from this date; a malformed or absent
    value must NEVER fall through to an open-ended (un-bounded) reconciliation
    window. Rules:
      * absent/blank -> IST today,
      * must parse as an ISO calendar date (``YYYY-MM-DD``); junk -> 400,
      * must be within IST today +/- 1 day (a store closes today, sometimes the
        previous day late, or pre-opens tomorrow); out of range -> 400.
    Returns the normalized ``YYYY-MM-DD`` string."""
    from datetime import date as _date, timedelta
    from ..utils.ist import ist_today

    if raw is None or not str(raw).strip():
        return ist_today().isoformat()
    try:
        d = _date.fromisoformat(str(raw).strip()[:10])
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid session_date (expected YYYY-MM-DD)")
    today = ist_today()
    if not (today - timedelta(days=1) <= d <= today + timedelta(days=1)):
        raise HTTPException(status_code=400, detail="session_date out of range (IST today +/- 1 day)")
    return d.isoformat()


def _raise(res: Dict[str, Any]):
    """Translate a service error envelope to an HTTPException."""
    raise HTTPException(status_code=int(res.get("http", 400)), detail=res.get("error", "error"))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DenominationLine(BaseModel):
    face: int = Field(..., description="Face value in rupees, e.g. 500")
    pieces: int = Field(0, ge=0, description="Number of notes/coins of this face")
    kind: str = Field("note", description="'note' or 'coin'")


class OpenSession(BaseModel):
    store_id: Optional[str] = None
    session_date: Optional[str] = Field(None, description="IST day YYYY-MM-DD; defaults to today")
    shift: Optional[str] = None
    opening_denominations: List[DenominationLine] = Field(default_factory=list)
    opening_float_paisa: Optional[int] = Field(None, description="Override of the denomination sum (paisa)")
    note: Optional[str] = None


class BlindSubmit(BaseModel):
    blind_denominations: List[DenominationLine] = Field(default_factory=list)
    blind_count_paisa: Optional[int] = Field(
        None, description="Optional explicit total (paisa); must equal the denomination sum"
    )
    cash_payouts_paisa: int = Field(0, ge=0, description="Cash paid out of the drawer during the session (paisa)")
    idempotency_key: Optional[str] = Field(None, description="Retry-safe key; a duplicate submit returns existing state")


class ReopenBody(BaseModel):
    reason: str = Field(..., min_length=1, description="Mandatory reason for releasing the soft-lock")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@router.post("/sessions")
async def open_till_session(
    body: OpenSession,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Open a blind till session. Cashier/manager roles. Store-scoped (a store
    opens its OWN day). NO expected figure is computed at open (blind)."""
    if not (_roles(current_user) & _TILL_OPERATE_ROLES):
        raise HTTPException(status_code=403, detail="not permitted to open a till session")
    store_id = validate_store_access(body.store_id or "", current_user)
    if not store_id:
        raise HTTPException(status_code=400, detail="No store context for this user")
    session_date = _validate_session_date(body.session_date)

    res = till.open_session(
        _get_db(),
        store_id=store_id,
        session_date=session_date,
        opening_denominations=[d.model_dump() for d in body.opening_denominations],
        opening_float_paisa=body.opening_float_paisa,
        shift=body.shift,
        note=body.note,
        actor=current_user,
    )
    if not res.get("ok"):
        _raise(res)
    session = res["session"]
    session.pop("_id", None)
    # Open carries no expected figure yet, but redact defensively for cashiers.
    if _is_cashier_only(current_user):
        session = till.redact_for_cashier(session)
    # ONE SHARED DRAWER PER STORE: a second open for the same store/day returns the
    # EXISTING shared session (already_open=True) rather than a phantom second drawer.
    return {"ok": True, "session": session, "already_open": res.get("already_open", False)}


@router.post("/sessions/{session_id}/blind-submit")
async def submit_blind_count(
    session_id: str,
    body: BlindSubmit,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Submit the blind denomination count. Cashier/manager roles. The server
    computes + STORES expected/variance but a cashier's RESPONSE is redacted (the
    expected figure stays hidden until a manager locks)."""
    if not (_roles(current_user) & _TILL_OPERATE_ROLES):
        raise HTTPException(status_code=403, detail="not permitted to submit a till count")
    db = _get_db()
    # Store-scope BEFORE the write (cross-store IDOR guard).
    session = till.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="till session not found")
    validate_store_access(session.get("store_id"), current_user)

    res = till.blind_submit(
        db,
        session_id,
        blind_denominations=[d.model_dump() for d in body.blind_denominations],
        blind_count_paisa=body.blind_count_paisa,
        cash_payouts_paisa=body.cash_payouts_paisa,
        idempotency_key=body.idempotency_key,
        actor=current_user,
    )
    if not res.get("ok"):
        _raise(res)
    out = res["session"]
    out.pop("_id", None)
    if _is_cashier_only(current_user):
        out = till.redact_for_cashier(out)
    return {"ok": True, "session": out, "idempotent": res.get("idempotent", False)}


@router.post("/sessions/{session_id}/lock")
async def lock_till_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Reveal the variance to the manager and SOFT-LOCK the Z-Read (atomic).
    Manager/area-manager/admin only. The response reveals expected vs counted vs
    variance. A second lock 409s (already_locked)."""
    if not (_roles(current_user) & _TILL_LOCK_ROLES):
        raise HTTPException(status_code=403, detail="not permitted to lock the Z-Read")
    db = _get_db()
    session = till.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="till session not found")
    validate_store_access(session.get("store_id"), current_user)

    res = till.lock_session(db, session_id, actor=current_user)
    if not res.get("ok"):
        _raise(res)
    res["session"].pop("_id", None)
    return {"ok": True, "session": res["session"]}


@router.post("/sessions/{session_id}/reopen")
async def reopen_till_session(
    session_id: str,
    body: ReopenBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Release the transparent soft-lock back to BLIND_SUBMITTED (audited).
    Requires a mandatory reason + a role in the E2 reopen set (the service
    re-checks the role too -- defense in depth). Store-scoped."""
    # Coarse gate matches the default reopen set; the service applies the
    # E2-configured (possibly narrower/wider) set authoritatively.
    if not (_roles(current_user) & _TILL_LOCK_ROLES):
        raise HTTPException(status_code=403, detail="not permitted to reopen a Z-Read")
    db = _get_db()
    session = till.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="till session not found")
    validate_store_access(session.get("store_id"), current_user)

    res = till.reopen_session(db, session_id, reason=body.reason, actor=current_user)
    if not res.get("ok"):
        _raise(res)
    res["session"].pop("_id", None)
    return {"ok": True, "session": res["session"]}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def list_till_sessions(
    store_id: str = Query(..., description="Store to list sessions for"),
    date: Optional[str] = Query(None, description="IST day YYYY-MM-DD"),
    status: Optional[str] = Query(None, description="OPEN / BLIND_SUBMITTED / LOCKED"),
    limit: int = Query(50, ge=1, le=200),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Session history for a store, newest first. Manager/finance roles (the
    expected/variance figures are visible). Store-scoped."""
    if not (_roles(current_user) & _TILL_READ_ROLES):
        raise HTTPException(status_code=403, detail="not permitted to view till sessions")
    scoped = validate_store_access(store_id, current_user)
    if not scoped:
        raise HTTPException(status_code=403, detail="not permitted for this store")
    rows = till.list_sessions(
        _get_db(), store_id=scoped, session_date=date, status=status, limit=limit
    )
    return {"sessions": rows}


@router.get("/sessions/{session_id}")
async def get_till_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """One session. Store-scoped. A cashier-only caller gets a REDACTED view
    (no expected/variance) until a manager locks (blind enforcement)."""
    if not (_roles(current_user) & _TILL_OPERATE_ROLES):
        raise HTTPException(status_code=403, detail="not permitted to view till sessions")
    db = _get_db()
    session = till.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="till session not found")
    validate_store_access(session.get("store_id"), current_user)
    session.pop("_id", None)
    if _is_cashier_only(current_user):
        session = till.redact_for_cashier(session)
    return {"ok": True, "session": session}


@router.get("/sessions/{session_id}/zread")
async def get_zread(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """The full Z-Read report (opening float, sales by tender, expected, counted,
    variance, lock + reopen trail) for print. Manager/finance roles only (it
    reveals the expected figure). Store-scoped."""
    if not (_roles(current_user) & _TILL_READ_ROLES):
        raise HTTPException(status_code=403, detail="not permitted to view the Z-Read")
    db = _get_db()
    session = till.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="till session not found")
    validate_store_access(session.get("store_id"), current_user)
    zread = till.build_zread(db, session_id)
    if not zread.get("ok"):
        _raise(zread)
    return zread
