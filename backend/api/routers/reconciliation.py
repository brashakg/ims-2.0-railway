"""
IMS 2.0 - E5 Tender reconciliation router
=========================================
HTTP surface for the tender-routing + day-close reconciliation engine in
``api/services/tender_reconciliation.py`` (which reads ``order.payments[]`` --
POS capture is UNCHANGED). Mounted at /api/v1/finance behind the finance
role gate (_FINANCE_ROLES) in api/main.py; this layer narrows map-write + lock
to the packet's tighter role sets inline.

Routes (mirrors rbac_policy POLICY):
  GET  /finance/tender-ledger-map            ADMIN/ACCOUNTANT/AREA_MANAGER/STORE_MANAGER(+SUPERADMIN)
  PUT  /finance/tender-ledger-map            global: SUPERADMIN/ADMIN ; store/entity: +ACCOUNTANT
  GET  /finance/reconciliation/by-mode       reads (store-scoped to own store)
  POST /finance/reconciliation/snapshot      SUPERADMIN/ADMIN/ACCOUNTANT/STORE_MANAGER (own store)
  POST /finance/reconciliation/{id}/lock     SUPERADMIN/ADMIN/ACCOUNTANT (atomic + immutable)

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import tender_reconciliation as tr
from ..services.tender_routing import IMS_DEFAULT_LEDGERS, canonicalize_tender

router = APIRouter(tags=["finance"])


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return set(user.get("roles", []) or [])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TenderLedgerSet(BaseModel):
    scope: str = Field("GLOBAL", description="GLOBAL | ENTITY:<id> | STORE:<id>")
    tender: str
    ledger: str
    # E4 maker-checker: an APPROVED approval token (forward-compat for a
    # locked-period map change). Optional; not required on a normal write.
    approval_token: Optional[str] = None


class SnapshotCreate(BaseModel):
    store_id: str
    date: Optional[str] = Field(None, description="IST day (YYYY-MM-DD); defaults to today")


class LockBody(BaseModel):
    # E4 maker-checker token, forward-compat for re-lock attempts. A first lock
    # of an OPEN snapshot does not require it.
    approval_token: Optional[str] = None


# ---------------------------------------------------------------------------
# Tender ledger map
# ---------------------------------------------------------------------------


@router.get("/tender-ledger-map")
async def get_tender_ledger_map(
    scope: Optional[str] = Query(None, description="GLOBAL | ENTITY:<id> | STORE:<id>"),
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """The EFFECTIVE tender->ledger map for a scope, each row tagged with its
    inheritance source (store/entity/global/default). ``scope`` is a convenience
    that derives store_id/entity_id; explicit ``store_id``/``entity_id`` win."""
    db = _get_db()
    sid, eid = store_id, entity_id
    if scope and not sid and not eid:
        if scope.upper().startswith("STORE:"):
            sid = scope.split(":", 1)[1]
        elif scope.upper().startswith("ENTITY:"):
            eid = scope.split(":", 1)[1]
    # Store-scoped read for non-HQ roles.
    if sid:
        sid = validate_store_access(sid, current_user) or sid
    rows = tr.get_effective_tender_map_with_sources(db, store_id=sid, entity_id=eid)
    return {
        "scope": scope or ("STORE:" + sid if sid else ("ENTITY:" + eid if eid else "GLOBAL")),
        "ledgers": rows,
        "defaults": IMS_DEFAULT_LEDGERS,
    }


@router.put("/tender-ledger-map")
async def put_tender_ledger_map(
    body: TenderLedgerSet,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Set ONE tender->ledger override at a scope. Global/entity writes are
    SUPERADMIN/ADMIN; a store-scope write also allows ACCOUNTANT (own store)."""
    roles = _roles(current_user)
    scope = (body.scope or "GLOBAL").strip()
    level = scope.split(":", 1)[0].upper()

    is_hq = bool(roles & {"SUPERADMIN", "ADMIN"})
    if level in ("GLOBAL", "ENTITY"):
        if not is_hq:
            raise HTTPException(status_code=403, detail="Global/entity ledger map is SUPERADMIN/ADMIN only")
    elif level == "STORE":
        if not (is_hq or ("ACCOUNTANT" in roles) or ("AREA_MANAGER" in roles) or ("STORE_MANAGER" in roles)):
            raise HTTPException(status_code=403, detail="not permitted to write the store ledger map")
        sid = scope.split(":", 1)[1] if ":" in scope else ""
        # Store-scope ownership for non-HQ roles.
        scoped = validate_store_access(sid, current_user)
        if not is_hq and not scoped:
            raise HTTPException(status_code=403, detail="not permitted to write policy for this store")
    else:
        raise HTTPException(status_code=400, detail="scope must be GLOBAL, ENTITY:<id>, or STORE:<id>")

    res = tr.set_tender_ledger(
        db=_get_db(),
        scope=scope,
        tender=body.tender,
        ledger=body.ledger,
        actor=current_user,
    )
    if not res.get("ok"):
        err = res.get("error", "write_failed")
        code = {"no_db": 503, "unknown_tender": 400, "empty_ledger": 400}.get(err, 400)
        raise HTTPException(status_code=code, detail=err)
    return res


# ---------------------------------------------------------------------------
# Reconciliation by-mode + snapshot + lock
# ---------------------------------------------------------------------------


@router.get("/reconciliation/by-mode")
async def get_reconciliation_by_mode(
    store_id: str = Query(...),
    date: Optional[str] = Query(None, description="IST day (YYYY-MM-DD); defaults to today"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """By-mode (per canonical tender) reconciliation over the IST day for a
    store. READS order.payments[] -- nothing is written. Store-scoped for
    non-HQ roles."""
    db = _get_db()
    scoped = validate_store_access(store_id, current_user)
    if not scoped:
        raise HTTPException(status_code=403, detail="not permitted for this store")
    start, end = _ist_day_window(date)
    return tr.reconcile_window(db, scoped, start, end)


@router.post("/reconciliation/snapshot")
async def create_reconciliation_snapshot(
    body: SnapshotCreate,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Create-or-refresh the OPEN daily snapshot doc for a store/IST day.
    SUPERADMIN/ADMIN/ACCOUNTANT/STORE_MANAGER (own store). An already-LOCKED day
    returns the locked snapshot unchanged."""
    roles = _roles(current_user)
    if not (roles & {"SUPERADMIN", "ADMIN", "ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"}):
        raise HTTPException(status_code=403, detail="not permitted to snapshot reconciliation")
    db = _get_db()
    scoped = validate_store_access(body.store_id, current_user)
    if not scoped:
        raise HTTPException(status_code=403, detail="not permitted for this store")
    start, end = _ist_day_window(body.date)
    return tr.build_reconciliation_snapshot(db, scoped, start, end, actor=current_user)


@router.post("/reconciliation/{snapshot_id}/lock")
async def lock_reconciliation_snapshot(
    snapshot_id: str,
    body: LockBody = LockBody(),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Lock a daily snapshot (atomic, immutable thereafter).
    SUPERADMIN/ADMIN/ACCOUNTANT. The lock is one guarded find_one_and_update;
    a second lock attempt 409s (already_locked)."""
    roles = _roles(current_user)
    if not (roles & {"SUPERADMIN", "ADMIN", "ACCOUNTANT"}):
        raise HTTPException(status_code=403, detail="not permitted to lock reconciliation")
    res = tr.lock_reconciliation(_get_db(), snapshot_id, actor=current_user)
    if not res.get("ok"):
        raise HTTPException(status_code=int(res.get("http", 400)), detail=res.get("error", "lock_failed"))
    return res


# ---------------------------------------------------------------------------
# IST day-window helper (reuses the shared IST clock)
# ---------------------------------------------------------------------------


def _ist_day_window(date_str: Optional[str]):
    """Return (window_start, window_end) as NAIVE-UTC instants bounding one IST
    calendar day. created_at is stored naive-UTC, so the IST-day boundary must be
    expressed in the same frame (ist_day_start_utc)."""
    from datetime import timedelta
    from ..utils.ist import ist_day_start_utc, ist_today
    from datetime import date as _date

    if date_str:
        try:
            d = _date.fromisoformat(str(date_str)[:10])
        except (ValueError, TypeError):
            d = ist_today()
    else:
        d = ist_today()
    start = ist_day_start_utc(d)
    end = start + timedelta(days=1)
    return start, end
