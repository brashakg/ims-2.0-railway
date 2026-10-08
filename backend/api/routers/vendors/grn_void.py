"""GRN void and escalate."""

from ._shared import (
    Depends,
    HTTPException,
    Query,
    _VENDOR_ROLES,
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
from .grn_accept_lock import (
    _GRN_TERMINAL_ACCEPT_STATUSES,
    _GRN_WRITE_ERROR,
    _claim_grn_for_accept,
    _grn_already_minted,
    _guarded_grn_write,
    _received_on,
    _release_grn_accept_claim,
)


def _reopen_po_if_nothing_received(grn_repo, grn, current_user=None) -> None:
    """A held receipt's accept moved its PO to PARTIALLY_RECEIVED with nothing
    on the shelf. Voided, and with no other live receipt on the order and
    nothing counted received, nothing was received: the order goes back to
    SENT, so it reads true and can be cancelled again (cancel refuses a
    part-received order) -- the way out for a draft whose box went back (a
    draft an open order still expects is never discarded, catalog DELETE).

    One GUARDED write: it lands only while the order is still part-received
    with nothing counted received, so an accept of another receipt that
    commits in between (it writes the received counts) is never undone.
    Fail-soft: the void stands."""
    po_id = (grn or {}).get("po_id")
    if not po_id:
        return
    try:
        po_repo = get_purchase_order_repository()
        if po_repo is None:
            return
        po = po_repo.find_by_id(po_id)
        if not po or po.get("status") not in ("PARTIALLY_RECEIVED", "PARTIAL"):
            return
        if grn_repo.find_many({"po_id": po_id, "status": {"$ne": "VOID"}}):
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
        if grn_repo.find_many({"po_id": po_id, "status": {"$ne": "VOID"}}):
            po_repo.collection.update_one(
                {"po_id": po_id, "status": "SENT"},
                {"$set": {"status": po.get("status"), "updated_at": datetime.now()}},
            )
            return
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


@router.post("/grn/{grn_id}/void")
async def void_grn(
    grn_id: str, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Void a goods-receipt note that never put stock on the shelf
    (duplicate/mistake cleanup).

    Two gates, and the second one matters more than it looks. The status gate
    is the bookkeeping one: an ACCEPTED GRN has minted stock_units and must be
    corrected through a vendor return; a PENDING one normally has not, nor has
    a PARTIALLY_ACCEPTED one whose every line was held for the catalogue (a
    second receipt of the same box, say). But the status does NOT imply "no
    stock": the accept flow flips the status only
    AFTER the mint loop, so a worker killed mid-accept leaves the receipt
    PENDING with real units already on the shelf. Voiding THAT orphans those
    units (PO receipt math reads only the accepted lines of a live receipt) and licenses a full re-mint
    under a new grn_id -- which the per-(grn, line, unit) unique index cannot
    catch, because it keys on source_id. So voiding is refused whenever
    stock_units already holds a row for this receipt, and the operator is told
    to accept it again instead (that retry is idempotent).

    Store-scoped like accept (cross-store reads as 404). The receipt row is kept
    (audit trail, numbering continuity) with status VOID -- the accept endpoint
    refuses VOID rows, and PO receipt math never counts a VOID one.
    """
    grn_repo = get_grn_repository()
    if grn_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    grn = grn_repo.find_by_id(grn_id)
    if not grn:
        raise HTTPException(status_code=404, detail="GRN not found")
    if not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")
    # A PARTIALLY_ACCEPTED receipt whose every line was HELD (product not
    # catalogued yet) put nothing on the shelf either -- and a second receipt of
    # the same box is exactly that. The stock gate below is what proves "nothing
    # on the shelf" for both; for a held receipt it must be able to look.
    status = grn.get("status")
    if status not in ("PENDING", "PARTIALLY_ACCEPTED"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a receipt that put nothing on the shelf can be voided. This "
                f"one is {status} -- accepted stock must be corrected via a "
                "vendor return."
            ),
        )

    # MUTUAL EXCLUSION WITH ACCEPT. Void takes the SAME guarded claim the accept
    # path uses, so the two can never interleave. A point-in-time stock gate is
    # not enough on its own: a void landing in the window between an accept's
    # claim and its FIRST mint -- a window spanning the PO fetch, the product
    # lookup, the cost backfill write and the already-minted count -- passes
    # every gate here while the accept is still about to mint. The accept then
    # puts the whole delivery onto a VOID receipt: real sellable units behind a
    # voided doc, PO receipt math permanently short (it never counts a VOID receipt),
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
        if stock_repo is None and status != "PENDING":
            raise HTTPException(
                status_code=503,
                detail=(
                    "Could not check whether this goods receipt has already put "
                    "stock on the shelf, so it was not voided. Try again in a "
                    "moment."
                ),
            )
        if stock_repo is not None:
            try:
                # By ORIGIN: a unit transferred to another shop since is still
                # this receipt's stock.
                already_minted = _grn_already_minted(stock_repo, _received_on(grn))
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
            if already_minted > 0 and status != "PENDING":
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

        # TERMINAL WRITE -- guarded exactly like the accept path's, because
        # holding the claim is NOT the same as writing under it. Each element
        # closes a DIFFERENT measured defect; the attribution below was checked
        # by mutation (remove one filter, see which probe reopens), because an
        # earlier version of this comment credited the wrong filter and would
        # have led the next reader to delete the one that is doing real work.
        #
        #   * status (as read above) -- carries BOTH the "no stall, two clerks" shape
        #     and the parked-count shape. The PENDING assertion above reads the
        #     doc fetched BEFORE the claim, and the claim itself admits
        #     PARTIALLY_ACCEPTED, so without this filter a colleague's accept
        #     landing in between voids a receipt that now holds stock. Measured
        #     with the token filter REMOVED: both of those still 409 here,
        #     because by then the doc is ACCEPTED / PARTIALLY_ACCEPTED and no
        #     longer matches.
        #
        #   * accept_lock_token -- its UNIQUE job is the shape where the doc is
        #     still PENDING when the parked void wakes up, so the status filter
        #     cannot help: an accept takes the stale claim over, mints every
        #     unit, and its terminal flip CANNOT be written (see
        #     _advance_grn_terminal_status -- "the receipt stays in its previous
        #     status"), then _finalise_grn_accept_metadata clears the token.
        #     Doc PENDING, stock on the shelf, token gone. Measured with this
        #     filter removed: 200 {"grn_status": "VOID"} over 24 real units.
        #     Regression-tested by
        #     test_a_parked_void_cannot_void_a_receipt_whose_flip_failed.
        #
        # And branching on the RESULT is what stops a swallowed write answering
        # a green "GRN voided" over a doc that is still PENDING.
        void_patch = {
            "status": "VOID",
            "voided_at": datetime.now().isoformat(),
            "voided_by": current_user.get("user_id"),
        }
        written = _guarded_grn_write(
            grn_repo,
            {
                "grn_id": grn_id,
                "status": status,
                "accept_lock_token": claim_token,
            },
            {"$set": void_patch},
        )
        if written is _GRN_WRITE_ERROR:
            # Deliberately does NOT claim "nothing was voided": the write may
            # have applied server-side and only its reply been lost, in which
            # case the receipt IS void. The stock gate above already proved zero
            # units, so no stock is at risk either way -- but the message must
            # not assert an outcome we cannot see.
            raise HTTPException(
                status_code=503,
                detail=(
                    "The database did not confirm whether this goods receipt "
                    "was voided. Refresh to see its current state before trying "
                    "again -- no stock was affected."
                ),
            )
        if written is None:
            # Minimal mock with no atomic primitive: plain write, but still
            # check it landed rather than assuming it did.
            if not grn_repo.update(grn_id, void_patch):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The goods receipt could not be voided. Refresh and try "
                        "again; nothing was voided."
                    ),
                )
        elif not written:
            try:
                current = grn_repo.find_by_id(grn_id)
            except Exception:  # noqa: BLE001
                current = None
            status_now = (current or {}).get("status") or "unknown"
            logger.error(
                "[VENDOR] GRN %s: void did NOT apply -- the receipt is now %s "
                "(it changed while this void was in flight)",
                grn_id,
                status_now,
            )
            if status_now in _GRN_TERMINAL_ACCEPT_STATUSES:
                detail = (
                    f"This goods receipt is now {status_now} and holds stock, "
                    "so it cannot be voided -- accepted stock must be corrected "
                    "via a vendor return. Refresh to see its current state."
                )
            else:
                detail = (
                    "This goods receipt changed while it was being voided (it "
                    f"is now {status_now}), so nothing was voided. Refresh and "
                    "check its current state before trying again."
                )
            raise HTTPException(status_code=409, detail=detail)

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

        _reopen_po_if_nothing_received(grn_repo, grn, current_user)

        return {
            "message": "GRN voided",
            "grn_id": grn_id,
            "grn_number": grn.get("grn_number"),
            "grn_status": "VOID",
        }
    finally:
        # VOID is terminal, so the claim is always handed back -- on the happy
        # path and on every refusal above.
        _release_grn_accept_claim(grn_repo, grn_id, claim_token)


@router.post("/grn/{grn_id}/escalate")
async def escalate_grn(
    grn_id: str,
    note: str = Query(...),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Escalate GRN to HQ for review"""
    grn_repo = get_grn_repository()

    if grn_repo is not None:
        grn = grn_repo.find_by_id(grn_id)
        if not grn:
            raise HTTPException(status_code=404, detail="GRN not found")

        grn_repo.update(
            grn_id,
            {
                "status": "ESCALATED",
                "escalated_at": datetime.now().isoformat(),
                "escalated_by": current_user.get("user_id"),
                "escalation_note": note,
            },
        )

    return {"message": "GRN escalated to HQ", "grn_id": grn_id}


# The receiving MANAGERS (owner 2026-09-28: receiving is managers only) -- and
# the over-order task is the shop's store manager's (2026-09-29).
_DROP_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")


@router.post("/grn/{grn_id}/drop-over-order")
async def drop_over_order(
    grn_id: str, current_user: dict = Depends(require_roles(*_DROP_ROLES))
):
    """'Not received' for the units a receipt holds BEYOND its order (the
    catalogue release's order cap, reason over_order): the vendor did not send
    them -- a second receipt of the same box, say. The way out for a held
    receipt that already put other lines on the shelf, which a void refuses
    (R1-13); "Add to stock" is the other answer, when the extra units really
    came.

    Only what is beyond the order is dropped: each such line keeps the units
    it put on the shelf (by their origin, _received_on) plus what its order
    still has room for (ordered less every unit the order already put on the
    shelf, _po_units_received); the rest leaves its accepted and received
    counts, recorded as dropped_qty. Kept units not yet on the shelf go there
    through THE one path (_put_on_shelf, order cap on) once this claim is
    handed back. Lines held for the catalogue stay held. The PO's received
    counts are refreshed, the store manager's over-order tasks close when
    nothing is beyond the order any more, and an audit row records who said
    so. Under the accept claim, so no accept runs meanwhile; every read fails
    closed."""
    from .grn_accept import (
        _complete_receipt_tasks,
        _ordered_by_product,
        _po_units_received,
        _put_on_shelf,
        refresh_po_received,
    )
    from ._shared import _get_db

    grn_repo = get_grn_repository()
    if grn_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    grn = grn_repo.find_by_id(grn_id)
    if not grn or not can_access_store_scoped(grn.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="GRN not found")
    held = grn.get("unresolved_lines") or []
    over = {ln.get("product_id") for ln in held if ln.get("reason") == "over_order"}
    if grn.get("status") != "PARTIALLY_ACCEPTED" or not over:
        raise HTTPException(
            status_code=400,
            detail="Nothing on this receipt is held beyond its order.",
        )
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
        try:
            ordered = _ordered_by_product(po_repo, po_id)
            items, still_over, dropped, to_shelve, room = [], [], 0, 0, {}
            for idx, it in enumerate(grn.get("items") or []):
                pid = it.get("product_id")
                accepted = int(it.get("accepted_qty") or 0)
                if pid not in over or accepted <= 0:
                    items.append(it)
                    continue
                on_line = _grn_already_minted(
                    stock_repo, _received_on(grn, product_id=pid, grn_line_index=idx)
                )
                if pid not in room:
                    room[pid] = max(
                        0, ordered.get(pid, 0) - _po_units_received(stock_repo, po_id, pid)
                    )
                take = min(max(0, accepted - on_line), room[pid])
                room[pid] -= take
                drop = max(0, accepted - on_line - take)
                dropped += drop
                to_shelve += take
                if take:
                    still_over.append(
                        {"product_id": pid, "accepted_qty": on_line + take, "reason": "over_order"}
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
        status = "PARTIALLY_ACCEPTED" if rest else "ACCEPTED"
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
        written = _guarded_grn_write(
            grn_repo,
            {"grn_id": grn_id, "status": "PARTIALLY_ACCEPTED", "accept_lock_token": claim_token},
            {"$set": patch},
        )
        if written is None:
            written = bool(grn_repo.update(grn_id, patch))
        if written is not True:
            raise HTTPException(
                status_code=503 if written is _GRN_WRITE_ERROR else 409,
                detail="The receipt changed while this ran. Refresh and check it before trying again.",
            )
        po_status = refresh_po_received(po_repo, grn_repo, po_id)
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
    finally:
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
    return out
