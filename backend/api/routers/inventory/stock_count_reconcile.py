"""Stock count: fetch a session, reconcile it, finish a stuck write-off."""

from api.utils.event_time import utc_event_iso

from ._shared import (
    BaseModel,
    Depends,
    Dict,
    HTTPException,
    List,
    Optional,
    _STOCK_MANAGER_ROLES,
    can_access_store_scoped,
    datetime,
    get_current_user,
    logger,
    require_roles,
    router,
    uuid,
)
from .helpers import (
    _get_db,
)
from ...services.cost_mask import mask_cost
from .stock_count import (
    _on_hand_units,
    _product_costs,
    _withhold_expected_while_open,
)

@router.get("/stock-count/{count_id}")
async def get_stock_count(
    count_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get details of a specific stock count session"""
    db = _get_db()

    if db is None:
        raise HTTPException(status_code=404, detail="Stock count not found")

    try:
        collection = db.get_collection("stock_counts")
        count_doc = collection.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if not can_access_store_scoped(count_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Stock count session not found")
        count_doc.pop("_id", None)
        # While the session is OPEN the counter sees only what has been
        # counted: pieces scanned so far and any typed styles -- no expected
        # quantity, no variance and no list of styles still to find (owner
        # ruling S1: that list IS the missing list). A style whose last piece
        # has no label is found by searching the catalogue, not from a sheet.
        count_doc["pieces_scanned"] = len(count_doc.get("scans") or [])
        # Values at cost: the roles that see product cost only (D7).
        return mask_cost(_withhold_expected_while_open(count_doc), current_user, "product")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"get_stock_count error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")


# INV-8: Guided cycle-count reconcile step
# After completing a count, the manager reviews variances and applies the
# physical counts to the stock ledger.  Negative variances (shrinkage) are
# written to the `stock_shrinkage` audit collection; positive ones (overages)
# are left for manual investigation (we never silently inflate stock).
# The count is transitioned to status="reconciled" so it cannot be
# re-reconciled.  Fail-soft: DB unavailable returns a 503 (not a silent 200)
# because reconciliation is a stock-altering write, not a read.


def _ledger_void(db, unit, count_id, actor_id) -> None:
    """One VOID row in the item-event ledger for a unit the write-off took.
    Additive (the conditional write above already happened); fail-soft."""
    try:
        from ...services import item_events as ie

        ie.record_post_write_event(
            db, event_type=ie.ItemEventType.VOID, actor_id=actor_id,
            stock_id=str(unit.get("stock_id") or unit["_id"]),
            from_state=ie.StockState.AVAILABLE, to_state=ie.StockState.VOID,
            store_id=unit.get("store_id"), product_id=unit.get("product_id"),
            source_type="STOCK_COUNT", source_id=count_id,
            payload={"void_reason": f"cycle-count-reconcile:{count_id}"},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[INVENTORY] write-off ledger row failed: %s", e)


def _refuse_partial_scope(count_doc: dict) -> None:
    """Only a whole-shop or whole-category count may write off. A zone is a
    label, not a scope -- the expected set is still the whole shop or
    category -- so a zone count would name stockroom pieces missing."""
    if count_doc.get("write_off_allowed") is False or (count_doc.get("zone") or "").strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "This was a zone count. Recount the whole shop or category "
                "to write anything off."
            ),
        )


class ReconcileStockCountRequest(BaseModel):
    notes: Optional[str] = None
    # /reconcile/finish only: close a stuck write-off that took NOTHING while
    # pieces are still named missing. Needs `notes` as the reason.
    close_without_write_off: bool = False
    # /reconcile only: ALSO write off the styles the count found NOTHING of
    # (owner ruling M6). The admin's second, explicit confirmation.
    include_none_found: bool = False
    # RETIRED. Overrides accepted a QUANTITY per style; a write-off now takes
    # the exact pieces the count did not find, so a quantity cannot say which
    # pieces to spare. Refused when sent (never silently ignored: ignoring one
    # would write off MORE than the admin asked) -- recount the style instead.
    overrides: Optional[List[Dict]] = None


@router.post("/stock-count/{count_id}/reconcile")
async def reconcile_stock_count(
    count_id: str,
    request: Optional[ReconcileStockCountRequest] = None,
    # OWNER RULING 2026-08-25 (#8): a stock write-off is ADMIN / SUPERADMIN
    # ONLY, at EVERY value -- no store-manager write-offs and therefore no
    # rupee threshold. A store manager may COUNT (the doors above stay on
    # _INVENTORY_ROLES) and SEND a count for write-off (/write-off-request);
    # destroying stock off the books is not theirs.
    # SUPERADMIN auto-passes inside require_roles.
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Write off the EXACT pieces a completed count did not find (INV-8).

    For each variance line:
    - Missing pieces (named by unit at completion): each one is VOIDed by its
      own conditional write and a valued row is written to ``stock_shrinkage``.
      Only a piece that has not moved since the count is taken -- still on
      hand at this shop, still AVAILABLE. One that was sold, transferred or
      reserved since drops out and is reported as ``units_not_voided``.
    - A shortfall the count could not name (a typed quantity: some pieces had
      no label) is NOT written off; it is reported as ``units_not_identified``
      -- scan the style to name the pieces.
    - A line whose stock moved during the count is skipped ("count again").
    - Extra pieces are recorded for review, never added to stock.

    Transitions ``completed`` -> ``reconciling`` (atomically claimed, so the
    same count can never be written off twice) -> ``reconciled``. A failure
    part-way leaves the count visibly stuck in ``reconciling`` rather than
    reporting a write-off that did not happen.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        counts_coll = db.get_collection("stock_counts")
        shrinkage_coll = db.get_collection("stock_shrinkage")
        stock_coll = db.get_collection("stock_units")

        count_doc = counts_coll.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if not can_access_store_scoped(count_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if count_doc.get("status") != "completed":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Only completed counts can be reconciled "
                    f"(current status: {count_doc.get('status')})"
                ),
            )

        variances = count_doc.get("variances", [])
        if not variances:
            raise HTTPException(
                status_code=400,
                detail="No variance data found — complete the count first",
            )
        if request and request.overrides:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A write-off takes the exact pieces the count did not find; "
                    "it no longer accepts quantities. Count the style again instead."
                ),
            )
        _refuse_partial_scope(count_doc)
        if request and request.include_none_found:
            # Styles with nothing found, as lines the loop below treats like
            # any other: same unmoved / AVAILABLE / this-shop rules.
            variances = list(variances) + [
                {
                    "product_id": n.get("product_id"),
                    "product_name": n.get("product_name", ""),
                    "sku": n.get("sku", ""),
                    "system_quantity": len(n.get("missing_units") or []),
                    "physical_quantity": 0,
                    "moved_during_count": n.get("moved_during_count"),
                    "missing_units": n.get("missing_units") or [],
                    "none_found": True,
                }
                for n in (count_doc.get("none_found") or [])
            ]

        store_id = count_doc.get("store_id", "")
        # The pieces on hand at this shop NOW, in their stored id type -- the
        # write below conditions on them. A missing piece no longer in this
        # set has moved since the count and is left alone. Read BEFORE the
        # claim: a failed read must leave the count "completed" (retryable),
        # not parked in "reconciling" with nothing taken.
        on_hand = _on_hand_units(
            db, store_id, [v.get("product_id", "") for v in variances if v.get("missing_units")]
        )
        if on_hand is None:
            raise HTTPException(
                status_code=503, detail="Could not read the stock right now. Try again."
            )

        # CLAIM the count before touching any stock. The status read above is a
        # check-then-act: two clicks of the button (or two admins) both saw
        # "completed" and both wrote off the same shortfall, destroying twice
        # the stock. This flip is atomic, so exactly one caller proceeds.
        claimed = counts_coll.update_one(
            {"count_id": count_id, "status": "completed"},
            {"$set": {"status": "reconciling"}},
        )
        if getattr(claimed, "modified_count", 0) != 1:
            raise HTTPException(
                status_code=409,
                detail="This count is already being written off",
            )

        now = datetime.utcnow()
        costs = _product_costs(db, [v.get("product_id", "") for v in variances])
        shrinkage_records = []
        overage_records = []
        reconciled_items = []
        units_voided_total = 0
        units_not_voided_total = 0
        units_not_identified_total = 0
        shrinkage_value_total = 0.0
        lines_skipped_moved = 0
        voided_pids: list = []

        for v in variances:
            pid = v.get("product_id", "")
            system_qty = int(v.get("system_quantity", 0) or 0)
            counted_qty = int(v.get("physical_quantity", 0) or 0)
            missing = list(v.get("missing_units") or [])
            unidentified = int(v.get("missing_unidentified", 0) or 0)
            extras = list(v.get("extra_units") or [])
            base = {
                "product_id": pid,
                "product_name": v.get("product_name", ""),
                "sku": v.get("sku", ""),
                "system_quantity": system_qty,
                "physical_quantity": counted_qty,
            }

            # A line whose on-hand moved while the count was open is NOT a
            # loss (completion flagged it). Writing it off would void a frame
            # the shop still owns and can still sell.
            if v.get("moved_during_count"):
                lines_skipped_moved += 1
                reconciled_items.append(
                    {**base, "net_variance": 0,
                     "skipped_reason": "stock moved during the count - count again"}
                )
                continue

            item = {**base, "net_variance": counted_qty - system_qty}
            reconciled_items.append(item)
            units_not_identified_total += unidentified
            if unidentified:
                item["units_not_identified"] = unidentified

            if missing:
                unit_cost = float(costs.get(pid, 0.0) or 0.0)
                live = {str(u): u for u in on_hand.get(pid, [])}
                # SAFETY: one CONDITIONAL write per named piece -- still this
                # shop's, still AVAILABLE at the instant of the write. POS
                # claims a unit with an atomic find_one_and_update on
                # status=="AVAILABLE", so a frame sold between the read and the
                # write loses here and is left exactly where it is. NOT
                # swallowed: a failed write fails the write-off loudly.
                #
                # ponytail: "has not moved since the count" is read as "still
                # on hand here and AVAILABLE". A piece sold and returned in
                # between reads as unmoved; add a per-unit event-sequence check
                # if that ever matters.
                voided_ids = []
                for m in missing:
                    raw = live.get(str(m.get("unit_id")))
                    if raw is None:
                        continue
                    res = stock_coll.update_one(
                        {"_id": raw, "store_id": store_id, "status": "AVAILABLE"},
                        {
                            "$set": {
                                "status": "VOID",
                                "voided_at": utc_event_iso(now),
                                "void_reason": f"cycle-count-reconcile:{count_id}",
                            }
                        },
                    )
                    if int(getattr(res, "modified_count", 0) or 0):
                        unit = stock_coll.find_one({"_id": raw}) or {"_id": raw}
                        voided_ids.append(str(unit.get("stock_id") or raw))
                        _ledger_void(db, unit, count_id, current_user.get("user_id", ""))
                voided = len(voided_ids)
                not_voided = len(missing) - voided
                units_voided_total += voided
                units_not_voided_total += not_voided
                if voided and pid not in voided_pids:
                    voided_pids.append(pid)
                shrinkage_value = round(voided * unit_cost, 2)
                shrinkage_value_total += shrinkage_value

                shrinkage_records.append(
                    {
                        "shrinkage_id": str(uuid.uuid4()),
                        "count_id": count_id,
                        "audit_number": count_doc.get("audit_number", ""),
                        "store_id": store_id,
                        "product_id": pid,
                        "product_name": v.get("product_name", ""),
                        "sku": v.get("sku", ""),
                        "shrinkage_quantity": len(missing),
                        # What the write-off actually did, not what it wanted
                        # to do. A shrinkage row that claims 3 units when 1
                        # could not be voided is the old silent failure.
                        "units_voided": voided,
                        "units_not_voided": not_voided,
                        # E3-5: exactly which units this row wrote off, and the
                        # labels of the pieces the count named missing.
                        "voided_stock_ids": voided_ids,
                        "missing_barcodes": [m.get("barcode") for m in missing],
                        # A loss with no rupee figure is not a loss anyone can
                        # act on: this is what the row is worth at cost.
                        "unit_cost": round(unit_cost, 2),
                        "shrinkage_value": shrinkage_value,
                        "system_quantity": system_qty,
                        "counted_quantity": counted_qty,
                        "none_found": bool(v.get("none_found")),
                        "write_off_requested_by": (
                            count_doc.get("write_off_request") or {}
                        ).get("requested_by"),
                        "recorded_at": utc_event_iso(now),
                        "recorded_by": current_user.get("user_id", ""),
                        "notes": request.notes if request else None,
                    }
                )
                item["units_voided"] = voided
                item["units_not_voided"] = not_voided

            over = int(v.get("overage_units", 0) or 0)
            if over > 0:
                # Extra pieces: recorded for investigation only -- never added
                # to stock (SYSTEM_INTENT: fail loudly / never fabricate stock).
                overage_records.append(
                    {
                        "product_id": pid,
                        "product_name": v.get("product_name", ""),
                        "overage_quantity": over,
                        "extra_barcodes": [x.get("barcode") for x in extras],
                    }
                )

        # Per-store online stock (owner ruling 2026-09-06): the written-off
        # units left this shop's shelf (VOID), so its Shopify location goes
        # down now, like a POS sale. Fire-and-forget, fail-soft.
        if voided_pids:
            try:
                from ...services.online_stock_writeback import writeback_after_units_left

                writeback_after_units_left(
                    db, voided_pids, store_id, source="stock_count_writeoff"
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("[INVENTORY] write-off online stock write-back skipped: %s", e)

        # Persist shrinkage audit rows. NOT swallowed -- a lost audit trail for
        # stock we have just destroyed is exactly the failure worth shouting
        # about, and it used to be a log line under a 200. If this does fail,
        # the count stays visibly "reconciling" and every voided unit still
        # carries void_reason="cycle-count-reconcile:{count_id}", so what was
        # destroyed is recoverable from stock_units itself.
        if shrinkage_records:
            shrinkage_coll.insert_many(shrinkage_records)

        shrinkage_value_total = round(shrinkage_value_total, 2)

        # Mark count as reconciled
        counts_coll.update_one(
            {"count_id": count_id},
            {
                "$set": {
                    "status": "reconciled",
                    "reconciled_at": utc_event_iso(now),
                    "reconciled_by": current_user.get("user_id", ""),
                    "reconciled_by_name": current_user.get(
                        "full_name", current_user.get("username", "")
                    ),
                    "reconciliation_notes": request.notes if request else None,
                    "reconciled_items": reconciled_items,
                    "shrinkage_count": len(shrinkage_records),
                    "overage_count": len(overage_records),
                    "units_voided": units_voided_total,
                    "units_not_voided": units_not_voided_total,
                    "units_not_identified": units_not_identified_total,
                    "lines_skipped_moved": lines_skipped_moved,
                    "shrinkage_value_written_off": shrinkage_value_total,
                }
            },
        )

        return {
            "message": "Stock count reconciled",
            "count_id": count_id,
            "audit_number": count_doc.get("audit_number", ""),
            "items_reconciled": len(reconciled_items),
            "shrinkage_lines": len(shrinkage_records),
            "overage_lines": len(overage_records),
            "units_voided": units_voided_total,
            "units_not_voided": units_not_voided_total,
            "units_not_identified": units_not_identified_total,
            "lines_skipped_moved": lines_skipped_moved,
            "shrinkage_value_written_off": shrinkage_value_total,
            "overages_pending_review": overage_records,
            "reconciled_at": utc_event_iso(now),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"reconcile_stock_count error: {e}")
        raise HTTPException(
            status_code=500, detail="Internal error during reconciliation"
        )


@router.post("/stock-count/{count_id}/reconcile/finish")
async def finish_stuck_stock_count_reconcile(
    count_id: str,
    request: Optional[ReconcileStockCountRequest] = None,
    # Same gate as the write-off itself (owner ruling 2026-08-25 #8): every
    # door onto a stock write-off is ADMIN / SUPERADMIN only.
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Close a write-off that destroyed the stock but lost its audit write.

    The write-off voids units first and writes ``stock_shrinkage`` after. If
    that insert fails it 500s on purpose -- but the count was then parked in
    ``reconciling`` forever: reconcile refuses it ("only completed counts"),
    complete refuses it ("not in progress"), and no route reset it.

    Re-running the write-off is NOT the fix: the units are already gone, so a
    retry would void a second set. This finishes the count from what the
    write-off ACTUALLY did -- every unit it took still carries
    ``void_reason="cycle-count-reconcile:{count_id}"`` -- and destroys nothing.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        counts_coll = db.get_collection("stock_counts")
        shrinkage_coll = db.get_collection("stock_shrinkage")
        stock_coll = db.get_collection("stock_units")

        count_doc = counts_coll.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if not can_access_store_scoped(count_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if count_doc.get("status") != "reconciling":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Only a write-off that stopped part-way can be finished "
                    f"(current status: {count_doc.get('status')})"
                ),
            )

        void_reason = f"cycle-count-reconcile:{count_id}"
        voided_by_product: Dict[str, int] = {}
        for unit in stock_coll.find({"void_reason": void_reason}, {"product_id": 1}):
            pid = str(unit.get("product_id") or "")
            if pid:
                voided_by_product[pid] = voided_by_product.get(pid, 0) + 1

        # NOTHING WAS TAKEN, yet pieces are still named missing: closing this
        # as "reconciled" would read as a finished write-off of a loss nobody
        # wrote off. Put it back to "completed" (nothing to rebuild; the
        # write-off can simply be run again) -- unless the admin explicitly
        # closes it without a write-off, with a reason.
        named = sum(len(v.get("missing_units") or []) for v in count_doc.get("variances") or [])
        if not voided_by_product and named:
            reason = ((request.notes if request else None) or "").strip()
            if not (request and request.close_without_write_off):
                counts_coll.update_one(
                    {"count_id": count_id, "status": "reconciling"},
                    {"$set": {"status": "completed"}},
                )
                return {
                    "message": (
                        "Nothing had been written off yet. The count is back to "
                        "waiting for write-off; run it again."
                    ),
                    "count_id": count_id,
                    "status": "completed",
                    "units_voided": 0,
                }
            if not reason:
                raise HTTPException(
                    status_code=400,
                    detail="Give a reason to close this count without a write-off.",
                )

        # ponytail: two admins clicking Finish in the same instant could both
        # pass this read and write one audit row each. No stock is destroyed
        # either way and the loser's status flip 409s; add a claim flip if
        # this ever stops being a rare manual recovery.
        already = {
            r.get("product_id")
            for r in shrinkage_coll.find({"count_id": count_id}, {"product_id": 1})
        }
        variances = {v.get("product_id"): v for v in (count_doc.get("variances") or [])}
        costs = _product_costs(db, list(voided_by_product.keys()))
        now = datetime.utcnow()
        notes = request.notes if request else None

        rows = []
        reconciled_items = []
        units_total = 0
        value_total = 0.0
        for pid, voided in voided_by_product.items():
            v = variances.get(pid) or {}
            unit_cost = float(costs.get(pid, 0.0) or 0.0)
            value = round(voided * unit_cost, 2)
            units_total += voided
            value_total += value
            reconciled_items.append(
                {
                    "product_id": pid,
                    "product_name": v.get("product_name", ""),
                    "sku": v.get("sku", ""),
                    "system_quantity": int(v.get("system_quantity", 0) or 0),
                    "physical_quantity": int(v.get("physical_quantity", 0) or 0),
                    "accepted_quantity": int(v.get("physical_quantity", 0) or 0),
                    "units_voided": voided,
                    "units_not_voided": 0,
                }
            )
            if pid in already:
                continue
            rows.append(
                {
                    "shrinkage_id": str(uuid.uuid4()),
                    "count_id": count_id,
                    "audit_number": count_doc.get("audit_number", ""),
                    "store_id": count_doc.get("store_id", ""),
                    "product_id": pid,
                    "product_name": v.get("product_name", ""),
                    "sku": v.get("sku", ""),
                    "shrinkage_quantity": voided,
                    "units_voided": voided,
                    "units_not_voided": 0,
                    "unit_cost": round(unit_cost, 2),
                    "shrinkage_value": value,
                    "system_quantity": int(v.get("system_quantity", 0) or 0),
                    "accepted_quantity": int(v.get("physical_quantity", 0) or 0),
                    "recorded_at": utc_event_iso(now),
                    "recorded_by": current_user.get("user_id", ""),
                    # Rebuilt from the units themselves after the original
                    # audit write failed -- say so, never pass it off as the
                    # trail written at the time.
                    "recovered": True,
                    "notes": notes,
                }
            )

        if rows:
            shrinkage_coll.insert_many(rows)

        value_total = round(value_total, 2)
        claimed = counts_coll.update_one(
            {"count_id": count_id, "status": "reconciling"},
            {
                "$set": {
                    "status": "reconciled",
                    "reconciled_at": utc_event_iso(now),
                    "reconciled_by": current_user.get("user_id", ""),
                    "reconciled_by_name": current_user.get(
                        "full_name", current_user.get("username", "")
                    ),
                    "reconciliation_notes": notes,
                    "reconciled_items": reconciled_items,
                    "shrinkage_count": len(voided_by_product),
                    "overage_count": 0,
                    "units_voided": units_total,
                    "units_not_voided": 0,
                    "shrinkage_value_written_off": value_total,
                    "recovered_from_stuck_write_off": True,
                    "closed_without_write_off": bool(
                        not voided_by_product and request and request.close_without_write_off
                    ),
                }
            },
        )
        if getattr(claimed, "modified_count", 0) != 1:
            raise HTTPException(status_code=409, detail="This count is no longer stuck")

        return {
            "message": "Stuck write-off finished from what it actually took",
            "count_id": count_id,
            "audit_number": count_doc.get("audit_number", ""),
            "units_voided": units_total,
            "shrinkage_lines": len(voided_by_product),
            "audit_rows_written": len(rows),
            "shrinkage_value_written_off": value_total,
            "reconciled_at": utc_event_iso(now),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"finish_stuck_stock_count_reconcile error: {e}")
        raise HTTPException(
            status_code=500, detail="Could not finish the stuck write-off"
        )


class WriteOffRequest(BaseModel):
    notes: Optional[str] = None


@router.post("/stock-count/{count_id}/write-off-request")
async def request_stock_count_write_off(
    count_id: str,
    request: Optional[WriteOffRequest] = None,
    current_user: dict = Depends(require_roles(*_STOCK_MANAGER_ROLES)),
):
    """SEND a finished count's missing pieces to the admin for write-off.

    Owner ruling 2026-08-25 (#8): only ADMIN / SUPERADMIN write stock off. A
    store manager's door is this request: it marks the count as waiting for
    the admin, and destroys nothing. The admin then writes off (``/reconcile``)
    the exact pieces still unmoved.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    counts_coll = db.get_collection("stock_counts")
    count_doc = counts_coll.find_one({"count_id": count_id})
    if not count_doc or not can_access_store_scoped(count_doc.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Stock count session not found")
    if count_doc.get("status") != "completed":
        raise HTTPException(
            status_code=400, detail="Only a finished count can be sent for write-off"
        )
    _refuse_partial_scope(count_doc)
    pieces = sum(len(v.get("missing_units") or []) for v in count_doc.get("variances") or [])
    # styles with nothing found go to the admin too (owner ruling M6); the
    # admin still confirms them separately.
    pieces += sum(len(n.get("missing_units") or []) for n in count_doc.get("none_found") or [])
    if not pieces:
        raise HTTPException(
            status_code=400, detail="This count found no named missing pieces to write off"
        )
    ask = {
        "requested_by": current_user.get("user_id", ""),
        "requested_by_name": current_user.get(
            "full_name", current_user.get("username", "")
        ),
        "requested_at": utc_event_iso(datetime.utcnow()),
        "pieces": pieces,
        "notes": request.notes if request else None,
    }
    res = counts_coll.update_one(
        {"count_id": count_id, "status": "completed", "write_off_request": None},
        {"$set": {"write_off_request": ask}},
    )
    if int(getattr(res, "modified_count", 0) or 0) != 1:
        raise HTTPException(status_code=409, detail="Already sent for write-off")
    return {"message": "Sent to admin for write-off", "count_id": count_id, **ask}
