"""Defective quarantine (F21 -- the E3-shim)."""

from ._shared import (
    Depends,
    Dict,
    HTTPException,
    List,
    Optional,
    Query,
    STOCK_STATUS_QUARANTINED,
    _QUARANTINE_REASONS,
    can_access_store_scoped,
    datetime,
    get_audit_repository,
    get_stock_repository,
    logger,
    require_roles,
    resolve_store_scope,
    router,
    timedelta,
    timezone,
)
from .models import (
    LiftQuarantineRequest,
    QuarantineRequest,
)
from .helpers import (
    _get_db,
)
from .accountability import (
    _STOCK_MANAGER_ROLES,
)

# ============================================================================
# DEFECTIVE QUARANTINE  (F21 -- the E3-shim)
# ============================================================================
# A store manager pulls a physically defective / damaged unit off the sellable
# floor by flipping its free-string status to QUARANTINED. Because every on-hand
# / sellable rollup in this module (and product_repository.find_available, and
# transfers' ship-move) uses an explicit AVAILABLE/RESERVED allowlist, a
# QUARANTINED unit is excluded from POS sale, transfers and blind-count purely
# by not being in any allowlist -- no rollup edit is needed. Each status
# transition writes ONE hash-chained audit row via AuditRepository.create (never
# append_audit_entry directly) and dispatches a fail-soft stock.quarantined
# event. Standalone Mongo: every write is a single-document op (no transactions).


def _now_ist():
    """IST-stamped datetime for quarantine records (India-time forensic trail).
    Falls back to naive local only if the IST helper is unavailable."""
    try:
        from api.utils.ist import now_ist

        return now_ist()
    except Exception:  # noqa: BLE001
        return datetime.now()


def _quarantine_audit(action: str, stock_id: str, store_id, user_id,
                      before_state: Dict, after_state: Dict, detail: Dict) -> None:
    """Write one hash-chained STOCK_UNIT audit row. Fail-soft: an audit hiccup
    never undoes the business write that triggered it."""
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": action,
                    "entity_type": "STOCK_UNIT",
                    "entity_id": stock_id,
                    "store_id": store_id,
                    "user_id": user_id,
                    "before_state": before_state,
                    "after_state": after_state,
                    "detail": detail,
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] quarantine audit failed: %s", e)


@router.patch("/stock/{stock_id}/quarantine")
async def quarantine_stock_unit(
    stock_id: str,
    req: QuarantineRequest,
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """Mark a physical stock unit QUARANTINED (defective -- pull off the floor).

    Guards: the unit must exist, be store-accessible to the caller, and be in an
    eligible status (AVAILABLE or DAMAGED -- NOT SOLD/TRANSFERRED/QUARANTINED).
    Writes the free-string QUARANTINED status + quarantine metadata, audits the
    transition, and dispatches a fail-soft stock.quarantined event so TASKMASTER
    can chase an RTV later. No accounting entry (per F21 owner decision).
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        raise HTTPException(status_code=503, detail="Stock repository unavailable")

    unit = stock_repo.find_by_id(stock_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Stock unit not found")

    store_id = unit.get("store_id")
    # Existence-hide a cross-store unit (same 404 contract as other IDOR guards).
    if not can_access_store_scoped(store_id, current_user):
        raise HTTPException(status_code=404, detail="Stock unit not found")

    reason = (req.reason or "").strip().upper()
    if reason not in _QUARANTINE_REASONS:
        raise HTTPException(
            status_code=422,
            detail=f"reason must be one of {sorted(_QUARANTINE_REASONS)}",
        )

    current_status = (unit.get("status") or "AVAILABLE").strip().upper()
    if current_status == STOCK_STATUS_QUARANTINED:
        raise HTTPException(
            status_code=409,
            detail={"code": "already_quarantined", "message": "Unit is already quarantined."},
        )
    if current_status not in ("AVAILABLE", "DAMAGED"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_eligible",
                "message": f"A unit in status {current_status} cannot be quarantined.",
            },
        )

    # Period-lock check (audit completeness; fail-soft -- only raises 423 for an
    # explicitly locked accounting month). Quarantine is a physical control, not
    # a financial write, so current-period operations are never gated.
    try:
        db = _get_db()
        if db is not None:
            from ..finance import check_period_locked
            from api.utils.ist import ist_today

            # IST business day, not the UTC box date (00:00-05:30 IST is still
            # "yesterday" in UTC -- the wrong accounting month on the 1st).
            check_period_locked(db, ist_today())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] quarantine period-lock check skipped: %s", e)

    now = _now_ist()
    actor = current_user.get("user_id")
    actor_name = current_user.get("name") or current_user.get("username") or ""

    update = {
        "status": STOCK_STATUS_QUARANTINED,
        "quarantine_reason": reason,
        "quarantine_at": now,
        "quarantine_by": actor,
        "quarantine_by_name": actor_name,
        "quarantine_notes": (req.notes or "")[:200],
        "quarantine_label_printed": False,
    }
    if req.rtv_vendor_id:
        update["rtv_vendor_id"] = req.rtv_vendor_id

    if not stock_repo.update(stock_id, update):
        raise HTTPException(status_code=500, detail="Failed to quarantine stock unit")

    _quarantine_audit(
        "STOCK_QUARANTINED",
        stock_id,
        store_id,
        actor,
        {"status": current_status},
        {"status": STOCK_STATUS_QUARANTINED, "quarantine_reason": reason},
        {"notes": update["quarantine_notes"], "rtv_vendor_id": req.rtv_vendor_id},
    )

    # E3w: converge this legacy F21 write-path onto the item-event ledger. The
    # status write + audit above already succeeded; this is a PURELY ADDITIVE
    # ledger row (no CAS, no projection) recording the AVAILABLE/DAMAGED ->
    # QUARANTINED transition so /items/{id}/events sees it. Fail-soft: any error
    # is logged and swallowed -- it can never undo the quarantine just written.
    try:
        from ...services import item_events as ie

        db_le = _get_db()
        if db_le is not None:
            frm = ie.canonical_state(current_status) or current_status
            ie.record_post_write_event(
                db_le,
                event_type=ie.ItemEventType.QUARANTINE_IN,
                actor_id=actor,
                stock_id=stock_id,
                from_state=frm,
                to_state=ie.StockState.QUARANTINED,
                store_id=store_id,
                product_id=unit.get("product_id"),
                source_type="F21",
                payload={"quarantine_reason": reason,
                         "notes": update["quarantine_notes"],
                         "rtv_vendor_id": req.rtv_vendor_id},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] quarantine ledger emit failed: %s", e)

    # Event bus (fail-soft): lets TASKMASTER raise an RTV follow-up after 7 days.
    try:
        from agents.registry import dispatch_event

        await dispatch_event(
            "stock.quarantined",
            {
                "stock_id": stock_id,
                "store_id": store_id,
                "reason": reason,
                "actor_id": actor,
            },
            source="inventory_router",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] stock.quarantined dispatch failed: %s", e)

    updated = stock_repo.find_by_id(stock_id) or {**unit, **update}
    return {"stock_unit": updated, "message": "Stock unit quarantined"}


@router.patch("/stock/{stock_id}/lift-quarantine")
async def lift_quarantine_stock_unit(
    stock_id: str,
    req: LiftQuarantineRequest,
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """Lift a quarantine (mis-quarantine correction) -- restore to AVAILABLE.

    A mandatory lift_reason (>=5 chars) is recorded in the audit trail. The unit
    must currently be QUARANTINED (409 not_quarantined otherwise). No approval /
    PIN gate -- store-manager self-approval is sufficient (F21 owner decision).
    """
    stock_repo = get_stock_repository()
    if stock_repo is None:
        raise HTTPException(status_code=503, detail="Stock repository unavailable")

    unit = stock_repo.find_by_id(stock_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Stock unit not found")

    store_id = unit.get("store_id")
    if not can_access_store_scoped(store_id, current_user):
        raise HTTPException(status_code=404, detail="Stock unit not found")

    current_status = (unit.get("status") or "").strip().upper()
    if current_status != STOCK_STATUS_QUARANTINED:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_quarantined", "message": "Unit is not quarantined."},
        )

    now = _now_ist()
    actor = current_user.get("user_id")

    update = {
        "status": "AVAILABLE",
        "quarantine_lifted_at": now,
        "quarantine_lifted_by": actor,
        "quarantine_lift_reason": req.lift_reason,
        "quarantine_label_printed": False,
    }
    if not stock_repo.update(stock_id, update):
        raise HTTPException(status_code=500, detail="Failed to lift quarantine")

    _quarantine_audit(
        "QUARANTINE_LIFTED",
        stock_id,
        store_id,
        actor,
        {"status": STOCK_STATUS_QUARANTINED},
        {"status": "AVAILABLE"},
        {"lift_reason": req.lift_reason},
    )

    # E3w: ledger the QUARANTINED -> AVAILABLE release (additive, no CAS) so the
    # two divergent QUARANTINED write-paths both land in item_events. Fail-soft.
    try:
        from ...services import item_events as ie

        db_le = _get_db()
        if db_le is not None:
            ie.record_post_write_event(
                db_le,
                event_type=ie.ItemEventType.QUARANTINE_OUT,
                actor_id=actor,
                stock_id=stock_id,
                from_state=ie.StockState.QUARANTINED,
                to_state=ie.StockState.AVAILABLE,
                store_id=store_id,
                product_id=unit.get("product_id"),
                source_type="F21",
                payload={"disposition": "RESTOCK",
                         "lift_reason": req.lift_reason},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] lift-quarantine ledger emit failed: %s", e)

    updated = stock_repo.find_by_id(stock_id) or {**unit, **update}
    return {"stock_unit": updated, "message": "Quarantine lifted"}


@router.get("/stock/quarantined")
async def list_quarantined_stock(
    store_id: Optional[str] = Query(None),
    rtv_vendor_id: Optional[str] = Query(None),
    label_printed: Optional[bool] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: dict = Depends(
        require_roles(*_STOCK_MANAGER_ROLES, "ACCOUNTANT")
    ),
):
    """The Quarantine Queue: all QUARANTINED units for the caller's store(s).

    Store-scoped: a store-level role only ever sees its OWN store (an explicit
    cross-store ?store_id is 403'd by resolve_store_scope); HQ roles may pass any
    store_id or see all. Each row carries product name/brand/category and the
    quarantine metadata; the summary reports the count of UNLABELED units (the
    ones that still need a red sticker before they can be cleared).
    """
    db = _get_db()
    if db is None:
        return {"items": [], "total": 0, "unlabeled_count": 0}

    # Authorise + resolve the store filter (store-roles pinned to their own).
    scoped_store = resolve_store_scope(store_id, current_user)

    match: Dict = {"status": STOCK_STATUS_QUARANTINED}
    if scoped_store:
        match["store_id"] = scoped_store
    if rtv_vendor_id:
        match["rtv_vendor_id"] = rtv_vendor_id
    if label_printed is not None:
        if label_printed:
            match["quarantine_label_printed"] = True
        else:
            match["quarantine_label_printed"] = {"$ne": True}
    if date_from or date_to:
        # quarantine_at is a tz-aware IST datetime; a raw STRING bound never matches
        # the BSON Date (type bracket) -> the filter was a silent no-op returning [].
        # Coerce to IST-aware datetimes; a date-only date_to covers the whole IST day.
        _IST = timezone(timedelta(hours=5, minutes=30))
        rng: Dict = {}
        try:
            if date_from:
                _f = datetime.fromisoformat(date_from)
                rng["$gte"] = _f.replace(tzinfo=_IST) if _f.tzinfo is None else _f
            if date_to:
                _t = datetime.fromisoformat(date_to)
                if _t.tzinfo is None:
                    if (_t.hour, _t.minute, _t.second) == (0, 0, 0):
                        _t = _t + timedelta(days=1) - timedelta(microseconds=1)
                    _t = _t.replace(tzinfo=_IST)
                rng["$lte"] = _t
        except ValueError:
            raise HTTPException(status_code=422, detail="date_from / date_to must be ISO format (YYYY-MM-DD)")
        match["quarantine_at"] = rng

    items: List[Dict] = []
    unlabeled = 0
    try:
        stock_coll = db.get_collection("stock_units")
        products_coll = db.get_collection("products")
        prod_cache: Dict[str, Dict] = {}
        for row in stock_coll.find(match):
            row.pop("_id", None)
            pid = row.get("product_id")
            prod = prod_cache.get(pid)
            if prod is None and pid:
                prod = products_coll.find_one({"product_id": pid}) or {}
                prod_cache[pid] = prod
            prod = prod or {}
            if not row.get("quarantine_label_printed"):
                unlabeled += 1
            items.append(
                {
                    **row,
                    "product_name": prod.get("name") or prod.get("product_name") or "",
                    "brand": prod.get("brand") or "",
                    "category": prod.get("category") or "",
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] quarantine queue read failed: %s", e)
        return {"items": [], "total": 0, "unlabeled_count": 0}

    # backlog #4: resolve the RTV vendor id -> vendor name for display.
    try:
        from ...services.name_resolver import vendor_name_map

        vmap = vendor_name_map(db, [it.get("rtv_vendor_id") for it in items])
        for it in items:
            vid = it.get("rtv_vendor_id")
            if vid and str(vid) in vmap:
                it["rtv_vendor_name"] = vmap[str(vid)]
    except Exception:  # noqa: BLE001
        pass

    return {"items": items, "total": len(items), "unlabeled_count": unlabeled}
