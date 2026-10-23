"""Stock count: record a counted item, complete the session."""

from api.utils.event_time import utc_event_iso

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
from ...services.cost_mask import mask_cost
from .stock_count import (
    _load_open_count,
    _on_hand_units,
    _product_costs,
    _product_labels,
    _record_typed,
    _unit_fingerprint,
    count_lines,
)

def _none_found(db, count_doc: dict, store_id: str, not_counted: list, *, costs_for) -> list:
    """The styles a whole-scope count found NOTHING of, with their exact
    pieces (see the M6 note in complete_stock_count)."""
    pids = [n["product_id"] for n in not_counted]
    live = _on_hand_units(db, store_id, pids) or {}
    opening_ids = count_doc.get("system_unit_ids")
    prints = count_doc.get("system_unit_fingerprints") or {}
    costs = costs_for(db, pids)
    raw_by_key: dict = {}
    rows = []
    for n in not_counted:
        pid = n["product_id"]
        live_by_str = {str(u): u for u in live.get(pid, [])}
        if opening_ids is not None and pid in opening_ids:
            opening = set(opening_ids.get(pid) or [])
            moved = bool(opening - set(live_by_str))
        else:
            moved = prints.get(pid) != _unit_fingerprint(live.get(pid, []))
            opening = set(live_by_str)
        row = {**n, "unit_cost": round(float(costs.get(pid, 0.0) or 0.0), 2),
               "moved_during_count": moved, "missing_units": [],
               "reserved_not_checked": []}
        if not moved:
            for key in sorted(opening):
                if key in live_by_str:
                    raw_by_key[key] = (live_by_str[key], len(rows))
        rows.append(row)
    if raw_by_key:
        for u in db.get_collection("stock_units").find(
            {"_id": {"$in": [r for r, _ in raw_by_key.values()]}},
            {"_id": 1, "stock_id": 1, "barcode": 1, "status": 1,
             "created_at": 1, "received_at": 1},
        ):
            _raw, idx = raw_by_key[str(u["_id"])]
            piece = {"unit_id": str(u["_id"]), "stock_id": u.get("stock_id"),
                     "barcode": u.get("barcode") or "",
                     "arrived_at": u.get("received_at") or u.get("created_at")}
            if str(u.get("status") or "").strip().upper() == "RESERVED":
                rows[idx]["reserved_not_checked"].append(piece)
            else:
                rows[idx]["missing_units"].append({**piece, "unit_cost": rows[idx]["unit_cost"]})
        for r in rows:
            r["missing_units"].sort(key=lambda m: m["barcode"])
    return rows


@router.post("/stock-count/{count_id}/items")
async def record_count_item(
    count_id: str,
    item: StockCountItem,
    current_user: dict = Depends(require_roles(*_INVENTORY_ROLES)),
):
    """Type a quantity for one style in an open count: the pieces of it that
    have NO label to scan (0 = "none of this style here", so it reads as
    counted, not skipped). It is added to the pieces scanned for the style,
    never in place of them."""
    db = _get_db()

    # Recording a counted quantity is a WRITE. "Item recorded (no DB)" told the
    # counter their number was saved when nothing was saved -- fail loud.
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        count_doc = _load_open_count(db, count_id, current_user)
        name, sku = _product_labels(db, [item.product_id]).get(
            item.product_id, (item.product_name or "", item.sku or "")
        )
        fresh = _record_typed(
            db,
            count_doc,
            product_id=item.product_id,
            product_name=name,
            sku=sku,
            typed_quantity=item.counted_quantity,
            notes=item.notes,
            user_id=current_user.get("user_id", ""),
        )

        return {
            "message": "Item recorded",
            "count_id": count_id,
            "items_counted": fresh.get("items_counted"),
            "pieces_scanned": len(fresh.get("scans") or []),
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
        lines = count_lines(count_doc)
        items = list(lines.values())

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
        pids = [i["product_id"] for i in items]
        costs = _product_costs(db, pids)
        # WHICH units the books say are on hand RIGHT NOW, against the snapshot
        # this session opened with. Any line where the two differ has been
        # sold, transferred, received or returned since the counter started,
        # so the difference between snapshot and shelf is not a loss.
        store_id = count_doc.get("store_id", "")
        live_units = _on_hand_units(db, store_id, pids)
        if live_units is None:
            # Without the shelf as the books see it now, no piece can be named
            # missing and no line can be cleared of movement. Fail loud; the
            # session stays open and can be finished again.
            raise HTTPException(
                status_code=503,
                detail="Could not read the stock right now. Try Finish again.",
            )
        opening_fingerprints = count_doc.get("system_unit_fingerprints") or {}
        # WHICH pieces were on hand when the count opened (sessions opened
        # before this was recorded fall back to the fingerprint alone).
        opening_ids_by_pid = count_doc.get("system_unit_ids")

        variances = []
        total_system = 0
        total_counted = 0
        total_shrinkage = 0
        total_overage = 0
        shrinkage_value = 0.0
        overage_value = 0.0
        lines_without_cost = 0
        lines_moved = 0
        # unit id (as stored) -> the variance line it is missing from; the
        # barcode / arrival date / status is filled in with one read below.
        missing_raw: dict = {}
        extra_barcodes: dict = {}
        added_raw: dict = {}

        for item in items:
            pid = item["product_id"]
            typed = item["typed_quantity"] or 0
            system = system_quantities.get(pid, 0)
            raw_live = live_units.get(pid, [])
            live_by_str = {str(u): u for u in raw_live}
            system_now = len(live_by_str)
            scanned_all = item["scanned_unit_ids"]
            # MOVED DURING THE COUNT: stock LEFT while the session was open
            # (sold, transferred, returned to a supplier). Counting it against
            # the opening snapshot manufactures a shortage out of a sale, and
            # the write-off then destroys a frame the shop still owns. Report
            # the line, never bank it as a loss, never let the write-off take
            # it. Compare the SET of units, never the total: one sale and one
            # receipt net to zero and an honest count would be written off.
            #
            # A piece that ARRIVED during the count (a receipt) does not move
            # the line: it is reported as "added during the count" and kept
            # out of both the expected set and the counted pieces.
            if opening_ids_by_pid is not None and pid in opening_ids_by_pid:
                opening = set(opening_ids_by_pid.get(pid) or [])
                left = opening - set(live_by_str)
                added = [s for s in sorted(live_by_str) if s not in opening]
                moved = bool(left)
                units_changed = bool(left) or bool(added)
            else:
                opening_print = opening_fingerprints.get(pid)
                units_changed = (
                    opening_print is not None
                    and _unit_fingerprint(raw_live) != opening_print
                )
                moved = system_now != system or units_changed
                opening = set(live_by_str)
                added = []
            scanned = set(scanned_all) - set(added)
            counted = len(scanned) + typed
            variance = counted - system
            var_pct = round((variance / max(system, 1)) * 100, 2)
            unit_cost = float(costs.get(pid, 0.0) or 0.0)
            variance_value = round(variance * unit_cost, 2)

            # THE EXACT PIECES. On a line where nothing left, the opening set
            # is still on hand, so "on hand at opening but not scanned" names
            # the missing pieces by unit, and "scanned but not on hand here"
            # names the extra ones. A typed quantity says some pieces had no
            # label: which unscanned ones they were cannot be known, so that
            # line's shortfall is counted but NOT named -- it cannot be
            # written off piece by piece until it is scanned.
            unscanned = [s for s in sorted(opening) if s not in scanned]
            extra_ids = [s for s in scanned_all if s not in live_by_str]
            if typed:
                missing_ids = []
                unidentified = max(0, len(unscanned) - typed)
                typed_extra = max(0, typed - len(unscanned))
            else:
                missing_ids = unscanned
                unidentified = 0
                typed_extra = 0
            line_short = len(missing_ids) + unidentified
            line_over = len(extra_ids) + typed_extra

            if moved:
                lines_moved += 1
                missing_ids, unidentified, extra_ids, added = [], 0, [], []
                line_short = line_over = 0
            else:
                total_system += system
                total_counted += counted
                if (line_short or line_over) and unit_cost <= 0:
                    # The rupee figure for this line is unknown, not zero.
                    lines_without_cost += 1
                for uid in missing_ids:
                    missing_raw[uid] = (live_by_str[uid], len(variances))
                for uid in added:
                    added_raw[uid] = (live_by_str[uid], len(variances), uid in set(scanned_all))
                for uid in extra_ids:
                    idx = scanned_all.index(uid)
                    extra_barcodes[item["scanned_barcodes"][idx]] = len(variances)

            variances.append(
                {
                    "product_id": pid,
                    "product_name": item.get("product_name", ""),
                    "sku": item.get("sku", ""),
                    "system_quantity": system,
                    "system_quantity_now": system_now,
                    "units_changed_during_count": units_changed,
                    "physical_quantity": counted,
                    "scanned_quantity": len(scanned),
                    "typed_quantity": item["typed_quantity"],
                    "variance": variance,
                    "variance_percentage": var_pct,
                    "unit_cost": round(unit_cost, 2),
                    "variance_value": variance_value,
                    "moved_during_count": moved,
                    "shortage_units": line_short,
                    "overage_units": line_over,
                    "missing_units": [],
                    "missing_unidentified": unidentified,
                    "reserved_not_checked": [],
                    "added_during_count": [],
                    "extra_units": [],
                }
            )

        # Name every missing piece (barcode, arrived) -- one read. A piece
        # RESERVED for a customer order is not named missing: it may be in
        # the order drawer, not on the shelf. It is shown as "reserved, not
        # checked" and never written off.
        if missing_raw or added_raw:
            raws = [raw for raw, _ in missing_raw.values()] + [
                raw for raw, _i, _s in added_raw.values()
            ]
            for u in db.get_collection("stock_units").find(
                {"_id": {"$in": raws}},
                {"_id": 1, "stock_id": 1, "barcode": 1, "status": 1,
                 "created_at": 1, "received_at": 1},
            ):
                key = str(u["_id"])
                piece = {
                    "unit_id": key,
                    "stock_id": u.get("stock_id"),
                    "barcode": u.get("barcode") or "",
                    "arrived_at": u.get("received_at") or u.get("created_at"),
                }
                if key in added_raw:
                    _raw, idx, was_scanned = added_raw[key]
                    variances[idx]["added_during_count"].append(
                        {**piece, "scanned": was_scanned}
                    )
                    continue
                _raw, idx = missing_raw[key]
                v = variances[idx]
                if str(u.get("status") or "").strip().upper() == "RESERVED":
                    v["reserved_not_checked"].append(piece)
                    v["shortage_units"] -= 1
                    continue
                v["missing_units"].append({**piece, "unit_cost": v["unit_cost"]})
            for v in variances:
                v["missing_units"].sort(key=lambda m: m["barcode"])
        for v in variances:
            if not v["moved_during_count"]:
                total_shrinkage += v["shortage_units"]
                shrinkage_value += v["shortage_units"] * v["unit_cost"]
                total_overage += v["overage_units"]
                overage_value += v["overage_units"] * v["unit_cost"]
        if extra_barcodes:
            seen = set()
            for u in db.get_collection("stock_units").find(
                {"barcode": {"$in": list(extra_barcodes)}},
                {"_id": 1, "barcode": 1, "store_id": 1, "status": 1},
            ):
                bc = u.get("barcode")
                if bc in seen or bc not in extra_barcodes:
                    continue
                seen.add(bc)
                variances[extra_barcodes[bc]]["extra_units"].append(
                    {
                        "unit_id": str(u["_id"]),
                        "barcode": bc,
                        # Where the books put it: another shop, or sold here.
                        "recorded_store_id": u.get("store_id"),
                        "recorded_status": u.get("status"),
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
        # The styles nobody counted, by name -- "count these now".
        labels = _product_labels(db, cov.get("products_not_counted") or [])
        not_counted = [
            {
                "product_id": pid,
                "product_name": labels.get(pid, ("Unknown", ""))[0],
                "sku": labels.get(pid, ("Unknown", ""))[1],
            }
            for pid in (cov.get("products_not_counted") or [])
        ]
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

        # ONLY A WHOLE-SHOP OR WHOLE-CATEGORY COUNT MAY WRITE OFF. A zone is a
        # label on the count, not a scope: the expected set is still the whole
        # shop (or category), so a "Wall A" count would name every stockroom
        # piece missing. Its shortage is shown, never written off -- recount
        # the whole shop or category for that.
        write_off_allowed = not (count_doc.get("zone") or "").strip()

        # NONE FOUND (owner ruling S1/M6): in a whole-shop or whole-category
        # count, a style nobody scanned or typed had NOTHING found. Its exact
        # pieces are listed apart from the missing list; the admin may write
        # them off only with one more explicit confirmation. Same rules as
        # every missing piece: the style must not have lost stock during the
        # count, a RESERVED piece is never named, and a piece that arrived
        # during the count is not one of them. Never for a zone count.
        none_found = _none_found(
            db, count_doc, store_id, not_counted, costs_for=_product_costs
        ) if write_off_allowed and not_counted else []

        now = datetime.utcnow()
        update_data = {
            "status": "completed",
            "completed_at": utc_event_iso(now),
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
            "not_counted": not_counted,
            "pieces_scanned": len(count_doc.get("scans") or []),
            "write_off_allowed": write_off_allowed,
            "none_found": none_found,
            "unknown_status_tokens": unknown_tokens or [],
            "notes": request.notes if request else None,
        }
        # Close the session ONLY at the revision it was read at: a scan or a
        # typed answer saved mid-Finish bumps `rev`, and would otherwise sit
        # on the session but be missing from every figure above (its piece
        # even listed as missing). A session opened before `rev` existed is
        # closed on "no rev yet" -- any save since would have created it.
        rev = count_doc.get("rev")
        closed = collection.update_one(
            {
                "count_id": count_id,
                "status": "in_progress",
                "rev": rev if rev is not None else {"$exists": False},
            },
            {"$set": update_data},
        )
        if int(getattr(closed, "modified_count", 0) or 0) != 1:
            raise HTTPException(
                status_code=409,
                detail="A piece was saved while finishing. Press Finish again.",
            )

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
                    # Units only: every store role reads the store's open tasks,
                    # and the value at cost is the managers' (owner ruling D7) --
                    # they read it on the count itself.
                    description=(
                        f"{total_shrinkage} units short, {total_overage} units over "
                        f"across {len(items)} counted lines (the value at cost is "
                        f"on the count in Stock Count). "
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

        # WORKSHOP_STAFF may complete a count; the rupee figures go to the roles
        # that see product cost only (D7) -- the units stay for everyone.
        return mask_cost({
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
            "not_counted": not_counted,
            "pieces_scanned": len(count_doc.get("scans") or []),
            "write_off_allowed": write_off_allowed,
            "none_found": none_found,
            "unknown_status_tokens": unknown_tokens or [],
            "variances": variances,
        }, current_user, "product")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"complete_stock_count error: {e}")
        raise HTTPException(
            status_code=500, detail="Could not complete the stock count"
        )
