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
  GET  /finance/tally/tender-receipt-jv      finance roles (mirrors /finance/tally/sales-jv);
                                             DARK unless policy tally.tender_receipt_voucher is ON

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
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
    # Store-scope BEFORE the irreversible lock: load the snapshot + validate the
    # actor owns its store (mirrors the sibling by-mode/snapshot-create routes).
    # Without this an accountant scoped to store A could permanently LOCK store B's
    # daily cash-variance record by id (cross-store IDOR on an irreversible freeze).
    snap = tr.get_snapshot(_get_db(), snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="reconciliation snapshot not found")
    validate_store_access(snap.get("store_id"), current_user)
    res = tr.lock_reconciliation(_get_db(), snapshot_id, actor=current_user)
    if not res.get("ok"):
        raise HTTPException(status_code=int(res.get("http", 400)), detail=res.get("error", "lock_failed"))
    return res


# ---------------------------------------------------------------------------
# E5 wiring: Tally tender Receipt voucher (sibling of /finance/tally/sales-jv)
# ---------------------------------------------------------------------------


def tender_receipt_policy_enabled(
    store_id: Optional[str] = None, entity_id: Optional[str] = None
) -> bool:
    """The E2 policy gate for the tender Receipt voucher. Registry default is
    False -> the feature is DARK on deploy. FAIL-DARK: any read error counts as
    disabled (a broken policy store must never light up a new accounting
    output)."""
    try:
        from ..services import policy_engine

        scope: Dict[str, Any] = {}
        if store_id:
            scope["store_id"] = store_id
        elif entity_id:
            scope["entity_id"] = entity_id
        return bool(
            policy_engine.get_policy("tally.tender_receipt_voucher", scope, default=False)
        )
    except Exception:  # noqa: BLE001
        return False


@router.get("/tally/tender-receipt-jv")
async def get_tally_tender_receipt_jv(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Tally **Receipt**-voucher XML for a period + scope -- the E5 tender legs
    the Sales day-JV is missing. One Receipt voucher per order, legs from the
    merged E5 engine (UPI/CARD -> bank ledgers, gift-voucher/loyalty/credit ->
    liability/receivable, unknown -> Suspense; NEVER folded into Cash), each
    voucher balance-asserted before emit.

    ADDITIVE + DARK BY DEFAULT: gated by policy ``tally.tender_receipt_voucher``
    (registry default False) -> 403 until the owner enables it; the existing
    /finance/tally/sales-jv output is untouched either way. READ-ONLY over
    ``order.payments[]`` -- no stamp, no capture mutation. The order set is
    selected with the SAME filters as the sales-JV (real orders only, same
    created_at range semantics) so the two exports cover identical days."""
    if not tender_receipt_policy_enabled(store_id=store_id, entity_id=entity_id):
        raise HTTPException(
            status_code=403,
            detail="Tender receipt voucher is disabled (policy tally.tender_receipt_voucher)",
        )

    db = _get_db()
    # SAME order selection as finance.get_tally_sales_jv -- never DRAFT/CANCELLED,
    # same datetime-typed created_at range -- so Sales + Receipt vouchers cover
    # the exact same order set.
    from .finance import _REAL_ORDER_STATUS_FILTER, _apply_created_at_range, _store_maps

    match: Dict[str, Any] = {"status": _REAL_ORDER_STATUS_FILTER}
    store_ids = None
    if store_id:
        store_ids = [store_id]
    elif entity_id:
        s2e, _ = _store_maps(db)
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    _apply_created_at_range(match, from_date, to_date)

    # READ-ONLY projection: only the fields the Receipt builder needs.
    orders = list(
        db.get_collection("orders").find(
            match,
            {
                "_id": 0,
                "order_id": 1,
                "store_id": 1,
                "customer_name": 1,
                "created_at": 1,
                "payments": 1,
            },
        )
    )

    # E2-layered tender->ledger map, memoized per store (a multi-store export
    # resolves each order against its own store's overrides).
    _maps: Dict[str, Dict[str, str]] = {}

    def _map_for(sid: Optional[str]) -> Dict[str, str]:
        key = sid or ""
        if key not in _maps:
            _maps[key] = tr.get_effective_tender_map(db, store_id=sid, entity_id=entity_id)
        return _maps[key]

    store_meta = {}
    if store_id:
        s = db.get_collection("stores").find_one({"store_id": store_id}) or {}
        store_meta = {
            "store_id": store_id,
            "store_code": s.get("store_code"),
            "store_name": s.get("store_name"),
        }

    from ..services.tally_tender_receipt import tally_build_tender_receipt_xml

    try:
        xml = tally_build_tender_receipt_xml(orders, _map_for, store_meta)
    except ValueError as exc:
        # assert_voucher_balanced tripped: fail LOUDLY (an unbalanced voucher
        # must never reach Tally) -- surface which voucher, no partial file.
        raise HTTPException(status_code=500, detail=str(exc))
    fname = f"tender_receipt_jv_{(from_date or 'all')[:10]}_{(to_date or 'all')[:10]}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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
