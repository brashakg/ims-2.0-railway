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
    _release_grn_accept_claim,
)


@router.post("/grn/{grn_id}/void")
async def void_grn(
    grn_id: str, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Void a goods-receipt note that never put stock on the shelf
    (duplicate/mistake cleanup).

    Two gates, and the second one matters more than it looks. PENDING-only is
    the bookkeeping gate: an ACCEPTED / PARTIALLY_ACCEPTED GRN has already
    minted stock_units and must be corrected through a vendor return. But
    PENDING does NOT imply "no stock": the accept flow flips the status only
    AFTER the mint loop, so a worker killed mid-accept leaves the receipt
    PENDING with real units already on the shelf. Voiding THAT orphans those
    units (PO receipt math only sums ACCEPTED GRNs) and licenses a full re-mint
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
    if grn.get("status") != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a PENDING GRN can be voided. This one is "
                f"{grn.get('status')} -- accepted stock must be corrected via a "
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
        if stock_repo is not None:
            try:
                already_minted = _grn_already_minted(
                    stock_repo, {"source_type": "GRN", "source_id": grn_id}
                )
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
        #   * status PENDING -- carries BOTH the "no stall, two clerks" shape
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
                "status": "PENDING",
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
