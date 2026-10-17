"""GRN void and escalate."""

from ._shared import (
    Depends,
    HTTPException,
    Optional,
    Query,
    _RECEIVE_ROLES,
    can_access_store_scoped,
    datetime,
    get_audit_repository,
    get_grn_repository,
    get_purchase_order_repository,
    get_stock_repository,
    logger,
    require_roles,
    router,
)
from .grn import _require_receipt_number
from .grn_accept_lock import (
    _GRN_TERMINAL_ACCEPT_STATUSES,
    _GRN_WRITE_ERROR,
    _claim_grn_for_accept,
    _claimed_receipt,
    _guarded_grn_write,
    _receipt_units,
    _release_grn_accept_claim,
)


_VOIDABLE = ("PENDING", "PARTIALLY_ACCEPTED")


def _hand_back(grn_repo, grn_id: str, claim_token) -> None:
    """Hand the receipt's claim back, then run the release a catalogue save
    lost to it meanwhile (grn_accept.release_finished_meanwhile)."""
    from .grn_accept import release_finished_meanwhile

    _release_grn_accept_claim(grn_repo, grn_id, claim_token)
    release_finished_meanwhile(grn_repo, grn_id)


def _reopen_po_if_nothing_received(grn, current_user=None) -> Optional[str]:
    """A held receipt's accept moved its PO to PARTIALLY_RECEIVED with nothing
    on the shelf. Voided -- by Void, or by 'Not received' for all it held --
    and with no other live receipt on the order and nothing counted received,
    nothing was received: the order goes back to SENT, so it reads true --
    never "part received" with nothing received.

    One GUARDED write: it lands only while the order is still part-received
    with nothing counted received, so an accept of another receipt that
    commits in between (it writes the received counts) is never undone.
    Answers "SENT" when it reopened the order. Fail-soft: the void stands."""
    po_id = (grn or {}).get("po_id")
    if not po_id:
        return None
    reopened = None
    try:
        po_repo = get_purchase_order_repository()
        if po_repo is None:
            return
        po = po_repo.find_by_id(po_id)
        if not po or po.get("status") not in ("PARTIALLY_RECEIVED", "PARTIAL"):
            return
        if _live_receipts(po_id):
            return
        result = po_repo.collection.update_one(
            {
                "po_id": po_id,
                "status": {"$in": ["PARTIALLY_RECEIVED", "PARTIAL"]},
                "total_received_qty": {"$in": [0, None]},
            },
            {"$set": {"status": "SENT", "updated_at": datetime.now()}},
        )
        if not getattr(result, "modified_count", 0):
            return
        # A receipt created meanwhile whose accept HELD its lines writes the
        # same "part received, nothing counted" the guard matches: re-check,
        # and put the order back if one appeared (it is not nothing-received).
        if _live_receipts(po_id):
            po_repo.collection.update_one(
                {"po_id": po_id, "status": "SENT"},
                {"$set": {"status": po.get("status"), "updated_at": datetime.now()}},
            )
            return None
        reopened = "SENT"
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "purchase.po_reopened_after_void",
                    "entity_type": "purchase_order",
                    "entity_id": po_id,
                    "user_id": (current_user or {}).get("user_id"),
                    "detail": {
                        "po_number": po.get("po_number"),
                        "status_was": po.get("status"),
                        "voided_grn": grn.get("grn_number") or grn.get("grn_id"),
                    },
                }
            )
    except Exception:  # noqa: BLE001 - the void stands; the PO status is advisory
        logger.warning(
            "[VENDOR] PO %s: could not reopen after its receipt was voided",
            po_id,
            exc_info=True,
        )
    return reopened


def _live_receipts(po_id) -> list:
    """The order's receipts that are not VOID, read fail-CLOSED
    (po_detail._order_receipts): a read error must never read as 'nothing
    else on the order' and reopen it."""
    from .po_detail import _order_receipts

    return [g for g in _order_receipts(po_id) if g.get("status") != "VOID"]


@router.post("/grn/{grn_id}/void")
async def void_grn(
    grn_id: str, current_user: dict = Depends(require_roles(*_RECEIVE_ROLES))
):
    """Void a goods-receipt note that never put stock on the shelf
    (duplicate/mistake cleanup).

    Two gates, and the second one matters more than it looks. The status is
    the bookkeeping gate: an ACCEPTED GRN has minted stock_units and must be
    corrected through a vendor return, so only a PENDING or a held
    (PARTIALLY_ACCEPTED) one may be voided. Neither status implies "no
    stock": a held receipt minted its catalogued lines, and the accept flow
    flips the status only AFTER the mint loop, so a worker killed mid-accept
    leaves the receipt PENDING with real units already on the shelf. Voiding
    THAT orphans those units (PO receipt math only sums ACCEPTED GRNs) and
    licenses a full re-mint
    under a new grn_id -- which the per-(grn, line, unit) unique index cannot
    catch, because it keys on source_id. So voiding is refused whenever
    stock_units already holds a row for this receipt, and the operator is told
    to accept it again instead (that retry is idempotent).

    Store-scoped like accept (cross-store reads as 404). The receipt row is kept
    (audit trail, numbering continuity) with status VOID -- the accept endpoint
    refuses VOID rows, and PO receipt math only ever sums ACCEPTED GRNs.
    """
    grn_repo = get_grn_repository()
    if grn_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    if not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")
    # A held receipt (PARTIALLY_ACCEPTED) is voidable too: when every line was
    # held it put nothing in stock, and an order with no room left for it
    # refuses its re-accept -- without a void it would wedge the receipt and
    # every cancel on the order. The stock gate below refuses one that did.
    if grn.get("status") not in _VOIDABLE:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a pending or held GRN can be voided. This one is "
                f"{grn.get('status')} -- accepted stock must be corrected via a "
                "vendor return."
            ),
        )

    # The void audit is immutable and the response names the receipt: a
    # receipt still on its PENDING/<id> placeholder is numbered first (a
    # stranded one) or refused until its own request has numbered it (audit
    # F28), so neither ever carries the placeholder.
    grn = _require_receipt_number(grn_repo, grn)

    # MUTUAL EXCLUSION WITH ACCEPT. Void takes the SAME guarded claim the accept
    # path uses, so the two can never interleave. A point-in-time stock gate is
    # not enough on its own: a void landing in the window between an accept's
    # claim and its FIRST mint -- a window spanning the PO fetch, the product
    # lookup, the cost backfill write and the already-minted count -- passes
    # every gate here while the accept is still about to mint. The accept then
    # puts the whole delivery onto a VOID receipt: real sellable units behind a
    # voided doc, PO receipt math permanently short (it sums ACCEPTED GRNs only),
    # and re-accepting impossible because accept refuses a non-PENDING receipt.
    # Taking the claim closes both orderings, not just accept-then-void.
    claim_token = _claim_grn_for_accept(grn_repo, grn_id, current_user.get("user_id"))
    if claim_token is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This goods receipt is being accepted right now, so it cannot "
                "be voided. Wait for that to finish, then refresh -- if it put "
                "stock on the shelf it must be accepted, never voided."
            ),
        )

    try:
        # STOCK GATE. FAILS CLOSED: if we cannot establish that this receipt put
        # nothing on the shelf, we do not void it. Read UNDER the claim, so no
        # accept can be minting while we look.
        stock_repo = get_stock_repository()
        if stock_repo is None and grn.get("status") != "PENDING":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not check whether this goods receipt has already put "
                    "stock on the shelf, so it was not voided. Try again in a "
                    "moment."
                ),
            )
        if stock_repo is not None:
            # Counted by ORIGIN, wherever the units are now: a transfer
            # rewrites their source fields, and a void over moved units let
            # the same goods be logged and received twice.
            try:
                already_minted = _receipt_units(stock_repo, grn)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[VENDOR] GRN %s: could not check for already-minted units "
                    "before voiding (%s) -- refusing the void",
                    grn_id,
                    exc,
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Could not check whether this goods receipt has already "
                        "put stock on the shelf, so it was not voided. Try "
                        "again in a moment."
                    ),
                ) from exc
            if already_minted > 0 and grn.get("status") != "PENDING":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This goods receipt has already put {already_minted} "
                        "unit(s) into stock, so it cannot be voided -- accepted "
                        "stock must be corrected via a vendor return."
                    ),
                )
            if already_minted > 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This goods receipt has already put {already_minted} "
                        "unit(s) into stock -- an earlier acceptance was "
                        "interrupted before it finished. It cannot be voided, "
                        "because that would leave those units on the shelf with "
                        "no receipt behind them. Accept it again to finish "
                        "receiving; the units already received will not be "
                        "counted twice."
                    ),
                )

        _write_under_claim(
            grn_repo,
            grn,
            claim_token,
            {
                "status": "VOID",
                "voided_at": datetime.now().isoformat(),
                "voided_by": current_user.get("user_id"),
            },
            "voided",
        )

        # Fail-soft audit trail (same contract as the other GRN mutations).
        try:
            audit = get_audit_repository()
            if audit is not None:
                audit.create(
                    {
                        "kind": "grn_void",
                        "entity_type": "grn",
                        "entity_id": grn_id,
                        "action": "VOID",
                        "performed_by": current_user.get("user_id"),
                        "details": {
                            "grn_number": grn.get("grn_number"),
                            "po_id": grn.get("po_id"),
                            "store_id": grn.get("store_id"),
                        },
                    }
                )
        except Exception:  # noqa: BLE001 - audit must never block the void
            pass

        try:
            from .grn_accept import _complete_receipt_tasks
            from ._shared import _get_db

            _db = _get_db()
            if _db is not None:
                _complete_receipt_tasks(_db, grn_id, "The receipt was voided.")
        except Exception:  # noqa: BLE001 - a task problem never undoes the void
            pass

        _reopen_po_if_nothing_received(grn, current_user)

        return {
            "message": "GRN voided",
            "grn_id": grn_id,
            "grn_number": grn.get("grn_number"),
            "grn_status": "VOID",
        }
    finally:
        # VOID is terminal, so the claim is always handed back -- on the happy
        # path and on every refusal above.
        _hand_back(grn_repo, grn_id, claim_token)


def _write_under_claim(grn_repo, grn, claim_token, patch, done) -> None:
    """The terminal write of a void or an escalation, under the claim it took;
    `done` names it in the messages ("voided" / "escalated").

    Guarded exactly like the accept path's, because holding the claim is NOT
    the same as writing under it. Each element closes a DIFFERENT measured
    defect; the attribution below was checked by mutation (remove one filter,
    see which probe reopens), because an earlier version of this comment
    credited the wrong filter and would have led the next reader to delete
    the one that is doing real work.

      * status AS READ -- carries BOTH the "no stall, two clerks" shape and
        the parked-count shape. The caller's status test reads the doc
        fetched BEFORE the claim, and the claim itself admits
        PARTIALLY_ACCEPTED, so without this filter a colleague's accept
        landing in between voids a receipt that now holds stock (a receipt
        read PENDING must still be PENDING; one read held, held). Measured
        with the token filter REMOVED: both of those still 409 here, because
        by then the doc is ACCEPTED / PARTIALLY_ACCEPTED and no longer
        matches.

      * accept_lock_token -- its UNIQUE job is the shape where the doc is
        still PENDING when the parked void wakes up, so the status filter
        cannot help: an accept takes the stale claim over, mints every unit,
        and its terminal flip CANNOT be written (see
        _advance_grn_terminal_status -- "the receipt stays in its previous
        status"), then _finalise_grn_accept_metadata clears the token. Doc
        PENDING, stock on the shelf, token gone. Measured with this filter
        removed: 200 {"grn_status": "VOID"} over 24 real units.
        Regression-tested by
        test_a_parked_void_cannot_void_a_receipt_whose_flip_failed.

    And branching on the RESULT is what stops a swallowed write answering a
    green "GRN voided" over a doc that is still PENDING."""
    grn_id = grn.get("grn_id")
    written = _guarded_grn_write(
        grn_repo,
        {
            "grn_id": grn_id,
            "status": grn.get("status"),  # as read: changed meanwhile -> refused
            "accept_lock_token": claim_token,
        },
        {"$set": patch},
    )
    if written is _GRN_WRITE_ERROR:
        # Deliberately does NOT claim "nothing was done": the write may have
        # applied server-side and only its reply been lost. Stock is not at
        # risk either way -- the write moves only the receipt's status.
        raise HTTPException(
            status_code=503,
            detail=(
                "The database did not confirm whether this goods receipt "
                f"was {done}. Refresh to see its current state before trying "
                "again -- no stock was affected."
            ),
        )
    if written is None:
        # Minimal mock with no atomic primitive: plain write, but still
        # check it landed rather than assuming it did.
        if not grn_repo.update(grn_id, patch):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"The goods receipt could not be {done}. Refresh and try "
                    f"again; nothing was {done}."
                ),
            )
        return
    if written:
        return
    try:
        current = grn_repo.find_by_id(grn_id)
    except Exception:  # noqa: BLE001
        current = None
    status_now = (current or {}).get("status") or "unknown"
    logger.error(
        "[VENDOR] GRN %s: the receipt was not %s -- it is now %s "
        "(it changed while this was in flight)",
        grn_id,
        done,
        status_now,
    )
    if status_now in _GRN_TERMINAL_ACCEPT_STATUSES:
        detail = (
            f"This goods receipt is now {status_now} and holds stock, "
            f"so it cannot be {done} -- accepted stock must be corrected "
            "via a vendor return. Refresh to see its current state."
        )
    else:
        detail = (
            f"This goods receipt changed while it was being {done} (it "
            f"is now {status_now}), so nothing was {done}. Refresh and "
            "check its current state before trying again."
        )
    raise HTTPException(status_code=409, detail=detail)


@router.post("/grn/{grn_id}/escalate")
async def escalate_grn(
    grn_id: str,
    note: str = Query(...),
    current_user: dict = Depends(require_roles(*_RECEIVE_ROLES)),
):
    """Escalate a receipt to head office. It leaves the order's waiting list
    (a cancel no longer waits for it); the units it put in stock still count
    as arrived.

    Only a PENDING or held receipt: an accepted one is corrected through a
    vendor return and a void one is gone. And never while it is being
    accepted -- escalate takes the SAME claim accept and void take. Without
    it, escalating a receipt mid-accept dropped it from the receipts another
    accept counts whole (the two-accepts guard), so both minted; and an
    escalation landing between an accept's claim and its first unit put a
    whole delivery behind an ESCALATED receipt. Store-scoped like accept and
    void (another store's receipt reads as 404)."""
    grn_repo = get_grn_repository()
    if grn_repo is None:
        return {"message": "GRN escalated to HQ", "grn_id": grn_id}

    grn = grn_repo.find_by_id(grn_id)
    if not grn or not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")
    if grn.get("status") not in _VOIDABLE:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a pending or held goods receipt can be escalated. This "
                f"one is {grn.get('status')}."
            ),
        )
    claim_token = _claim_grn_for_accept(grn_repo, grn_id, current_user.get("user_id"))
    if claim_token is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This goods receipt is being accepted right now, so it cannot "
                "be escalated. Wait for that to finish, then refresh."
            ),
        )
    try:
        _write_under_claim(
            grn_repo,
            grn,
            claim_token,
            {
                "status": "ESCALATED",
                "escalated_at": datetime.now().isoformat(),
                "escalated_by": current_user.get("user_id"),
                "escalation_note": note,
            },
            "escalated",
        )
    finally:
        _release_grn_accept_claim(grn_repo, grn_id, claim_token)

    return {"message": "GRN escalated to HQ", "grn_id": grn_id}


@router.post("/grn/{grn_id}/drop-over-order")
async def drop_over_order(
    grn_id: str, current_user: dict = Depends(require_roles(*_RECEIVE_ROLES))
):
    """'Not received' for the units a receipt holds BEYOND its order (the
    catalogue release's order cap, reason over_order): the vendor did not send
    them -- a second receipt of the same box, say. The way out for a held
    receipt that already put other lines on the shelf, which a void refuses
    (R1-13); "Add to stock" is the other answer, when the extra units really
    came.

    Only what is beyond the order is dropped: each such line keeps the units
    it put on the shelf (by their origin, _receipt_units) plus what its order
    still has room for (ordered less what every receipt of the order put on
    the shelf, po_detail._received_by_product); the rest leaves its accepted and received
    counts, recorded as dropped_qty. Kept units not yet on the shelf go there
    through THE one path (_put_on_shelf, order cap on) once this claim is
    handed back. Lines held for the catalogue stay held. A receipt left with
    nothing held and nothing accepted put nothing on the shelf: it is VOID.
    The PO's received counts are refreshed, the store manager's over-order
    tasks close when nothing is beyond the order any more, and an audit row
    records who said so. Under the accept claim, so no accept runs meanwhile;
    every read fails closed. Receiving is managers only (owner 2026-09-28):
    the route's gate."""
    from .grn_accept import (
        _complete_receipt_tasks,
        _put_on_shelf,
        refresh_po_received,
        release_finished_meanwhile,
    )
    from .po_detail import _order_or_503, _qty, _received_by_product
    from ...services.po_variance_engine import _ordered_by_product
    from ._shared import _get_db

    grn_repo = get_grn_repository()
    if grn_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    grn = grn_repo.find_by_id(grn_id)
    if not grn or not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")

    def _over(g):
        if g.get("status") != "PARTIALLY_ACCEPTED":
            return set()
        return {
            ln.get("product_id")
            for ln in g.get("unresolved_lines") or []
            if ln.get("reason") == "over_order"
        }

    nothing_over = HTTPException(
        status_code=400,
        detail="Nothing on this receipt is held beyond its order.",
    )
    if not _over(grn):
        raise nothing_over
    stock_repo = get_stock_repository()
    po_repo = get_purchase_order_repository()
    po_id = grn.get("po_id")
    unreadable = HTTPException(
        status_code=503,
        detail="Could not count what this receipt and its order put on the shelf. Try again.",
    )
    if stock_repo is None or po_repo is None or not po_id:
        raise unreadable
    claim_token = _claim_grn_for_accept(grn_repo, grn_id, current_user.get("user_id"))
    if claim_token is None:
        raise HTTPException(
            status_code=409,
            detail="This receipt is being accepted right now. Refresh in a moment.",
        )
    try:
        # Decided from the receipt as it stands under the claim: the read
        # above predates it, and a catalogue save or an accept may have
        # changed what is held since (panel P2).
        grn = _claimed_receipt(grn_repo, grn_id, claim_token)
        held = grn.get("unresolved_lines") or []
        over = _over(grn)
        if not over:
            raise nothing_over
        try:
            po = _order_or_503(po_repo, po_id)
            if not po:
                raise unreadable
            ordered = _ordered_by_product(po)
            others = _received_by_product(po, leave_out_grn=grn_id)
            items, still_over, dropped, to_shelve, room = [], [], 0, 0, {}
            for idx, it in enumerate(grn.get("items") or []):
                pid = it.get("product_id")
                accepted = int(it.get("accepted_qty") or 0)
                if pid not in over or accepted <= 0:
                    items.append(it)
                    continue
                on_line = _receipt_units(
                    stock_repo, grn, product_id=pid, grn_line_index=idx
                )
                if pid not in room:
                    room[pid] = max(
                        0,
                        ordered.get(pid, 0)
                        - _qty(others.get(pid))
                        - _receipt_units(stock_repo, grn, product_id=pid),
                    )
                take = min(max(0, accepted - on_line), room[pid])
                room[pid] -= take
                drop = max(0, accepted - on_line - take)
                dropped += drop
                to_shelve += take
                if take:
                    still_over.append(
                        {
                            "product_id": pid,
                            "accepted_qty": on_line + take,
                            "held_qty": take,
                            "reason": "over_order",
                        }
                    )
                items.append(
                    {
                        **it,
                        "accepted_qty": accepted - drop,
                        "received_qty": max(0, int(it.get("received_qty") or 0) - drop),
                        "dropped_qty": int(it.get("dropped_qty") or 0) + drop,
                    }
                )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed
            raise unreadable from exc
        rest = [ln for ln in held if ln.get("reason") != "over_order"] + still_over
        # Nothing held and nothing kept: the receipt put nothing on the shelf
        # (a second count of a box, say) -- VOID, as a void leaves it, never
        # an ACCEPTED that reads "On shelf" for 0 units.
        kept = any(int(it.get("accepted_qty") or 0) > 0 for it in items)
        status = "PARTIALLY_ACCEPTED" if rest else ("ACCEPTED" if kept else "VOID")
        patch = {
            "items": items,
            # The header totals (vendor performance reads them) follow the lines.
            "total_received": max(0, int(grn.get("total_received") or 0) - dropped),
            "total_accepted": max(0, int(grn.get("total_accepted") or 0) - dropped),
            "unresolved_lines": rest,
            "status": status,
            "over_order_dropped_by": current_user.get("user_id"),
            "over_order_dropped_at": datetime.now().isoformat(),
        }
        if status == "VOID":
            patch.update(voided_at=patch["over_order_dropped_at"], voided_by=current_user.get("user_id"))
        # Under the claim, guarded on the status read under it (the void's
        # and the escalation's one write).
        _write_under_claim(grn_repo, grn, claim_token, patch, "marked not received")
        po_status = refresh_po_received(po_repo, po_id, grn_id)[0]
        if status == "VOID":
            # Voided as a void leaves it: nothing received, the order reopens.
            po_status = _reopen_po_if_nothing_received(grn, current_user) or po_status
        if not still_over:
            try:
                _db = _get_db()
                if _db is not None:
                    _complete_receipt_tasks(
                        _db,
                        grn_id,
                        "The units held beyond the order were not received.",
                        category="Purchase",
                    )
            except Exception:  # noqa: BLE001 - a task problem never undoes the drop
                logger.warning("[VENDOR] GRN %s: over-order tasks not closed", grn_id, exc_info=True)
        try:
            audit = get_audit_repository()
            if audit is not None:
                audit.create(
                    {
                        "action": "grn.over_order_dropped",
                        "entity_type": "grn",
                        "entity_id": grn_id,
                        "user_id": current_user.get("user_id"),
                        "detail": {
                            "grn_number": grn.get("grn_number"),
                            "po_id": po_id,
                            "dropped_units": dropped,
                            "kept_units": to_shelve,
                            "products": sorted(str(p) for p in over),
                        },
                    }
                )
        except Exception:  # noqa: BLE001 - the audit never undoes the drop
            logger.warning("[VENDOR] GRN %s: drop audit failed", grn_id, exc_info=True)
    except BaseException:
        _hand_back(grn_repo, grn_id, claim_token)
        raise
    _release_grn_accept_claim(grn_repo, grn_id, claim_token)
    out = {
        "message": f"{dropped} unit(s) held beyond the order were marked not received",
        "grn_id": grn_id,
        "grn_status": status,
        "dropped_units": dropped,
        "units_added": 0,
        "po_status": po_status,
        "unresolved_lines": rest,
    }
    if to_shelve:
        # The units within the order the vendor did send go on the shelf now,
        # through the one path, with the order cap still on.
        try:
            shelved = _put_on_shelf(
                grn_repo, grn_id, grn_repo.find_by_id(grn_id), current_user, order_cap=True
            )
            out.update(
                grn_status=shelved.get("grn_status"),
                units_added=shelved.get("units_added") or 0,
                po_status=shelved.get("po_status"),
                unresolved_lines=shelved.get("unresolved_lines") or [],
            )
        except Exception:  # noqa: BLE001 - the drop stands; 'Add to stock' shelves them
            logger.error(
                "[VENDOR] GRN %s: the units kept within the order were not shelved",
                grn_id,
                exc_info=True,
            )
    # After the shelving above (it runs the same check itself when it shelves):
    # a catalogue save that lost its release to this claim is released now.
    release_finished_meanwhile(grn_repo, grn_id)
    return out
