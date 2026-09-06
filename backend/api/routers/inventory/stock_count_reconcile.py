"""Stock count: fetch a session, reconcile it, finish a stuck write-off."""

from ._shared import (
    BaseModel,
    Depends,
    Dict,
    HTTPException,
    List,
    Optional,
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
from .stock_count import (
    _expected_lines,
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
        # The sheet the counter works from: what this session expects to find,
        # so a style whose last unit has walked (no label left to scan) still
        # has a line to write a zero against. While the session is OPEN the
        # lines carry NO system_quantity -- the count is blind until submitted.
        count_doc["expected_lines"] = _expected_lines(db, count_doc)
        return _withhold_expected_while_open(count_doc)
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


class ReconcileStockCountRequest(BaseModel):
    notes: Optional[str] = None
    # Per-item overrides: the reviewer can accept a different final quantity
    # for specific items before writing.  If not supplied, the counted_quantity
    # from the completed count is used.
    overrides: Optional[List[Dict]] = None  # [{product_id, accepted_quantity}]


@router.post("/stock-count/{count_id}/reconcile")
async def reconcile_stock_count(
    count_id: str,
    request: Optional[ReconcileStockCountRequest] = None,
    # OWNER RULING 2026-08-25 (#8): a stock write-off is ADMIN / SUPERADMIN
    # ONLY, at EVERY value -- no store-manager write-offs and therefore no
    # rupee threshold. A store manager may COUNT (the doors above stay on
    # _INVENTORY_ROLES); destroying stock off the books is not theirs.
    # SUPERADMIN auto-passes inside require_roles.
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Write off what a completed cycle-count found missing (INV-8).

    For each variance line:
    - Negative variance (shrinkage): the oldest still-AVAILABLE units are
      VOIDed and a valued row is written to ``stock_shrinkage`` for audit. A
      unit that moved during the count (sold, transferred, returned) is never
      taken -- the conditional write loses that race on purpose and the
      shortfall is reported as ``units_not_voided``.
    - Positive variance (overage): recorded for review but NOT silently inflated
      (SYSTEM_INTENT: fail loudly / never fabricate stock).

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

        # Build the override map BEFORE the claim, so a refused override
        # leaves the count exactly where it was instead of parking it in
        # "reconciling" with no way back.
        #
        # BOUND: an override may never accept LESS than was counted. Nothing
        # bounded these, so accepted_quantity 0 on a count that found nothing
        # missing voided the entire shelf. Accepting MORE than was counted is
        # the legitimate direction (a recount found more) and writes off less.
        counted_by_product = {
            v.get("product_id"): int(v.get("physical_quantity", 0) or 0)
            for v in variances
        }
        override_map: Dict[str, int] = {}
        if request and request.overrides:
            for ov in request.overrides:
                pid = ov.get("product_id") or ""
                qty = ov.get("accepted_quantity")
                if not pid or qty is None:
                    continue
                accepted = max(0, int(qty))
                if pid not in counted_by_product:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Cannot accept a quantity for a product this count "
                            f"never recorded ({pid})"
                        ),
                    )
                if accepted < counted_by_product[pid]:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "An accepted quantity may not be lower than what was "
                            f"counted ({accepted} accepted vs {counted_by_product[pid]} "
                            "counted). That would write off stock the count never "
                            "said was missing -- recount the shelf instead."
                        ),
                    )
                override_map[pid] = accepted

        # CLAIM the count before touching any stock. The status read above is a
        # check-then-act: two clicks of the button (or two managers) both saw
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
        store_id = count_doc.get("store_id", "")
        costs = _product_costs(db, [v.get("product_id", "") for v in variances])
        shrinkage_records = []
        overage_records = []
        reconciled_items = []
        units_voided_total = 0
        units_not_voided_total = 0
        shrinkage_value_total = 0.0
        lines_skipped_moved = 0

        for v in variances:
            pid = v.get("product_id", "")
            system_qty = int(v.get("system_quantity", 0) or 0)
            counted_qty = int(v.get("physical_quantity", 0) or 0)
            accepted_qty = override_map.get(pid, counted_qty)
            net_variance = accepted_qty - system_qty

            # A line whose on-hand moved while the count was open is NOT a
            # loss (completion flagged it). Writing it off would void a frame
            # the shop still owns and can still sell.
            if v.get("moved_during_count"):
                lines_skipped_moved += 1
                reconciled_items.append(
                    {
                        "product_id": pid,
                        "product_name": v.get("product_name", ""),
                        "sku": v.get("sku", ""),
                        "system_quantity": system_qty,
                        "physical_quantity": counted_qty,
                        "accepted_quantity": counted_qty,
                        "net_variance": 0,
                        "skipped_reason": "stock moved during the count - count again",
                    }
                )
                continue

            reconciled_items.append(
                {
                    "product_id": pid,
                    "product_name": v.get("product_name", ""),
                    "sku": v.get("sku", ""),
                    "system_quantity": system_qty,
                    "physical_quantity": counted_qty,
                    "accepted_quantity": accepted_qty,
                    "net_variance": net_variance,
                }
            )

            if net_variance < 0:
                # Shrinkage: write an audit record.
                # Stock units are serialized (one row per unit) so we
                # VOID the excess rows rather than decrementing a counter.
                shrinkage_qty = abs(net_variance)
                unit_cost = float(costs.get(pid, 0.0) or 0.0)

                # Void the oldest AVAILABLE units to reconcile stock.
                #
                # SAFETY: the write is CONDITIONAL on the unit still being
                # AVAILABLE. Reading candidates and then writing them blind is
                # a check-then-act -- POS claims a unit with an atomic
                # find_one_and_update on status=="AVAILABLE", so a frame sold
                # between the read and the write was being overwritten from
                # SOLD to VOID: the sale destroyed, the customer's frame
                # deleted from the books, and the shortfall still "corrected".
                # Losing that race is now the CORRECT outcome -- a unit that
                # moved during the count (sold, transferred, returned) is left
                # exactly where it is and the shortfall is reported honestly
                # instead of being taken out of a live sale.
                #
                # NOT swallowed: this used to sit inside a bare except that
                # logged and carried on, so the endpoint reported a successful
                # write-off over a failed one.
                #
                # ponytail: oldest-first among the units still AVAILABLE. A
                # count records a QUANTITY per product, not which serials were
                # on the shelf, so it cannot know WHICH unit is the missing
                # one -- the quantity is right (that is what gates the sale),
                # but the particular serial voided may not be the one that
                # walked. If a serial-accurate write-off is ever wanted, the
                # count sheet has to record scanned barcodes, not a number.
                candidates = list(
                    stock_coll.find(
                        {
                            "product_id": pid,
                            "store_id": store_id,
                            "status": "AVAILABLE",
                        },
                        sort=[("created_at", 1)],
                        limit=shrinkage_qty,
                    )
                )
                ids_to_void = [c["_id"] for c in candidates if "_id" in c]
                voided = 0
                if ids_to_void:
                    res = stock_coll.update_many(
                        {"_id": {"$in": ids_to_void}, "status": "AVAILABLE"},
                        {
                            "$set": {
                                "status": "VOID",
                                "voided_at": now.isoformat(),
                                "void_reason": f"cycle-count-reconcile:{count_id}",
                            }
                        },
                    )
                    voided = int(getattr(res, "modified_count", 0) or 0)

                not_voided = shrinkage_qty - voided
                units_voided_total += voided
                units_not_voided_total += not_voided
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
                        "shrinkage_quantity": shrinkage_qty,
                        # What the write-off actually did, not what it wanted
                        # to do. A shrinkage row that claims 3 units when 1
                        # could not be voided is the old silent failure.
                        "units_voided": voided,
                        "units_not_voided": not_voided,
                        # A loss with no rupee figure is not a loss anyone can
                        # act on: this is what the row is worth at cost.
                        "unit_cost": round(unit_cost, 2),
                        "shrinkage_value": shrinkage_value,
                        "system_quantity": system_qty,
                        "counted_quantity": counted_qty,
                        "accepted_quantity": accepted_qty,
                        # Whether a human moved the number away from what was
                        # counted, and who. An unstamped override is a loss
                        # nobody owns.
                        "override_applied": pid in override_map
                        and accepted_qty != counted_qty,
                        "overridden_by": (
                            current_user.get("user_id", "")
                            if (pid in override_map and accepted_qty != counted_qty)
                            else None
                        ),
                        "recorded_at": now.isoformat(),
                        "recorded_by": current_user.get("user_id", ""),
                        "notes": request.notes if request else None,
                    }
                )

                reconciled_items[-1]["units_voided"] = voided
                reconciled_items[-1]["units_not_voided"] = not_voided

            elif net_variance > 0:
                # Overage: record for investigation only — do not inflate stock.
                overage_records.append(
                    {
                        "product_id": pid,
                        "product_name": v.get("product_name", ""),
                        "overage_quantity": net_variance,
                    }
                )

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
                    "reconciled_at": now.isoformat(),
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
            "lines_skipped_moved": lines_skipped_moved,
            "shrinkage_value_written_off": shrinkage_value_total,
            "overages_pending_review": overage_records,
            "reconciled_at": now.isoformat(),
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
                    "recorded_at": now.isoformat(),
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
                    "reconciled_at": now.isoformat(),
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
            "reconciled_at": now.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"finish_stuck_stock_count_reconcile error: {e}")
        raise HTTPException(
            status_code=500, detail="Could not finish the stuck write-off"
        )
