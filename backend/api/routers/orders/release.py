"""Stock and lens release primitives and the optimistic status claims used
by confirm / items / delivery / cancel.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from datetime import datetime
from typing import List, Optional
from ...dependencies import get_stock_repository
from ._shared import (
    _get_db,
    logger,
)
from .stock import (
    _legacy_lens_reservation_key,
    _lens_reservation_key,
    _takes_serialized_stock,
)


def _is_unit_tracked(stock_repo, product_id: str, store_id: Optional[str]) -> bool:
    """Does this product have ANY serialized stock_units row at this store?

    Same question the availability assert asks before it will block a sale
    (`if not tracked: continue`). A product that is not unit-tracked can never
    strand a unit, so it must never raise the restock-failure signal.
    Fail-soft TRUE-ish only when we genuinely cannot tell: a lookup error
    returns False so we do not invent an alarm.
    """
    if not product_id or not store_id or stock_repo is None:
        return False
    try:
        return bool(stock_repo.count({"product_id": product_id, "store_id": store_id}))
    except Exception:  # noqa: BLE001
        return False


def _release_line_units(
    order_id: str,
    item_id: str,
    line: dict,
    surviving_lines: Optional[List[dict]] = None,
    store_id: Optional[str] = None,
) -> tuple:
    """Give back the serialized stock of ONE removed DRAFT line.

    Targeting matters (panel must-fix 5). When the line names its own
    `stock_id` -- the barcode the staffer actually scanned -- we release THAT
    EXACT unit. Releasing an arbitrary unit of the same product instead is a
    real counter failure: staff scan the wrong unit of the same frame model,
    remove the line, and now the customer walks out with a serial the system
    shows AVAILABLE (double-sellable) while an identical frame sitting on the
    shelf reads SOLD and 409s at the till. Only a line that never named a unit
    falls back to the product+quantity sweep.

    Skipped entirely for line kinds that never took serialized stock -- decided
    by the SHARED `_takes_serialized_stock` predicate, the same one
    _mark_units_sold and the availability assert use, so the way in and the way
    out can never disagree again.

    Returns (released_ids, failed) so the caller can persist and surface the
    outcome. The cancel door already reports an incomplete restock; this door
    used to swallow it into a log line and answer 200 regardless.
    """
    if not _takes_serialized_stock(line):
        return [], False
    pid = line.get("product_id") or ""
    sid = line.get("stock_id") or ""
    qty = max(int(line.get("quantity") or 1), 1)
    freed: List[str] = []
    incomplete = False
    try:
        stock_repo = get_stock_repository()
        if stock_repo is None or not hasattr(
            stock_repo, "release_sold_units_for_order"
        ):
            return [], False
        # SERIALIZED OVERSELL GUARD: never hand back a serial that a SURVIVING
        # line is still billing. Two lines of the same product, one scanned, the
        # other FIFO -- releasing blind could free the scanned line's unit.
        keep = [
            str(other.get("stock_id"))
            for other in (surviving_lines or [])
            if other.get("stock_id")
        ]
        if sid:
            result = stock_repo.release_sold_units_for_order(
                order_id, stock_id=str(sid), reason="ORDER_LINE_REMOVED"
            )
            freed += list(getattr(result, "released", result) or [])
            incomplete = bool(getattr(result, "incomplete", False)) or incomplete
            # A qty>1 line consumed its NAMED unit once and FIFO-allocated the
            # rest on the way in, so releasing only the named one strands the
            # remainder -- and used to report a clean success while doing it.
            if qty > 1:
                rest = stock_repo.release_sold_units_for_order(
                    order_id,
                    product_id=pid,
                    exclude_stock_ids=keep + [str(sid)],
                    limit=qty - 1,
                    reason="ORDER_LINE_REMOVED",
                )
                freed += list(getattr(rest, "released", rest) or [])
                incomplete = bool(getattr(rest, "incomplete", False)) or incomplete
        else:
            result = stock_repo.release_sold_units_for_order(
                order_id,
                product_id=pid,
                exclude_stock_ids=keep,
                limit=qty,
                reason="ORDER_LINE_REMOVED",
            )
            freed += list(getattr(result, "released", result) or [])
            incomplete = bool(getattr(result, "incomplete", False)) or incomplete
        # GROUND TRUTH beats the return value, exactly as the cancel door does:
        # fewer units back than the line consumed means something is still SOLD
        # against a line that no longer exists.
        #
        # ...but ONLY for a product that is actually unit-tracked at this store.
        # A plain ACCESSORY has no stock_units rows at all, so it releases
        # nothing and is not a failure -- and the availability gate 3,000 lines
        # earlier exempts exactly these products (`if not tracked: continue`).
        # Without this the door cried wolf on every non-tracked line and
        # poisoned the very signal it was built to raise.
        if len(freed) < qty and _is_unit_tracked(stock_repo, pid, store_id):
            incomplete = True
        if incomplete:
            logger.error(
                "[STOCK] item-remove restock INCOMPLETE (order %s item %s) -- "
                "released %d of %d; unit(s) may be stranded SOLD",
                order_id,
                item_id,
                len(freed),
                qty,
            )
        logger.info(
            "[STOCK] item-remove %s/%s reactivated %d unit(s)%s",
            order_id,
            item_id,
            len(freed),
            f" (explicit unit {sid})" if sid else "",
        )
    except Exception as exc:  # noqa: BLE001
        incomplete = True
        logger.error(
            "[STOCK] item-remove restock FAILED (order %s item %s): %s",
            order_id,
            item_id,
            exc,
        )
    return freed, incomplete


def _lens_line_already_committed(order_id: str, keys: List) -> bool:
    """Has the lens for this line already been CUT (committed) under ANY of its
    candidate reservation keys?

    lens_stock_hook.release_for_cancel checks the commit marker under only the
    single key it is passed. The workshop MOUNTED commit writes that marker
    under the POSITIONAL index while reserve/release now key on item_id, so the
    guard misses and an already-mounted cell gets released. We therefore check
    every candidate key here, before any release is attempted.

    Reads through the lens_stock router's own _get_db seam -- the same one the
    hook uses -- so there is exactly one place to stub. Fail-soft: if we cannot
    tell, we do NOT block the release (a stuck reservation is recoverable; the
    normal cancel path must keep working when the audit store is unavailable).
    """
    if not order_id or not keys:
        return False
    source_ids = ["{0}#{1}#commit".format(order_id, k) for k in keys]
    try:
        from ...routers import lens_stock as stock_router

        db = stock_router._get_db()  # noqa: SLF001 -- intentional shared seam
        if db is None:
            return False
        coll = db.get_collection("lens_stock_audit")
        if coll is None:
            return False
        row = coll.find_one({"source_id": {"$in": source_ids}, "action": "commit"})
        return row is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LENS_HOOK] commit-marker lookup failed for %s (%s): %s",
            order_id,
            source_ids,
            exc,
        )
        return False


async def _release_lens_lines(
    lines: List[dict],
    *,
    order_id: str,
    store_id: str,
    user: dict,
    release,
    positions: Optional[List[int]] = None,
) -> List[dict]:
    """Release the lens cell of every line, under BOTH the current key and the
    key the older code may have used.

    An order reserved before the item_id switch holds its cell under
    "{order_id}#{position}" -- origin/main passed `line_index=idx`, i.e. the
    POSITION, and those legacy lines carry an item_id but NO line_index. So the
    legacy key is the line's TRUE POSITION IN THE ORDER, which is why
    `positions` MUST be supplied whenever `lines` is not the whole order.

    Passing a one-element list without `positions` made every caller release
    under position 0: for a legacy order, removing the lens line at position 1
    emitted keys [item_id, 0] while the live reservation sat at key 1 -- the
    removed line's cell was never released (the exact leak this exists to close)
    and the key-0 call could CONSUME the neighbouring line's live reservation,
    leaking that one on the later cancel.

    Both calls are idempotent no-ops when no such reservation exists. Fully
    fail-soft per line.
    """
    seq = list(positions) if positions is not None else list(range(len(lines or [])))
    for pos, line in zip(seq, lines or []):
        keys = [_lens_reservation_key(line, pos)]
        legacy = _legacy_lens_reservation_key(line, pos)
        if legacy is not None:
            keys.append(legacy)
        # COMMIT GUARD ACROSS BOTH KEYS. The workshop MOUNTED commit still
        # writes its audit row under the POSITIONAL index, while reserve/release
        # now key on item_id. release_for_cancel only checks the ONE key it is
        # handed, so the item_id call sails past the "already committed" guard
        # and releases a cell whose lens is ALREADY CUT AND MOUNTED in the
        # customer's frame -- and when another pending order holds a reservation
        # on the same power cell (routine for common powers) the CAS succeeds
        # and silently decrements THAT order's reservation. One physical lens,
        # two customers. Checking every candidate key BEFORE releasing under any
        # of them is the release-side half of the fix.
        if _lens_line_already_committed(order_id, keys):
            logger.info(
                "[LENS_HOOK] order %s line %s already COMMITTED (lens cut) -- "
                "skipping release under keys %s",
                order_id,
                pos,
                keys,
            )
            continue
        for key in keys:
            try:
                await release(
                    order_item=line,
                    order_id=order_id,
                    line_index=key,
                    store_id=store_id,
                    user=user,
                )
            except Exception as rel_exc:  # noqa: BLE001
                logger.warning(
                    "[LENS_HOOK] release failed (order %s line %s key %s): %s",
                    order_id,
                    pos,
                    key,
                    rel_exc,
                )
    return list(lines or [])


def _claim_order_status(
    repo, order_id: str, new_status: str, required_status, user_id, extra=None
) -> bool:
    """Atomically move an order to `new_status` ONLY while its status is still
    one of `required_status`. Returns False when the precondition no longer
    holds. `required_status` may be a single status or any collection of them.

    THE PRECONDITION IS THE POINT. OrderRepository.update_status writes
    update_one({order_id}, ...) filtered on the id ALONE, so every door that
    calls it directly will happily overwrite a status another request just
    claimed. _claim_order_for_cancel wins its claim correctly and these doors
    used to stamp straight over it: the cancel's stock release and loyalty
    clawback both STAND while the order comes back alive, so a frame that is
    physically in the customer's bag reads AVAILABLE and is re-sellable -- and
    on a pooled ONLINE store that is a Shopify oversell.

    Falls back to a plain read-check-update when the collection has no
    find_one_and_update, so a legacy/mock backend still works (that fallback
    enforces the SAME set membership -- it is a narrower window, not an open
    door).
    """
    wanted = (
        [required_status] if isinstance(required_status, str) else list(required_status)
    )
    now = datetime.now()
    payload = {
        "status": new_status,
        "status_updated_at": now,
        "status_updated_by": user_id,
    }
    if extra:
        # Opt-in extra fields ride the SAME atomic write (e.g. the delivery
        # handover_record) so a lost race can never strand them on a doc the
        # claim did not win.
        payload.update(extra)
    # update_status sets this and orders.py surfaces it; folding the write in
    # means we must carry it or silently drop it.
    if new_status == "DELIVERED":
        payload["delivered_at"] = now
    # status_history feeds the CUSTOMER-FACING portal tracking view, the online
    # order mirror and the order detail response. It is part of the claim, not
    # an afterthought.
    history_entry = {
        "status": new_status,
        "timestamp": now.isoformat(),
        "changed_by": user_id or "system",
    }
    coll = getattr(repo, "collection", None)
    updater = getattr(coll, "find_one_and_update", None) if coll is not None else None
    if callable(updater):
        try:
            doc = updater(
                {"order_id": order_id, "status": {"$in": wanted}},
                {"$set": payload, "$push": {"status_history": history_entry}},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ORDERS] atomic %s claim unavailable for %s (%s); falling back",
                new_status,
                order_id,
                exc,
            )
        else:
            # ONE round trip, and NOTHING after it. The previous version won the
            # claim here and then called repo.update_status four lines later --
            # which is update_one({id}, ...) with NO status precondition, the
            # exact primitive this helper's docstring diagnoses as the bug. A
            # cancel committing in that gap was stamped straight back over, and
            # since _claim_order_for_cancel filters $nin [CANCELLED, DELIVERED],
            # both CONFIRMED and READY stayed claimable. Losing that race used to
            # leave the unit SOLD; now that cancel releases it, it leaves a frame
            # in the customer's bag reading AVAILABLE.
            return doc is not None
    # NON-ATOMIC FALLBACK -- only for a collection with no find_one_and_update.
    # Real pymongo has it and so does MockCollection, so this is unreachable in
    # production and in local no-Mongo mode alike; it exists for hand-rolled
    # doubles. We still narrow it as far as the surface allows: a guarded
    # update_one carrying the SAME status precondition, so the window is the
    # read-modify-write of one statement rather than two round trips. Only when
    # even that is missing do we fall back to a bare read-check.
    existing = repo.find_by_id(order_id)
    if not existing or existing.get("status") not in wanted:
        return False
    guarded = getattr(coll, "update_one", None) if coll is not None else None
    if callable(guarded):
        try:
            res = guarded(
                {"order_id": order_id, "status": {"$in": wanted}},
                {"$set": payload, "$push": {"status_history": history_entry}},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ORDERS] guarded %s fallback failed for %s: %s",
                new_status,
                order_id,
                exc,
            )
        else:
            return bool(getattr(res, "modified_count", 0))
    return bool(repo.update_status(order_id, new_status, user_id))


def _claim_order_for_cancel(
    repo, order_id: str, reason: str, current_user: dict
) -> Optional[dict]:
    """Atomically flip ONE order to CANCELLED, only if it is not already
    CANCELLED or DELIVERED. Returns the PRE-IMAGE doc when this caller won the
    claim, else None.

    This is what makes cancel single-shot: the stock reactivation and the
    loyalty clawback must run for exactly one caller, and a check-then-act
    status test cannot promise that under two concurrent cancels.

    Fail-soft: a collection that cannot do find_one_and_update (legacy mock)
    falls back to the previous read-check-update behaviour rather than blocking
    a cancel outright.
    """
    payload = {
        "status": "CANCELLED",
        "cancellation_reason": reason,
        "cancelled_by": current_user.get("user_id"),
        "cancelled_at": datetime.now().isoformat(),
    }
    coll = getattr(repo, "collection", None)
    updater = getattr(coll, "find_one_and_update", None) if coll is not None else None
    if callable(updater):
        try:
            return updater(
                {
                    "order_id": order_id,
                    "status": {"$nin": ["CANCELLED", "DELIVERED"]},
                },
                {"$set": payload},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[ORDERS] atomic cancel claim unavailable for %s (%s); "
                "falling back to read-check-update",
                order_id,
                exc,
            )
    existing = repo.find_by_id(order_id)
    if not existing or existing.get("status") in ("CANCELLED", "DELIVERED"):
        return None
    # GATE ON THE WRITE. base_repository.update swallows exceptions and returns
    # False, so discarding this result meant a FAILED status write still ran the
    # whole stock + loyalty undo and reported the order cancelled -- stock back
    # on the shelf and points clawed for an order still sitting CONFIRMED.
    if not repo.update(order_id, payload):
        logger.error(
            "[ORDERS] cancel status write failed for %s; undo NOT run", order_id
        )
        return None
    return existing
