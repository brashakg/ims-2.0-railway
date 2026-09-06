"""Maker-checker journal entries, chart of accounts and the journal JV export.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime
from ...utils.ist import now_ist, now_ist_naive, ist_today, fy_start_year_ist
from typing import Optional, List
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from ..auth import get_current_user
from ...services import je_service
from ._shared import (
    _get_db,
    _je_cal_day,
    _parse_range_dt,
    _scope_store,
    check_period_locked,
    router,
)

# ============================================================================
# F17/#25 - Maker-checker manual journal entries (gated by E4 ApprovalEngine)
# ============================================================================
# A maker (ACCOUNTANT/ADMIN/SUPERADMIN) drafts a balanced double-entry voucher,
# submits it (opens an E4 journal_entry approval request), a DIFFERENT-user
# checker (ADMIN/SUPERADMIN) PIN-approves via E4 (E4 hard-blocks self-approval),
# then posts it (consuming the E4 approval EXACTLY once) so it flows into the
# P&L read + nightly Tally journal-voucher export. The maker-checker / PIN /
# single-use logic is the shared E4 engine -- NOT reimplemented here.

# Roles allowed to draft + submit a JE (the maker side).
_JE_MAKER_ROLES = {"ACCOUNTANT", "ADMIN", "SUPERADMIN"}
# Roles allowed to approve / reject / post / reverse a JE (the checker side).
_JE_CHECKER_ROLES = {"ADMIN", "SUPERADMIN"}


def _je_require_enabled() -> None:
    """Feature flag gate for JE WRITE endpoints (off by default)."""
    if not je_service.is_je_enabled():
        raise HTTPException(status_code=503, detail="manual_je_not_enabled")


def _require_roles_for(current_user: dict, allowed: set, msg: str) -> None:
    roles = set(current_user.get("roles") or [])
    if not (roles & allowed):
        raise HTTPException(status_code=403, detail=msg)


def _je_parse_entry_date(s, current_user: dict) -> datetime:
    """Parse the maker-supplied entry_date (YYYY-MM-DD / ISO) to the naive datetime
    at MIDNIGHT of the intended IST CALENDAR day. entry_date is an ACCOUNTING day,
    NOT a created_at instant -- it must NOT be routed through ist_day_start_utc()
    (which maps IST-midnight to 18:30 UTC the PRIOR day, mis-bucketing the
    period-lock month + the Rule-46(b) FY serial + the displayed date, and wrongly
    rejecting a legit 1-April entry at the FY-guard). Defaults to today (IST).
    Non-SUPERADMIN makers cannot back-date before the current financial-year start."""
    if not s:
        _t = ist_today()
        dt = datetime(_t.year, _t.month, _t.day)
    else:
        dt = _je_cal_day(s)
        if dt is None:
            raise HTTPException(status_code=400, detail="invalid_entry_date")
    roles = set(current_user.get("roles") or [])
    if "SUPERADMIN" not in roles:
        fy_year = fy_start_year_ist(now_ist())
        fy_start = datetime(fy_year, 4, 1)
        if dt < fy_start:
            raise HTTPException(
                status_code=400,
                detail="entry_date before current financial year (SUPERADMIN only)",
            )
    return dt


def _je_raise(res: dict) -> dict:
    """Map a je_service {"ok", "http", "error"} result to a response or raise."""
    if res.get("ok"):
        return res
    code = int(res.get("http", 400))
    detail: dict = {"error": res.get("error", "failed")}
    for k in ("status", "remaining", "retry_after_min"):
        if k in res:
            detail[k] = res[k]
    raise HTTPException(status_code=code, detail=detail)


class JeLineBody(BaseModel):
    account_code: str
    debit: float = Field(default=0, ge=0)
    credit: float = Field(default=0, ge=0)
    narration: Optional[str] = None


class JeCreateBody(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    lines: List[JeLineBody]
    store_id: Optional[str] = None
    entity_id: Optional[str] = None
    entry_date: Optional[str] = None
    reference: Optional[str] = None


class JePinBody(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)


class JeRejectBody(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)
    note: str = Field(..., min_length=10, max_length=500)


class CoaUpsertBody(BaseModel):
    account_code: str = Field(..., min_length=1, max_length=20)
    account_name: str = Field(..., min_length=1, max_length=120)
    account_type: str
    allow_manual_je: bool = True
    is_active: bool = True


@router.post("/journal-entries")
async def create_journal_entry(
    body: JeCreateBody,
    current_user: dict = Depends(get_current_user),
):
    """Create a DRAFT journal voucher (maker). Validates a balanced
    debit=credit voucher against the chart of accounts, checks the period lock
    on entry_date, mints an FY-scoped JE number."""
    _je_require_enabled()
    _require_roles_for(
        current_user, _JE_MAKER_ROLES, "Journal entries require ACCOUNTANT / ADMIN"
    )
    db = _get_db()
    store_id = _scope_store(body.store_id, current_user)
    entry_date = _je_parse_entry_date(body.entry_date, current_user)
    # Gate 1: a closed period rejects a draft at creation.
    check_period_locked(db, entry_date.date())
    res = je_service.create_je(
        db,
        store_id=store_id,
        entity_id=body.entity_id,
        entry_date=entry_date,
        description=body.description,
        lines=[ln.model_dump() for ln in body.lines],
        maker_id=current_user.get("user_id"),
        maker_name=current_user.get("name") or current_user.get("full_name"),
        reference=body.reference,
    )
    return _je_raise(res)


@router.get("/journal-entries")
async def list_journal_entries(
    store_id: Optional[str] = None,
    status: Optional[str] = None,
    maker_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """List journal vouchers, store-scoped for store-level roles."""
    db = _get_db()
    store_id = _scope_store(store_id, current_user)
    rows = je_service.list_jes(
        db, store_id=store_id, status=status, maker_id=maker_id, limit=200
    )
    return {"journal_entries": rows, "total": len(rows)}


@router.get("/journal-entries/{je_id}")
async def get_journal_entry(
    je_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Fetch one journal voucher with its full line detail."""
    db = _get_db()
    je = je_service.get_je(db, je_id)
    if not je:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    # Store-scope read: a store-level role cannot read another store's JE.
    if je.get("store_id"):
        _scope_store(je.get("store_id"), current_user)
    else:
        # Store-LESS (HQ/entity-level) voucher: skipping the guard here let any
        # store-scoped finance role read HQ vouchers by id (adversarial P3).
        # HQ vouchers are HQ-readable only.
        roles = set(current_user.get("roles") or [])
        if not (roles & {"SUPERADMIN", "ADMIN"}):
            raise HTTPException(
                status_code=403, detail="HQ journal entries are not store-readable"
            )
    je.pop("_id", None)
    return je_service._jsonable(je)


@router.post("/journal-entries/{je_id}/submit")
async def submit_journal_entry(
    je_id: str,
    current_user: dict = Depends(get_current_user),
):
    """DRAFT -> SUBMITTED (maker only). Opens the E4 maker-checker approval."""
    _je_require_enabled()
    _require_roles_for(
        current_user, _JE_MAKER_ROLES, "Journal entries require ACCOUNTANT / ADMIN"
    )
    db = _get_db()
    res = je_service.submit_je(
        db,
        je_id=je_id,
        maker_id=current_user.get("user_id"),
        maker_roles=list(current_user.get("roles") or []),
        maker_store_ids=list(current_user.get("store_ids") or []),
    )
    return _je_raise(res)


@router.post("/journal-entries/{je_id}/approve")
async def approve_journal_entry(
    je_id: str,
    body: JePinBody,
    current_user: dict = Depends(get_current_user),
):
    """SUBMITTED -> APPROVED (checker, PIN-gated, via E4). The maker cannot
    approve their own entry -- E4 enforces approver != maker."""
    _je_require_enabled()
    _require_roles_for(
        current_user,
        _JE_CHECKER_ROLES,
        "Only ADMIN / SUPERADMIN may approve a journal entry",
    )
    db = _get_db()
    res = je_service.approve_je(
        db,
        je_id=je_id,
        approver_id=current_user.get("user_id"),
        approver_roles=list(current_user.get("roles") or []),
        pin=body.pin,
        approver_store_ids=list(current_user.get("store_ids") or []),
    )
    return _je_raise(res)


@router.post("/journal-entries/{je_id}/reject")
async def reject_journal_entry(
    je_id: str,
    body: JeRejectBody,
    current_user: dict = Depends(get_current_user),
):
    """SUBMITTED -> REJECTED with a mandatory note (checker, PIN-gated, via E4)."""
    _je_require_enabled()
    _require_roles_for(
        current_user,
        _JE_CHECKER_ROLES,
        "Only ADMIN / SUPERADMIN may reject a journal entry",
    )
    db = _get_db()
    res = je_service.reject_je(
        db,
        je_id=je_id,
        approver_id=current_user.get("user_id"),
        approver_roles=list(current_user.get("roles") or []),
        pin=body.pin,
        note=body.note,
        approver_store_ids=list(current_user.get("store_ids") or []),
    )
    return _je_raise(res)


@router.post("/journal-entries/{je_id}/post")
async def post_journal_entry(
    je_id: str,
    current_user: dict = Depends(get_current_user),
):
    """APPROVED -> POSTED (checker). Consumes the E4 approval EXACTLY once, then
    re-checks the period lock (double gate) before the JE hits the ledger."""
    _je_require_enabled()
    _require_roles_for(
        current_user,
        _JE_CHECKER_ROLES,
        "Only ADMIN / SUPERADMIN may post a journal entry",
    )
    db = _get_db()
    je = je_service.get_je(db, je_id)
    if not je:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    # Gate 2 (double period-lock): a period locked after approval blocks posting.
    entry_date = je.get("entry_date")
    if isinstance(entry_date, datetime):
        check_period_locked(db, entry_date.date())
    res = je_service.post_je(db, je_id=je_id, poster_id=current_user.get("user_id"))
    return _je_raise(res)


@router.post("/journal-entries/{je_id}/reverse")
async def reverse_journal_entry(
    je_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Reverse a POSTED JE (ADMIN/SUPERADMIN). Mints a mirror voucher dated
    today (today's period must be open); both vouchers are linked."""
    _je_require_enabled()
    _require_roles_for(
        current_user,
        _JE_CHECKER_ROLES,
        "Only ADMIN / SUPERADMIN may reverse a journal entry",
    )
    db = _get_db()
    # The reversal posts on today's date -- today's period must be open.
    check_period_locked(db, now_ist_naive().date())
    res = je_service.reverse_je(
        db,
        je_id=je_id,
        actor_id=current_user.get("user_id"),
        actor_name=current_user.get("name") or current_user.get("full_name"),
    )
    return _je_raise(res)


@router.get("/chart-of-accounts")
async def get_chart_of_accounts(
    manual_only: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Active chart of accounts. ``manual_only=true`` returns only accounts the
    JE line picker may use (allow_manual_je=True)."""
    db = _get_db()
    return {"accounts": je_service.list_accounts(db, manual_only=manual_only)}


@router.post("/chart-of-accounts")
async def upsert_chart_of_account(
    body: CoaUpsertBody,
    current_user: dict = Depends(get_current_user),
):
    """Upsert a chart-of-accounts entry (SUPERADMIN only)."""
    roles = set(current_user.get("roles") or [])
    if "SUPERADMIN" not in roles:
        raise HTTPException(
            status_code=403, detail="Only SUPERADMIN may edit the chart of accounts"
        )
    db = _get_db()
    res = je_service.upsert_account(
        db,
        account_code=body.account_code,
        account_name=body.account_name,
        account_type=body.account_type,
        allow_manual_je=body.allow_manual_je,
        is_active=body.is_active,
    )
    if not res.get("ok"):
        err = res.get("error")
        code = 503 if err == "no_db" else 400
        raise HTTPException(status_code=code, detail=err or "upsert_failed")
    return res


@router.get("/tally/journal-jv")
async def get_tally_journal_jv(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """POSTED journal vouchers as Tally ``<JOURNALVOUCHER>`` import XML. DRAFT /
    SUBMITTED / APPROVED JEs are never exported."""
    db = _get_db()
    store_id = _scope_store(store_id, current_user)
    from_dt = _parse_range_dt(from_date)
    to_dt = _parse_range_dt(to_date, end=True)
    rows = je_service.list_jes(
        db, store_id=store_id, status=je_service.STATUS_POSTED, limit=1000
    )
    # Date-filter in Python (entry_date is a datetime); keeps the service query simple.
    filtered = []
    for je in rows:
        ed = je.get("entry_date")
        if isinstance(ed, str):
            ed = _parse_range_dt(ed)
        if from_dt is not None and ed is not None and ed < from_dt:
            continue
        if to_dt is not None and ed is not None and ed > to_dt:
            continue
        filtered.append(je)
    xml = je_service.build_journal_voucher_xml(filtered)
    fname = f"journal_jv_{(from_date or 'all')[:10]}_{(to_date or 'all')[:10]}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
