"""Stock count: record a counted item, complete the session."""

from ._shared import (
    Depends,
    HTTPException,
    Optional,
    _INVENTORY_ROLES,
    can_access_store_scoped,
    datetime,
    logger,
    require_roles,
    router,
)
from .models import (
    CompleteStockCountRequest,
    StockCountItem,
)
from .helpers import (
    _get_db,
)
from .stock_count import (
    _load_open_count,
    _on_hand_now,
    _product_costs,
    _upsert_count_item,
)

@router.post("/stock-count/{count_id}/items")
async def record_count_item(
    count_id: str,
    item: StockCountItem,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Record a counted item in an active stock count session"""
    db = _get_db()

    # Recording a counted quantity is a WRITE. "Item recorded (no DB)" told the
    # counter their number was saved when nothing was saved -- fail loud.
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        count_doc = _load_open_count(db, count_id, current_user)
        items_counted = _upsert_count_item(
            db,
            count_doc,
            product_id=item.product_id,
            product_name=item.product_name or "",
            sku=item.sku or "",
            counted_quantity=item.counted_quantity,
            notes=item.notes,
            user_id=current_user.get("user_id", ""),
        )

        return {
            "message": "Item recorded",
            "count_id": count_id,
            "items_counted": items_counted,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"record_count_item error: {e}")
        raise HTTPException(
            status_code=500, detail="Could not record the counted quantity"
        )


@router.post("/stock-count/{count_id}/complete")
async def complete_stock_count(
    count_id: str,
    request: Optional[CompleteStockCountRequest] = None,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Complete stock count — calculates variances between system and physical count"""
    db = _get_db()

    # Completing is a WRITE that closes the session. Reporting "completed" with
    # no DB behind it is the same lie as reporting a perfect variance.
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        collection = db.get_collection("stock_counts")
        count_doc = collection.find_one({"count_id": count_id})
        if not count_doc:
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if not can_access_store_scoped(count_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Stock count session not found")
        if count_doc.get("status") != "in_progress":
            raise HTTPException(
                status_code=400, detail="Stock count is not in progress"
            )

        # None = the opening snapshot could not be taken (scope unreadable).
        # NOT the same as {} (a shelf with nothing on hand): coverage below
        # must report UNKNOWN for the first and a clean 100% for the second.
        snapshot = count_doc.get("system_quantities", {})
        system_quantities = snapshot or {}
        items = count_doc.get("items", [])

        # A count with no lines is NOT a count. Completing an empty session used
        # to run this whole calculation over an empty list and hand the counter
        # "Variance: 0%" -- a perfect result for a shelf nobody looked at. Every
        # count run in the business reported that. Refuse instead.
        if not items:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nothing has been counted in this session. "
                    "Record at least one counted quantity before completing it."
                ),
            )

        # Calculate variances. A count is only useful if the answer is in
        # rupees as well as units -- "12 units short" means nothing until it
        # reads "12 units short, Rs 24,000".
        costs = _product_costs(db, [i.get("product_id", "") for i in items])
        # What the books say RIGHT NOW, against the snapshot this session
        # opened with. Any line where the two differ has been sold,
        # transferred, received or returned since the counter started, so the
        # difference between snapshot and shelf is not a loss.
        live_on_hand, live_fingerprints = _on_hand_now(
            db, count_doc.get("store_id", ""), [i.get("product_id", "") for i in items]
        )
        opening_fingerprints = count_doc.get("system_unit_fingerprints") or {}

        variances = []
        total_system = 0
        total_counted = 0
        total_shrinkage = 0
        total_overage = 0
        shrinkage_value = 0.0
        overage_value = 0.0
        lines_without_cost = 0
        lines_moved = 0

        for item in items:
            pid = item["product_id"]
            counted = item["counted_quantity"]
            system = system_quantities.get(pid, 0)
            system_now = live_on_hand.get(pid, system)
            # MOVED DURING THE COUNT: stock left or arrived while the session
            # was open. Counting it against the opening snapshot manufactures
            # a shortage out of a sale, and the write-off then destroys a
            # frame the shop still owns and could still sell. Report the line,
            # never bank it as a loss, and never let the write-off take it.
            #
            # Compare the SET of units, not the total. One frame sold at the
            # till and one received into the stockroom net to zero: the totals
            # match, the line looks untouched, and an honest shelf count is
            # written off as shrinkage. Different units on hand IS movement,
            # whatever the totals say.
            opening_print = opening_fingerprints.get(pid)
            units_changed = (
                opening_print is not None
                and live_fingerprints.get(pid) != opening_print
            )
            moved = system_now != system or units_changed
            variance = counted - system
            var_pct = round((variance / max(system, 1)) * 100, 2)
            unit_cost = float(costs.get(pid, 0.0) or 0.0)
            variance_value = round(variance * unit_cost, 2)

            if moved:
                lines_moved += 1
            else:
                total_system += system
                total_counted += counted
                if variance < 0:
                    total_shrinkage += abs(variance)
                    shrinkage_value += abs(variance_value)
                elif variance > 0:
                    total_overage += variance
                    overage_value += variance_value
                if variance != 0 and unit_cost <= 0:
                    # The rupee figure for this line is unknown, not zero.
                    lines_without_cost += 1

            variances.append(
                {
                    "product_id": pid,
                    "product_name": item.get("product_name", ""),
                    "sku": item.get("sku", ""),
                    "system_quantity": system,
                    "system_quantity_now": system_now,
                    "units_changed_during_count": units_changed,
                    "physical_quantity": counted,
                    "variance": variance,
                    "variance_percentage": var_pct,
                    "unit_cost": round(unit_cost, 2),
                    "variance_value": variance_value,
                    "moved_during_count": moved,
                }
            )

        # HOW MUCH OF THE SHELF WAS WALKED. The session has known the full
        # expected set since it opened; nothing ever compared the two, so
        # counting 1 product out of 400 completed as "everything matched" and
        # the stat tile read Rs 0 missing for a shelf nobody looked at. A
        # counter gets interrupted -- that is the lie that actually happens.
        # Coverage is measured against the EXPECTED set only: a line for a
        # product the session never expected is an overage, not coverage.
        # The set comparison itself is blind_stock_take.coverage -- the ONE
        # implementation both counts share (an unreadable snapshot reports
        # coverage UNKNOWN, never a clean 100%).
        from ...services.blind_stock_take import coverage as _coverage
        from ...services.item_events import unknown_status_tokens

        cov = _coverage(
            None if snapshot is None else system_quantities.keys(),
            (i.get("product_id", "") for i in items),
        )
        # The vocabulary tripwire (same as the blind lock): a unit whose
        # status token canonicalises to NOTHING is invisible to the expected
        # set, so the shelf cannot be certified fully counted while any exist.
        unknown_tokens = unknown_status_tokens(db, count_doc.get("store_id", ""))
        if unknown_tokens:
            cov["full_count"] = False
        products_expected = cov["products_expected"]
        products_counted = cov["products_counted"]
        coverage_pct = cov["coverage_percentage"]
        full_count = cov["full_count"]

        # Overall metrics
        overall_var_pct = round(
            ((total_counted - total_system) / max(total_system, 1)) * 100, 2
        )
        shrinkage_pct = round((total_shrinkage / max(total_system, 1)) * 100, 2)

        shrinkage_value = round(shrinkage_value, 2)
        overage_value = round(overage_value, 2)

        now = datetime.utcnow()
        update_data = {
            "status": "completed",
            "completed_at": now.isoformat(),
            "completed_by": current_user.get("user_id", ""),
            "variances": variances,
            "variance_percentage": overall_var_pct,
            "shrinkage_percentage": shrinkage_pct,
            "shrinkage_units": total_shrinkage,
            "shrinkage_value": shrinkage_value,
            "overage_units": total_overage,
            "overage_value": overage_value,
            "lines_without_cost": lines_without_cost,
            "lines_moved_during_count": lines_moved,
            **cov,
            "unknown_status_tokens": unknown_tokens or [],
            "notes": request.notes if request else None,
        }
        collection.update_one({"count_id": count_id}, {"$set": update_data})

        # Variance -> accountable SYSTEM task (fail-soft; deduped per count).
        try:
            from ...services.task_triggers import (
                create_system_task,
                stock_variance_priority,
            )
            from ...dependencies import get_task_repository

            pri = stock_variance_priority(shrinkage_pct, overall_var_pct)
            if pri:
                create_system_task(
                    get_task_repository(),
                    title=f"Stock-count variance: {count_doc.get('audit_number', count_id)}",
                    description=(
                        f"{total_shrinkage} units short (Rs {shrinkage_value:,.2f} at cost), "
                        f"{total_overage} units over (Rs {overage_value:,.2f}) "
                        f"across {len(items)} counted lines. "
                        f"Shrinkage {shrinkage_pct}% / overall variance {overall_var_pct}%. "
                        + (
                            f"{products_counted} of {products_expected} expected "
                            f"products were counted ({coverage_pct}%). "
                            if products_expected is not None
                            else "How much of the shelf this covered is "
                            "unknown (the opening scope could not be read). "
                        )
                        + "Investigate and reconcile."
                    ),
                    priority=pri,
                    category="Inventory",
                    store_id=count_doc.get("store_id")
                    or current_user.get("active_store_id"),
                    dedupe_ref=f"stockcount:{count_id}",
                )
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[INVENTORY] variance task creation skipped: {_e}")

        return {
            "message": "Stock count completed",
            "count_id": count_id,
            "audit_number": count_doc.get("audit_number", ""),
            "items_counted": len(items),
            "variance_percentage": overall_var_pct,
            "shrinkage_percentage": shrinkage_pct,
            "shrinkage_units": total_shrinkage,
            "shrinkage_value": shrinkage_value,
            "overage_units": total_overage,
            "overage_value": overage_value,
            "lines_without_cost": lines_without_cost,
            "lines_moved_during_count": lines_moved,
            "products_expected": products_expected,
            "products_counted": products_counted,
            "products_missed": cov["products_missed"],
            "coverage_percentage": coverage_pct,
            "full_count": full_count,
            "unknown_status_tokens": unknown_tokens or [],
            "variances": variances,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"complete_stock_count error: {e}")
        raise HTTPException(
            status_code=500, detail="Could not complete the stock count"
        )
